"""SQLite-backed local work library for S / ComfyBatch.

Why a database instead of "just read the JSON"
---------------------------------------------
Everything about a generated image already exists, but it is spread across one
JSON document per run (``<数据目录>/reviews/<run_id>.json``) plus the files
themselves. That shape is fine for *one* batch and useless for the question the
user actually asks: "which of my images used this style / this seed / this
model, and where are the ones I never finished reviewing?"

This module turns that per-run pile into a single queryable index:

* **One row per batch item**, keyed ``<run_id>:<index>`` -- stable across
  re-syncs, redo attempts and restarts, so tags and favourites stick to the
  right image.
* **Derived, never authoritative.** Rows are produced by *reading* the review
  documents; nothing here invents image metadata. Anything the library disagrees
  about can be rebuilt from the sources with :meth:`LibraryStore.sync_all`.
* **User-owned data lives only here.** Favourites, tags and deletions are the
  only fields the sync is not allowed to overwrite, because they are not
  derivable from anything else.

Deletion is a **tombstone inside the row** (``deleted_at``), not a ``DELETE``.
The same discipline the settings store already uses: re-reading the source must
never put a removed record back, and the user must be able to undo it.

The database is a single file. It is opened per operation rather than kept open,
because the HTTP server is threaded and a shared connection would need locking
around every statement for no measurable gain at this scale.
"""

from __future__ import annotations

import contextlib
import json
import pathlib
import sqlite3
import time
from typing import Any, Iterable, Iterator

try:  # Pillow ships with the program; the library still works without it.
    from PIL import Image
except Exception:  # noqa: BLE001 - a missing Pillow only costs image dimensions
    Image = None

#: Bumped when the table layout changes; ``meta`` records what a file is.
SCHEMA_VERSION = 1

#: How long SQLite waits for a lock another process holds before reporting the
#: database busy. Several S processes may now have the same library open, and a
#: writer can legitimately hold the lock for as long as one batch sync takes.
SQLITE_BUSY_TIMEOUT_SECONDS = 30.0

#: 每个 SQLite 文件开头的 16 个字节。用来区分"这不是数据库"和"数据库暂时打不开"。
SQLITE_MAGIC = b"SQLite format 3\x00"

#: Review states the rest of the program already uses. Imported nowhere on
#: purpose -- kept as a literal so this module stays free of ``comfybatch_v2_core``
#: and can be tested (and reused) on its own.
REVIEW_STATUSES = ("待确认", "已通过", "需重做", "已替换")

#: Sort keys accepted by :meth:`LibraryStore.query`, mapped to SQL order clauses.
#: A whitelist, not an f-string: the value arrives from the query string.
#:
#: ``newest``/``oldest`` order by the **image's own** mtime and put records whose
#: file is gone last. Sorting on the time the record entered the index looked
#: equivalent and was not: a batch of failures has no files, so every one of them
#: got the "just indexed" timestamp and jumped above the works the user actually
#: has. Real data is what exposed it.
SORTS: dict[str, str] = {
    "newest": "(mtime_ns > 0) DESC, mtime_ns DESC, run_id DESC, item_index DESC",
    "oldest": "(mtime_ns > 0) DESC, mtime_ns ASC, run_id ASC, item_index ASC",
    "index": "run_id ASC, item_index ASC",
    "seed": "CAST(seed AS INTEGER) ASC, run_id ASC, item_index ASC",
    "size": "size_bytes DESC, run_id DESC, item_index DESC",
}

#: Human labels for the sort keys.
#:
#: The page used to receive the SQL clause as the option text, so the sort
#: dropdown literally read ``(mtime_ns > 0) DESC, mtime_ns DESC, ...``. The
#: clause is an implementation detail; the label is the interface.
SORT_LABELS: dict[str, str] = {
    "newest": "最新",
    "oldest": "最早",
    "index": "按批次编号",
    "seed": "按种子",
    "size": "按文件大小",
}

DEFAULT_PAGE_SIZE = 24
MAX_PAGE_SIZE = 200

#: Columns the sync may overwrite on every pass. Everything *not* listed here
#: (``work_id``, ``favorite``, ``deleted_at``) belongs to the user or to identity.
_SYNCED_COLUMNS = (
    "run_id", "item_index", "title", "preset_name", "status", "review_status", "note",
    "prompt", "negative_prompt", "compiled_prompt", "seed", "seeds_json", "model",
    "workflow_path", "workflow_variant", "output_dir", "copied_to", "size_bytes",
    "width", "height", "mtime_ns", "attempt_count", "elapsed", "error", "history_json",
    "indexed_at", "updated_at",
)

#: Written once, on insert: re-reading the source must not make an old work look new.
_INSERT_ONLY_COLUMNS = ("indexed_at",)


def work_id_for(run_id: str, index: Any) -> str:
    """Stable identity for one batch item.

    Deliberately ``run + index`` and **not** the file path: a redo replaces the
    file on disk, and the user's tags must survive that. The index is padded so
    the id sorts naturally and cannot be confused with a neighbouring value.
    """
    try:
        position = int(index)
    except (TypeError, ValueError):
        position = 0
    return f"{str(run_id).strip()}:{position:04d}"


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _seed_of(entry: dict[str, Any]) -> str:
    """The submitted seed of the latest attempt, as a string.

    ``generation.seeds`` maps node id -> seed and can hold several entries when a
    workflow has more than one sampler; the first (lowest node id) is the one a
    user means by "the seed" and the one the review card shows first.
    """
    seeds = (entry.get("generation") or {}).get("seeds") or {}
    if isinstance(seeds, dict) and seeds:
        for key in sorted(seeds, key=lambda item: (len(str(item)), str(item))):
            return _safe_text(seeds[key])
    return _safe_text(entry.get("seed"))


def _like_pattern(text: str) -> str:
    """Escape ``%``/``_`` so a search for "50%" is a search, not a wildcard."""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


class LibraryUnavailable(RuntimeError):
    """The database could not be opened *and* could not be replaced.

    The library is a derived index, not the user's work. When it cannot be
    opened at all the program still has to start and say so -- refusing to load
    the review page because an index is missing would be the tail wagging the
    dog.
    """


class LibraryStore:
    """One SQLite file holding every generated image the program knows about."""

    def __init__(self, path: pathlib.Path | str) -> None:
        self.path = pathlib.Path(path)
        self.last_warning = ""
        #: False once the file can neither be opened nor replaced. Every
        #: operation then raises :class:`LibraryUnavailable` instead of the
        #: program failing to start.
        self.available = True
        self._prepare()

    # ------------------------------------------------------------- schema

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=SQLITE_BUSY_TIMEOUT_SECONDS)
        connection.row_factory = sqlite3.Row
        # Two processes may now have this database open, so ask SQLite for the
        # behaviour that makes that work instead of relying on the connect timeout:
        # WAL lets a reader and a writer proceed together (the default rollback
        # journal blocks one against the other, which turns a long batch sync into a
        # wall of "database is locked"), and an explicit busy timeout turns a
        # momentary lock into a short wait instead of an immediate error.
        try:
            connection.execute("PRAGMA busy_timeout = %d" % int(SQLITE_BUSY_TIMEOUT_SECONDS * 1000))
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
        except sqlite3.Error:
            # A read-only or unusual location still works; these pragmas are a
            # concurrency optimisation, not a correctness requirement.
            pass
        return connection

    @contextlib.contextmanager
    def _session(self) -> Iterator[sqlite3.Connection]:
        """One connection per operation, committed and **closed**.

        ``with sqlite3.connect(...)`` is a transaction context, not a resource
        one: it commits and leaves the connection open. On Windows that keeps a
        lock on the database file, which broke both ``VACUUM`` and cleaning up a
        temporary directory in the tests -- so every call site goes through here.
        """
        if not self.available:
            raise LibraryUnavailable(self.last_warning or "作品库当前不可用")
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _prepare(self) -> None:
        """Create the file and its tables, recovering from a damaged file.

        A half-written or truncated database used to be a hard stop for anything
        that touched it. Renaming the bad file aside and starting a fresh one
        keeps the program usable, and the old bytes are kept so nothing is
        silently destroyed -- the library is a rebuildable index, so losing the
        index is recoverable, losing the user's *sources* would not be.
        """
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.available = False
            self.last_warning = f"作品库目录无法使用，作品库暂不可用：{exc}"
            return
        try:
            with self._session() as connection:
                self._ensure_schema(connection)
            return
        except (sqlite3.Error, OSError) as exc:
            self.last_warning = f"作品库文件无法读取，已另存为备份并新建：{exc}"
        if not self._is_foreign_file():
            # 打开失败了，但文件本身还带着 SQLite 文件头——多半是占锁、瞬时 I/O
            # 错误，或者"能读不能写"。把它挪走等于让用户丢掉全部标签与收藏，
            # 代价远高于"这次不可用，下次再试"。
            self.available = False
            self.last_warning = f"{self.last_warning}（文件看起来仍是数据库，未另存，请稍后重试）"
            return
        stamp = time.strftime("%Y%m%d-%H%M%S")
        try:
            self.path.replace(self.path.with_name(f"{self.path.name}.corrupt-{stamp}"))
        except OSError as exc:
            self.last_warning = f"{self.last_warning}（旧文件没能挪走：{exc}）"
        try:
            with self._session() as connection:
                self._ensure_schema(connection)
        except (sqlite3.Error, OSError, LibraryUnavailable) as exc:
            # Second failure -- a read-only directory, another process holding
            # the file, a path whose parent is not a directory. Give up on the
            # index rather than taking the whole program down with it.
            self.available = False
            self.last_warning = f"{self.last_warning}（作品库暂不可用：{exc}）"

    def _is_foreign_file(self) -> bool:
        """文件存在、且**不是**一个 SQLite 数据库。

        空文件（0 字节）算数据库：SQLite 会把空文件当合法的新库。读不到也不下结论。
        """
        try:
            with self.path.open("rb") as handle:
                header = handle.read(len(SQLITE_MAGIC))
        except OSError:
            return False
        return bool(header) and not header.startswith(SQLITE_MAGIC)

    @staticmethod
    def _ensure_schema(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS works (
                work_id           TEXT PRIMARY KEY,
                run_id            TEXT NOT NULL,
                item_index        INTEGER NOT NULL,
                title             TEXT NOT NULL DEFAULT '',
                preset_name       TEXT NOT NULL DEFAULT '',
                status            TEXT NOT NULL DEFAULT '',
                review_status     TEXT NOT NULL DEFAULT '待确认',
                note              TEXT NOT NULL DEFAULT '',
                prompt            TEXT NOT NULL DEFAULT '',
                negative_prompt   TEXT NOT NULL DEFAULT '',
                compiled_prompt   TEXT NOT NULL DEFAULT '',
                seed              TEXT NOT NULL DEFAULT '',
                seeds_json        TEXT NOT NULL DEFAULT '{}',
                model             TEXT NOT NULL DEFAULT '',
                workflow_path     TEXT NOT NULL DEFAULT '',
                workflow_variant  TEXT NOT NULL DEFAULT '',
                output_dir        TEXT NOT NULL DEFAULT '',
                copied_to         TEXT NOT NULL DEFAULT '',
                size_bytes        INTEGER NOT NULL DEFAULT 0,
                width             INTEGER NOT NULL DEFAULT 0,
                height            INTEGER NOT NULL DEFAULT 0,
                mtime_ns          INTEGER NOT NULL DEFAULT 0,
                attempt_count     INTEGER NOT NULL DEFAULT 0,
                elapsed           REAL,
                error             TEXT NOT NULL DEFAULT '',
                history_json      TEXT NOT NULL DEFAULT '[]',
                favorite          INTEGER NOT NULL DEFAULT 0,
                deleted_at        REAL NOT NULL DEFAULT 0,
                indexed_at        REAL NOT NULL,
                updated_at        REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS works_run_index ON works(run_id, item_index);
            CREATE INDEX IF NOT EXISTS works_review_status ON works(review_status);
            CREATE INDEX IF NOT EXISTS works_favorite ON works(favorite);
            CREATE INDEX IF NOT EXISTS works_deleted ON works(deleted_at);
            CREATE TABLE IF NOT EXISTS work_tags (
                work_id    TEXT NOT NULL,
                tag        TEXT NOT NULL,
                created_at REAL NOT NULL,
                PRIMARY KEY (work_id, tag)
            );
            CREATE INDEX IF NOT EXISTS work_tags_tag ON work_tags(tag);
            CREATE TABLE IF NOT EXISTS runs (
                run_id        TEXT PRIMARY KEY,
                output_dir    TEXT NOT NULL DEFAULT '',
                total         INTEGER NOT NULL DEFAULT 0,
                completed     INTEGER NOT NULL DEFAULT 0,
                errors        INTEGER NOT NULL DEFAULT 0,
                indexed_at    REAL NOT NULL,
                updated_at    REAL NOT NULL
            );
            """
        )
        connection.execute(
            "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(SCHEMA_VERSION),),
        )

    # -------------------------------------------------------------- sync

    def sync_run(self, run_id: str, document: dict[str, Any]) -> dict[str, int]:
        """Upsert one run's review document into the index.

        Idempotent by design: it runs on every poll of the results endpoint, so
        re-ingesting the same document must change nothing. Only the derived
        columns are written -- ``favorite``, ``deleted_at`` and the tags are the
        user's, and a re-sync that cleared them would be indistinguishable from
        data loss.
        """
        run_id = str(run_id or "").strip()
        items = (document or {}).get("items") or {}
        if not run_id or not isinstance(items, dict):
            return {"inserted": 0, "updated": 0, "skipped": 0}
        now = time.time()
        counters = {"inserted": 0, "updated": 0, "skipped": 0}
        with self._session() as connection:
            for key, entry in items.items():
                if not isinstance(entry, dict):
                    counters["skipped"] += 1
                    continue
                index = entry.get("index", key)
                # A non-numeric index has no stable identity: ``work_id_for``
                # would fold every such entry onto ``...:0000`` and the last one
                # would silently overwrite the others. Skip rather than guess.
                if not _is_index(index):
                    counters["skipped"] += 1
                    continue
                work_id = work_id_for(run_id, index)
                row = self._entry_to_row(run_id, index, entry, document, now)
                # The whitelist is enforced here rather than trusted to
                # ``_entry_to_row``: a column added to that helper by mistake must
                # not become a way for a re-sync to clear a favourite or undo a
                # deletion.
                row = {column: value for column, value in row.items() if column in _SYNCED_COLUMNS}
                existing = connection.execute(
                    "SELECT favorite, deleted_at FROM works WHERE work_id = ?", (work_id,)
                ).fetchone()
                row["work_id"] = work_id
                if existing is None:
                    columns = ", ".join(sorted(row))
                    placeholders = ", ".join("?" for _ in row)
                    connection.execute(
                        f"INSERT INTO works ({columns}) VALUES ({placeholders})",
                        [row[column] for column in sorted(row)],
                    )
                    counters["inserted"] += 1
                else:
                    # ``favorite`` / ``deleted_at`` are intentionally absent from
                    # ``_SYNCED_COLUMNS`` and therefore absent from ``row``.
                    # ``indexed_at`` is excluded too: it records when the work
                    # first entered the library, so a later sync must not move it.
                    assigned = [column for column in sorted(row) if column not in ("work_id",) + _INSERT_ONLY_COLUMNS]
                    assignments = ", ".join(f"{column} = ?" for column in assigned)
                    connection.execute(
                        f"UPDATE works SET {assignments} WHERE work_id = ?",
                        [row[column] for column in assigned] + [work_id],
                    )
                    counters["updated"] += 1
            totals = self._run_totals(items)
            connection.execute(
                """
                INSERT INTO runs(run_id, output_dir, total, completed, errors, indexed_at, updated_at)
                VALUES(?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    output_dir = excluded.output_dir,
                    total = excluded.total,
                    completed = excluded.completed,
                    errors = excluded.errors,
                    updated_at = excluded.updated_at
                """,
                (
                    run_id,
                    _safe_text(document.get("output_dir")),
                    totals["total"],
                    totals["completed"],
                    totals["errors"],
                    now,
                    now,
                ),
            )
        return counters

    @staticmethod
    def _run_totals(items: dict[str, Any]) -> dict[str, int]:
        completed = errors = 0
        for entry in items.values():
            if not isinstance(entry, dict):
                continue
            if _safe_text(entry.get("status")) == "completed" and _safe_text(entry.get("copied_to")):
                completed += 1
            elif _safe_text(entry.get("status")) == "error":
                errors += 1
        return {"total": len(items), "completed": completed, "errors": errors}

    def _entry_to_row(
        self, run_id: str, index: Any, entry: dict[str, Any], document: dict[str, Any], now: float
    ) -> dict[str, Any]:
        generation = entry.get("generation") if isinstance(entry.get("generation"), dict) else {}
        copied_to = _safe_text(entry.get("copied_to"))
        size_bytes, width, height, mtime_ns = _file_facts(copied_to)
        attempts = entry.get("attempts") if isinstance(entry.get("attempts"), list) else []
        seeds = generation.get("seeds") if isinstance(generation.get("seeds"), dict) else {}
        return {
            "run_id": run_id,
            "item_index": int(index) if str(index).lstrip("-").isdigit() else 0,
            "title": _safe_text(entry.get("title")),
            "preset_name": _safe_text(entry.get("preset_name")),
            "status": _safe_text(entry.get("status")),
            "review_status": _safe_text(entry.get("review_status")) or "待确认",
            "note": _safe_text(entry.get("note")),
            "prompt": _safe_text(entry.get("prompt")),
            "negative_prompt": _safe_text(entry.get("negative_prompt")),
            "compiled_prompt": _safe_text(entry.get("compiled_prompt")),
            "seed": _seed_of(entry),
            "seeds_json": json.dumps(seeds, ensure_ascii=False),
            "model": _safe_text(generation.get("model")),
            "workflow_path": _safe_text(generation.get("workflow_path")),
            "workflow_variant": _safe_text(generation.get("workflow_variant")),
            "output_dir": _safe_text(document.get("output_dir")),
            "copied_to": copied_to,
            "size_bytes": size_bytes,
            "width": width,
            "height": height,
            "mtime_ns": mtime_ns,
            "attempt_count": len(attempts),
            "elapsed": _safe_float(entry.get("elapsed")),
            "error": _safe_text(entry.get("error")),
            "history_json": json.dumps(entry.get("history") or [], ensure_ascii=False),
            "indexed_at": now,
            "updated_at": now,
        }

    def sync_all(self, reviews: Any) -> dict[str, int]:
        """Rebuild the index from every review document on disk.

        ``reviews`` is anything with ``root`` (a directory) and ``load(run_id)``
        -- the review store, in practice. Taking it as a duck-typed argument
        rather than importing ``comfybatch_v2_core`` keeps this module loadable
        on its own, which is what lets it be tested without a ComfyUI.
        """
        root = pathlib.Path(getattr(reviews, "root", reviews))
        totals = {"runs": 0, "inserted": 0, "updated": 0, "skipped": 0, "failed": 0}
        if not root.is_dir():
            return totals
        for path in sorted(root.glob("*.json")):
            run_id = path.stem
            try:
                document = reviews.load(run_id) if hasattr(reviews, "load") else json.loads(
                    path.read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError):
                totals["failed"] += 1
                continue
            counters = self.sync_run(run_id, document)
            totals["runs"] += 1
            for key in ("inserted", "updated", "skipped"):
                totals[key] += counters[key]
        return totals

    # ------------------------------------------------------------- query

    def query(
        self,
        *,
        q: str = "",
        run_id: str = "",
        review_status: str = "",
        tag: str = "",
        favorite: bool | None = None,
        model: str = "",
        include_deleted: bool = False,
        sort: str = "newest",
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Search the library.

        Every filter is optional and they combine with AND, which is what makes
        the panel's "narrow it down until you find it" workflow possible without
        a query language.
        """
        if sort not in SORTS:
            raise ValueError(f"不支持的作品库排序：{sort}")
        if review_status and review_status not in REVIEW_STATUSES:
            raise ValueError(f"不支持的确认状态：{review_status}")
        # ``0`` means "one row", not "use the default": a truthiness test here
        # would silently turn the smallest legal page into the default one.
        limit = _clamp(int_or(limit, DEFAULT_PAGE_SIZE), 1, MAX_PAGE_SIZE)
        offset = _clamp(int_or(offset, 0), 0, None)

        clauses: list[str] = []
        params: list[Any] = []
        if not include_deleted:
            clauses.append("w.deleted_at = 0")
        if run_id:
            clauses.append("w.run_id = ?")
            params.append(run_id)
        if review_status:
            clauses.append("w.review_status = ?")
            params.append(review_status)
        if model:
            clauses.append("w.model = ?")
            params.append(model)
        if favorite is not None:
            clauses.append("w.favorite = ?")
            params.append(1 if favorite else 0)
        if tag:
            clauses.append("EXISTS (SELECT 1 FROM work_tags t WHERE t.work_id = w.work_id AND t.tag = ?)")
            params.append(tag)
        if q:
            pattern = _like_pattern(q)
            clauses.append(
                "("
                "w.title LIKE ? ESCAPE '\\' OR w.prompt LIKE ? ESCAPE '\\' OR "
                "w.compiled_prompt LIKE ? ESCAPE '\\' OR w.negative_prompt LIKE ? ESCAPE '\\' OR "
                "w.note LIKE ? ESCAPE '\\' OR w.model LIKE ? ESCAPE '\\' OR "
                "w.seed LIKE ? ESCAPE '\\' OR w.work_id LIKE ? ESCAPE '\\' OR "
                "EXISTS (SELECT 1 FROM work_tags t WHERE t.work_id = w.work_id AND t.tag LIKE ? ESCAPE '\\')"
                ")"
            )
            params.extend([pattern] * 9)

        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._session() as connection:
            total = int(connection.execute(f"SELECT COUNT(*) AS n FROM works w{where}", params).fetchone()["n"])
            rows = connection.execute(
                f"SELECT w.* FROM works w{where} ORDER BY {SORTS[sort]} LIMIT ? OFFSET ?",
                params + [limit, offset],
            ).fetchall()
            items = [self._row_to_item(connection, row) for row in rows]
            facets = self._facets(connection)
        return {"total": total, "limit": limit, "offset": offset, "sort": sort, "items": items, "facets": facets}

    def _facets(self, connection: sqlite3.Connection) -> dict[str, Any]:
        """Counts for the panel's filter chips, scoped to live records only."""
        by_status = {status: 0 for status in REVIEW_STATUSES}
        for row in connection.execute(
            "SELECT review_status, COUNT(*) AS n FROM works WHERE deleted_at = 0 GROUP BY review_status"
        ):
            by_status[_safe_text(row["review_status"])] = int(row["n"])
        favorite = int(connection.execute("SELECT COUNT(*) AS n FROM works WHERE deleted_at = 0 AND favorite = 1").fetchone()["n"])
        total = int(connection.execute("SELECT COUNT(*) AS n FROM works WHERE deleted_at = 0").fetchone()["n"])
        deleted = int(connection.execute("SELECT COUNT(*) AS n FROM works WHERE deleted_at <> 0").fetchone()["n"])
        tags = [
            {"tag": _safe_text(row["tag"]), "count": int(row["n"])}
            for row in connection.execute(
                "SELECT t.tag, COUNT(*) AS n FROM work_tags t JOIN works w ON w.work_id = t.work_id "
                "WHERE w.deleted_at = 0 GROUP BY t.tag ORDER BY n DESC, t.tag ASC LIMIT 40"
            )
        ]
        runs = [
            {"run_id": _safe_text(row["run_id"]), "total": int(row["total"])}
            for row in connection.execute(
                "SELECT r.run_id, r.total FROM runs r WHERE EXISTS "
                "(SELECT 1 FROM works w WHERE w.run_id = r.run_id AND w.deleted_at = 0) "
                "ORDER BY r.updated_at DESC LIMIT 40"
            )
        ]
        return {"total": total, "deleted": deleted, "favorite": favorite, "by_status": by_status, "tags": tags, "runs": runs}

    def _row_to_item(self, connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        copied_to = _safe_text(row["copied_to"])
        available = bool(copied_to) and pathlib.Path(copied_to).is_file()
        tags = [
            _safe_text(tag["tag"])
            for tag in connection.execute(
                "SELECT tag FROM work_tags WHERE work_id = ? ORDER BY tag ASC", (row["work_id"],)
            )
        ]
        stored_size = int(row["size_bytes"] or 0)
        stored_mtime = int(row["mtime_ns"] or 0)
        size_bytes, width, height, mtime_ns = stored_size, int(row["width"] or 0), int(row["height"] or 0), stored_mtime
        if available:
            live_size, live_width, live_height, live_mtime = _file_facts(copied_to)
            # A redo rewrites the same path, so the recorded numbers go stale the
            # moment it happens. Reporting the live ones is the difference between
            # "the library says 4 MB" and the truth.
            size_bytes = live_size or stored_size
            mtime_ns = live_mtime or stored_mtime
            if live_mtime != stored_mtime or not width:
                width, height = live_width or width, live_height or height
        try:
            seeds = json.loads(_safe_text(row["seeds_json"]) or "{}")
        except json.JSONDecodeError:
            seeds = {}
        try:
            history = json.loads(_safe_text(row["history_json"]) or "[]")
        except json.JSONDecodeError:
            history = []
        return {
            "work_id": _safe_text(row["work_id"]),
            "run_id": _safe_text(row["run_id"]),
            "index": int(row["item_index"] or 0),
            "title": _safe_text(row["title"]),
            "preset_name": _safe_text(row["preset_name"]),
            "status": _safe_text(row["status"]),
            "review_status": _safe_text(row["review_status"]),
            "note": _safe_text(row["note"]),
            "prompt": _safe_text(row["prompt"]),
            "negative_prompt": _safe_text(row["negative_prompt"]),
            "seed": _safe_text(row["seed"]),
            "seeds": seeds,
            "model": _safe_text(row["model"]),
            "workflow_path": _safe_text(row["workflow_path"]),
            "workflow_variant": _safe_text(row["workflow_variant"]),
            "output_dir": _safe_text(row["output_dir"]),
            "copied_to": copied_to,
            "available": available,
            "size_bytes": size_bytes,
            "width": width,
            "height": height,
            "attempt_count": int(row["attempt_count"] or 0),
            "elapsed": row["elapsed"],
            "error": _safe_text(row["error"]),
            "history_count": len(history),
            "favorite": bool(row["favorite"]),
            "deleted": bool(row["deleted_at"]),
            "indexed_at": float(row["indexed_at"] or 0),
            "updated_at": float(row["updated_at"] or 0),
            "tags": tags,
        }

    # ----------------------------------------------------------- mutation

    def get(self, work_id: str, *, include_deleted: bool = True) -> dict[str, Any]:
        clause = "" if include_deleted else " AND deleted_at = 0"
        with self._session() as connection:
            row = connection.execute(
                f"SELECT * FROM works WHERE work_id = ?{clause}", (str(work_id),)
            ).fetchone()
            if row is None:
                raise ValueError(f"作品库中没有这条记录：{work_id}")
            return self._row_to_item(connection, row)

    def set_favorite(self, work_id: str, favorite: bool = True) -> dict[str, Any]:
        with self._session() as connection:
            cursor = connection.execute(
                "UPDATE works SET favorite = ?, updated_at = ? WHERE work_id = ? AND deleted_at = 0",
                (1 if favorite else 0, time.time(), str(work_id)),
            )
            if not cursor.rowcount:
                raise ValueError(f"作品库中没有这条记录：{work_id}")
        return self.get(work_id)

    def add_tags(self, work_id: str, tags: Iterable[str]) -> dict[str, Any]:
        cleaned = _clean_tags(tags)
        with self._session() as connection:
            self._require_live(connection, work_id)
            now = time.time()
            connection.executemany(
                "INSERT INTO work_tags(work_id, tag, created_at) VALUES(?, ?, ?) ON CONFLICT(work_id, tag) DO NOTHING",
                [(str(work_id), tag, now) for tag in cleaned],
            )
        return self.get(work_id)

    def remove_tag(self, work_id: str, tag: str) -> dict[str, Any]:
        with self._session() as connection:
            self._require_live(connection, work_id)
            connection.execute("DELETE FROM work_tags WHERE work_id = ? AND tag = ?", (str(work_id), str(tag).strip()))
        return self.get(work_id)

    def set_tags(self, work_id: str, tags: Iterable[str]) -> dict[str, Any]:
        """Replace the tag set, so the panel's editor has one predictable verb."""
        cleaned = _clean_tags(tags)
        with self._session() as connection:
            self._require_live(connection, work_id)
            connection.execute("DELETE FROM work_tags WHERE work_id = ?", (str(work_id),))
            now = time.time()
            connection.executemany(
                "INSERT INTO work_tags(work_id, tag, created_at) VALUES(?, ?, ?)",
                [(str(work_id), tag, now) for tag in cleaned],
            )
        return self.get(work_id)

    def delete(self, work_id: str, reason: str = "") -> dict[str, Any]:
        """Remove a record from the library without touching the image.

        The file on disk is left exactly where it is. This is a *library*
        operation: the user is saying "stop showing me this", not "delete my
        work". Keeping the bytes is also what makes :meth:`restore` honest.
        """
        with self._session() as connection:
            cursor = connection.execute(
                "UPDATE works SET deleted_at = ?, updated_at = ? WHERE work_id = ? AND deleted_at = 0",
                (time.time(), time.time(), str(work_id)),
            )
            if not cursor.rowcount:
                raise ValueError(f"作品库中没有这条记录：{work_id}")
        return {"work_id": str(work_id), "deleted": True, "reason": _safe_text(reason)}

    def restore(self, work_id: str) -> dict[str, Any]:
        with self._session() as connection:
            cursor = connection.execute(
                "UPDATE works SET deleted_at = 0, updated_at = ? WHERE work_id = ? AND deleted_at <> 0",
                (time.time(), str(work_id)),
            )
            if not cursor.rowcount:
                raise ValueError(f"这条记录不在已删除列表中：{work_id}")
        return self.get(work_id)

    @staticmethod
    def _require_live(connection: sqlite3.Connection, work_id: str) -> None:
        row = connection.execute(
            "SELECT deleted_at FROM works WHERE work_id = ?", (str(work_id),)
        ).fetchone()
        if row is None:
            raise ValueError(f"作品库中没有这条记录：{work_id}")
        if int(row["deleted_at"] or 0):
            raise ValueError(f"这条记录已从作品库删除，请先恢复：{work_id}")

    # ------------------------------------------------------------- report

    def runs(self, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(MAX_PAGE_SIZE, int(limit or 50)))
        with self._session() as connection:
            return [
                {
                    "run_id": _safe_text(row["run_id"]),
                    "output_dir": _safe_text(row["output_dir"]),
                    "total": int(row["total"] or 0),
                    "completed": int(row["completed"] or 0),
                    "errors": int(row["errors"] or 0),
                    "updated_at": float(row["updated_at"] or 0),
                }
                for row in connection.execute(
                    "SELECT * FROM runs ORDER BY updated_at DESC LIMIT ?", (limit,)
                )
            ]

    def stats(self) -> dict[str, Any]:
        """Counters for the panel header. Derived from the index, never guessed."""
        try:
            size_bytes = self.path.stat().st_size
        except OSError:
            size_bytes = 0
        with self._session() as connection:
            facets = self._facets(connection)
            total_bytes = int(
                connection.execute(
                    "SELECT COALESCE(SUM(size_bytes), 0) AS n FROM works WHERE deleted_at = 0"
                ).fetchone()["n"]
                or 0
            )
            favorite_bytes = int(
                connection.execute(
                    "SELECT COALESCE(SUM(size_bytes), 0) AS n FROM works WHERE deleted_at = 0 AND favorite = 1"
                ).fetchone()["n"]
                or 0
            )
            # Scoped to live records the same way ``facets`` counts them: a tag
            # left over from a cleared index must not appear in one number and
            # vanish from the tag strip in the same payload.
            tag_count = int(
                connection.execute(
                    "SELECT COUNT(DISTINCT t.tag) AS n FROM work_tags t "
                    "JOIN works w ON w.work_id = t.work_id WHERE w.deleted_at = 0"
                ).fetchone()["n"]
            )
            run_count = int(connection.execute("SELECT COUNT(*) AS n FROM runs").fetchone()["n"])
        return {
            "works": facets["total"],
            "deleted": facets["deleted"],
            "favorite": facets["favorite"],
            "by_status": facets["by_status"],
            "runs": run_count,
            "tags": tag_count,
            "image_bytes": total_bytes,
            "favorite_bytes": favorite_bytes,
            "db_bytes": size_bytes,
            # The absolute path is deliberately not sent: it is the user's data
            # directory and nothing on the page needs it.
            "available": self.available,
            "schema_version": SCHEMA_VERSION,
            "warning": self.last_warning,
        }

    # --------------------------------------------------------- maintenance

    def vacuum(self) -> dict[str, Any]:
        """Reclaim space after deletions; never touches the images themselves."""
        before = 0
        try:
            before = self.path.stat().st_size
        except OSError:
            pass
        with self._session() as connection:
            connection.execute("VACUUM")
        after = 0
        try:
            after = self.path.stat().st_size
        except OSError:
            pass
        return {"before": before, "after": after}

    def clear_index(self) -> dict[str, int]:
        """Drop every derived row, keeping favourites, tags and deletions.

        The panel offers this after a manual clean-up of the output folder: what
        the index knows is rebuildable, what the user marked is not, so only the
        first is thrown away. Tags survive on the rows that survive -- a
        favourite keeps its tags, and so does a tombstone.
        """
        with self._session() as connection:
            # Tags on the dropped rows go with them. Leaving them behind produced
            # tags that no record carried: invisible in the tag strip, but
            # reappearing on whatever later took the same ``work_id``.
            connection.execute(
                "DELETE FROM work_tags WHERE work_id IN "
                "(SELECT work_id FROM works WHERE favorite = 0 AND deleted_at = 0)"
            )
            works = connection.execute("DELETE FROM works WHERE favorite = 0 AND deleted_at = 0").rowcount
            connection.execute("DELETE FROM runs")
        return {"removed": int(works)}


def _is_index(value: Any) -> bool:
    """True for anything that can name a batch slot (``1``, ``"1"``, ``"-2"``)."""
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    return str(value).strip().lstrip("-").isdigit()


def _clean_tags(tags: Iterable[str]) -> list[str]:
    """Normalise the tag list: trimmed, non-empty, case-insensitively unique."""
    cleaned: list[str] = []
    seen: set[str] = set()
    for value in tags or []:
        text = _safe_text(value).strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(text)
    return cleaned


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def int_or(value: Any, fallback: int) -> int:
    """``int(value)`` for anything a query string can carry, else ``fallback``."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _clamp(value: int, low: int, high: int | None) -> int:
    value = max(low, value)
    return value if high is None else min(high, value)


def _file_facts(path_text: str) -> tuple[int, int, int, int]:
    """(size, width, height, mtime_ns) for an image, or zeros when unreadable.

    Never raises. A missing or locked file must not stop an index pass: the
    record is still worth keeping, it just reports ``available: false``.
    """
    if not path_text:
        return 0, 0, 0, 0
    path = pathlib.Path(path_text)
    try:
        stat = path.stat()
    except OSError:
        return 0, 0, 0, 0
    width = height = 0
    if Image is not None:
        try:
            with Image.open(path) as image:
                width, height = image.size
        except Exception:  # noqa: BLE001 - any decoder failure just means "no size"
            width = height = 0
    return int(stat.st_size), int(width), int(height), int(stat.st_mtime_ns)
