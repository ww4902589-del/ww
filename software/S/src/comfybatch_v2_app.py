from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import io
import json
import mimetypes
import os
import pathlib
import re
import socket
import sys
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
import urllib.request as urllib_request
from urllib.request import ProxyHandler, build_opener

from PIL import Image

from comfybatch_errors import SEVERITY_BLOCKING, ErrorTranslator
from comfybatch_image_extract import ImageExtractor
from comfybatch_nodeschema import (
    NodeSchemaRegistry, active_registry, purpose_catalog, set_active_registry, structural_fingerprint,
)
from comfybatch_hub import EditLease, InstanceLock, SSE_HEARTBEAT_SECONDS, StateHub
from comfybatch_params import PARAMS, registry_payload, resolve as resolve_params
from comfybatch_v2_core import (
    REDO_MODES,
    REVIEW_STATUSES,
    THUMBNAIL_SIZE,
    BatchConfig,
    BatchRunner,
    ComfyClient,
    Krea2WorkflowAdapter,
    PromptBundle,
    PromptBundleParser,
    PromptItem,
    ResourceInventory,
    ResourceOverrideStore,
    ResultReviewStore,
    WorkflowParamsStore,
    _safe_name,
)


DEFAULT_COMFY_ROOT = pathlib.Path(os.environ.get("COMFYBATCH_COMFY_ROOT") or pathlib.Path.home() / "ComfyUI")
DEFAULT_WORKFLOW_ROOT = pathlib.Path(
    os.environ.get("COMFYBATCH_WORKFLOW_ROOT") or pathlib.Path.home() / "Documents" / "ComfyBatch" / "workflows"
)
DEFAULT_OUTPUT_ROOT = pathlib.Path(
    os.environ.get("COMFYBATCH_OUTPUT_ROOT") or pathlib.Path.home() / "Desktop" / "ComfyBatch-output"
)
#: The page is three files instead of one minified blob. Served from here so a
#: frozen build finds them via ``resource_path`` exactly like the sources.
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "application/javascript; charset=utf-8"),
}
STATIC_ASSETS: dict[str, bytes] = {}


def load_static_assets() -> dict[str, bytes]:
    """Read the page assets into memory once at startup."""
    assets: dict[str, bytes] = {}
    for route, (name, _content_type) in STATIC_FILES.items():
        try:
            assets[route] = resource_path(name).read_bytes()
        except OSError:
            assets[route] = b""
    return assets
PERSISTENT_MAPS = ("lora_profiles", "style_lora_presets")
#: Entity map -> the tombstone map that records deletions of its entries.
#: Tombstones exist so a delete cannot be undone by a stale copy of the file.
TOMBSTONE_MAPS = {
    "style_lora_presets": "deleted_style_lora_presets",
    "lora_profiles": "deleted_lora_profiles",
}
#: Entities come from the winning file; tombstones are the union of every file.
ENTITY_KEYS = frozenset(TOMBSTONE_MAPS)
TOMBSTONE_KEYS = frozenset(TOMBSTONE_MAPS.values())
#: Bumped when the document shape changes, so an upgrade can migrate explicitly.
SCHEMA_VERSION = 2
#: The one place the product version is written down.
APP_VERSION = "2.20"
#: Shipped preset library. Populated once the loader below is defined, so the
#: module can be read top to bottom.
DEFAULT_STYLE_LORA_PRESETS: list[dict] = []
#: Cap on concurrent SSE connections; each one holds a server thread.
MAX_EVENT_STREAMS = 12

#: Sent while nothing changed, so intermediaries do not drop an idle stream.
HEARTBEAT_BYTES = b": keep-alive\n\n"


def sse_frame(event: str, payload: str) -> bytes:
    """Format one Server-Sent Event. Framing lives here so it is written once."""
    return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")


#: Name of the Windows mutex that keeps the desktop app single-instance.
INSTANCE_MUTEX_NAME = "ComfyBatch-S-desktop"

#: Routes that mutate observable state, so they need the editing lease and must
#: notify open pages. Listed here once instead of being checked per branch.
WRITE_ROUTES = frozenset({
    "/api/configure", "/api/import", "/api/import-images", "/api/extract-images", "/api/remap-import",
    "/api/update-bundle", "/api/save-lora-profile", "/api/delete-lora-profile", "/api/save-style-lora-preset",
    "/api/delete-style-lora-preset", "/api/assign-style-lora-preset",
    "/api/assign-image-preset", "/api/params/apply", "/api/params/clear",
    "/api/resource/apply", "/api/resource/forget", "/api/resource/reapply",
    "/api/preflight", "/api/start", "/api/pause", "/api/resume", "/api/cancel",
    "/api/review/confirm", "/api/review/note", "/api/review/redo", "/api/review/redo-batch",
    "/api/reload-schema", "/api/open-folder",
    "/api/restore-default-presets",
})
#: Aspect-ratio labels must match the ``ResolutionSelector`` node's candidate
#: list byte for byte -- ComfyUI rejects anything else with a 400
#: ``value_not_in_list``. The live candidate list (recorded in
#: ``tests/fixtures/object_info.json``) is::
#:
#:     1:1 (Square), 2:3 (Portrait Photo), 3:2 (Photo), 3:4 (Portrait Standard),
#:     4:3 (Standard), 9:16 (Portrait Widescreen), 16:9 (Widescreen), 21:9 (Ultrawide)
#:
#: Five of the original entries used labels that do not exist
#: ("3:2 (Landscape)", "4:3 (Landscape)", "9:16 (Portrait)"), so selecting those
#: presets could only ever fail validation. ``tests/test_image_presets.py`` pins
#: every entry against the real schema.
IMAGE_PRESETS = (
    {"id": "wide-s", "name": "横图·小 1024×576", "aspect_ratio": "16:9 (Widescreen)", "megapixels": 0.6},
    {"id": "wide-m", "name": "横图·标准 1280×720", "aspect_ratio": "16:9 (Widescreen)", "megapixels": 0.9},
    {"id": "wide-l", "name": "横图·高清 1536×864", "aspect_ratio": "16:9 (Widescreen)", "megapixels": 1.3},
    {"id": "landscape-m", "name": "横图·3:2 标准 1200×800", "aspect_ratio": "3:2 (Photo)", "megapixels": 1.0},
    {"id": "classic-m", "name": "横图·4:3 标准 1152×864", "aspect_ratio": "4:3 (Standard)", "megapixels": 1.0},
    {"id": "square-m", "name": "方图·标准 1024×1024", "aspect_ratio": "1:1 (Square)", "megapixels": 1.0},
    {"id": "portrait-s", "name": "竖图·小 576×1024", "aspect_ratio": "9:16 (Portrait Widescreen)", "megapixels": 0.6},
    {"id": "portrait-m", "name": "竖图·标准 720×1280", "aspect_ratio": "9:16 (Portrait Widescreen)", "megapixels": 0.9},
    {"id": "portrait-l", "name": "竖图·高清 864×1536", "aspect_ratio": "9:16 (Portrait Widescreen)", "megapixels": 1.3},
)


def default_settings_path() -> pathlib.Path:
    override = os.environ.get("COMFYBATCH_DATA_DIR")
    if override:
        return pathlib.Path(override) / "settings.json"
    local_data = pathlib.Path(os.environ.get("LOCALAPPDATA") or pathlib.Path.home() / "AppData" / "Local")
    return local_data / "ComfyBatch-S" / "settings.json"


def default_backup_settings_path() -> pathlib.Path | None:
    """Rotating recovery copy, kept next to the user's data.

    Previously this pointed inside the installation directory -- the very file an
    update replaces -- so a repaired or reinstalled build could ship an old copy
    and resurrect presets the user had deleted. The recovery copy now lives with
    the user's data and is read only when the primary is unusable.
    """
    return default_settings_path().parent / "settings.backup.json"


def shipped_defaults_path() -> pathlib.Path:
    """The read-only preset library that ships with the program.

    Kept as its own file inside the package, deliberately separate from the
    user's ``settings.json``. That separation is what makes it safe for an update
    to replace the shipped template: it cannot touch user data, and a deleted
    preset is not reinstated by it.

    Resolved directly rather than through ``resource_path`` so it can be loaded
    while this module is still being defined.
    """
    bundle = pathlib.Path(getattr(sys, "_MEIPASS", pathlib.Path(__file__).resolve().parent))
    return bundle / "default_presets.json"


def load_shipped_default_presets() -> list[dict]:
    try:
        payload = json.loads(shipped_defaults_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    presets = payload.get("style_lora_presets") if isinstance(payload, dict) else None
    if not isinstance(presets, dict):
        return []
    return [{**value, "id": key} for key, value in presets.items() if isinstance(value, dict)]


DEFAULT_STYLE_LORA_PRESETS = load_shipped_default_presets()


def legacy_settings_paths() -> list[pathlib.Path]:
    """Read-only sources an older build may have written.

    The old build mirrored settings into ``<exe dir>/S-data/settings.json``. That
    path is found automatically when frozen; elsewhere it can be supplied through
    ``COMFYBATCH_LEGACY_SETTINGS`` (``;``-separated) so a migration can be run
    deliberately. These files are read once and then **never written**, which is
    what stops an update from reintroducing old data.
    """
    found: list[pathlib.Path] = []
    if getattr(sys, "frozen", False):
        found.append(pathlib.Path(sys.executable).resolve().parent / "S-data" / "settings.json")
    for entry in os.environ.get("COMFYBATCH_LEGACY_SETTINGS", "").split(os.pathsep):
        entry = entry.strip()
        if entry:
            found.append(pathlib.Path(entry))
    seen: set[str] = set()
    unique: list[pathlib.Path] = []
    for path in found:
        key = str(path).lower()
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def schema_cache_path() -> pathlib.Path:
    """Cached ``/object_info`` so validation still works with ComfyUI offline."""
    return default_settings_path().parent / "object_info.cache.json"


def data_dir() -> pathlib.Path:
    """Application data directory; overridable so dev never touches live presets."""
    override = os.environ.get("COMFYBATCH_DATA_DIR")
    if override:
        return pathlib.Path(override)
    return default_settings_path().parent


def resource_path(name: str) -> pathlib.Path:
    base = pathlib.Path(getattr(sys, "_MEIPASS", pathlib.Path(__file__).resolve().parent))
    return base / name


class SettingsStore:
    """Versioned store with one authoritative file and deletion tombstones.

    Why this is not the old mirror-and-union
    ----------------------------------------
    The previous version read every candidate file and **unioned** the preset and
    LoRA maps across them, while ``save`` only raised if *all* writes failed. So a
    single stale copy -- a failed write, or an update that shipped an old file --
    put an already-deleted preset straight back. That is a data-loss-in-reverse
    bug: the user deletes something and it returns.

    The rules now are:

    * **One authority.** Entities come from a single winning file, chosen by
      ``revision``, then ``updated_at``, then mtime. Nothing is merged blindly.
    * **Tombstones are additive.** Deletions are recorded with ``deleted_at`` and
      the tombstone set is the *union* across every readable file, so a delete in
      any copy wins. Deleting is therefore irreversible-by-accident.
    * **Legacy files are read-only.** The install-directory copy is treated as a
      migration source, never written, so an update cannot reintroduce old data.
      Updates that replace a mirror cannot clobber user data that lives elsewhere.
    * **The backup is for recovery, not for merging.** It mirrors the latest
      successful write and is consulted only when the primary is missing or
      corrupt. Mirroring the *new* content (rather than keeping the previous
      revision) is what makes recovery lossless -- a backup one step behind would
      silently drop the user's most recent change.
    """

    def __init__(
        self,
        primary: pathlib.Path,
        backup: pathlib.Path | None = None,
        legacy: list[pathlib.Path] | None = None,
    ) -> None:
        self.primary = pathlib.Path(primary)
        self.backup = pathlib.Path(backup) if backup and pathlib.Path(backup) != self.primary else None
        #: Read-only sources kept only so an upgrade does not lose data that an
        #: older build wrote somewhere else.
        self.legacy = [pathlib.Path(p) for p in (legacy or []) if pathlib.Path(p) != self.primary]
        #: Retained for callers that only want to know whether a recovery copy exists.
        self.paths = [p for p in [self.primary, self.backup] if p is not None]

    # ------------------------------------------------------------- reading

    def _read(self, path: pathlib.Path) -> dict | None:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    @staticmethod
    def _rank(payload: dict, path: pathlib.Path) -> tuple[int, str, float]:
        """Order candidates: revision first, then timestamp, then mtime.

        ``revision`` is a logical clock, so it survives clock skew and file
        copies -- mtime alone is not trustworthy across machines or restores.
        """
        try:
            revision = int(payload.get("revision") or 0)
        except (TypeError, ValueError):
            revision = 0
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = 0.0
        return revision, str(payload.get("updated_at") or ""), mtime

    def _candidates(self) -> list[tuple[pathlib.Path, dict]]:
        found: list[tuple[pathlib.Path, dict]] = []
        for path in [self.primary, self.backup, *self.legacy]:
            if path is None or not path.exists():
                continue
            payload = self._read(path)
            if payload is not None:
                found.append((path, payload))
        return found

    def load(self) -> dict:
        candidates = self._candidates()
        if not candidates:
            return {}

        winner_path, winner = max(candidates, key=lambda item: self._rank(item[1], item[0]))

        document: dict = {
            key: value for key, value in winner.items()
            if key not in ENTITY_KEYS and key not in TOMBSTONE_KEYS and key != "revision"
        }
        # Entities from the winner alone: no cross-file union, so nothing returns.
        for key in PERSISTENT_MAPS:
            value = winner.get(key)
            document[key] = dict(value) if isinstance(value, dict) else {}

        # Tombstones from every readable file, unioned: a delete anywhere wins.
        # Note the iteration is over the tombstone KEY NAMES, not the entity keys --
        # reading the entity map here would re-merge the very entries a tombstone
        # is meant to remove.
        tombstones: dict[str, dict] = {}
        for _path, payload in candidates:
            for tombstone_key in TOMBSTONE_MAPS.values():
                stored = payload.get(tombstone_key)
                if isinstance(stored, dict):
                    tombstones.setdefault(tombstone_key, {}).update(stored)
        for key, deleted in tombstones.items():
            if deleted:
                document[key] = deleted

        # Apply them, so a resurrected entry cannot survive a load.
        for entity_key, tombstone_key in TOMBSTONE_MAPS.items():
            deleted = document.get(tombstone_key) or {}
            entities = document.get(entity_key)
            if deleted and isinstance(entities, dict):
                for identity in list(entities):
                    if identity in deleted:
                        entities.pop(identity, None)

        document.setdefault("schema_version", SCHEMA_VERSION)
        return document

    # ------------------------------------------------------------- writing

    def save(self, value: dict) -> None:
        """Write the authoritative file, keeping the previous revision as backup.

        The primary write failure is propagated: silently continuing was how the
        stale mirror got created in the first place.
        """
        existing = self._read(self.primary) or {}
        try:
            previous_revision = int(existing.get("revision") or 0)
        except (TypeError, ValueError):
            previous_revision = 0

        payload = {
            **value,
            "schema_version": SCHEMA_VERSION,
            "revision": previous_revision + 1,
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        body = json.dumps(payload, ensure_ascii=False, indent=2)

        self.primary.parent.mkdir(parents=True, exist_ok=True)
        # The authoritative write must succeed; letting it fail silently is what
        # created stale mirrors in the first place.
        self._write_atomic(self.primary, body)
        if self.backup is not None:
            # Same content, so a recovery loses nothing. Best-effort: a broken
            # recovery copy must not fail the write the user asked for.
            try:
                self._write_atomic(self.backup, body)
            except OSError:
                pass

    @staticmethod
    def _write_atomic(path: pathlib.Path, body: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(body, encoding="utf-8")
        temporary.replace(path)

    # ---------------------------------------------------------- tombstones

    def tombstones(self) -> dict[str, dict]:
        """The union of every readable copy's deletion records."""
        merged: dict[str, dict] = {}
        for _path, payload in self._candidates():
            for tombstone_key in TOMBSTONE_MAPS.values():
                stored = payload.get(tombstone_key)
                if isinstance(stored, dict):
                    merged.setdefault(tombstone_key, {}).update(stored)
        return merged


def is_comfybatch_server(host: str, port: int, timeout: float = 0.7) -> bool:
    try:
        opener = build_opener(ProxyHandler({}))
        with opener.open(f"http://{host}:{port}/", timeout=timeout) as response:
            body = response.read(8192).decode("utf-8", errors="ignore")
        return response.status == 200 and "ComfyBatch" in body
    except Exception:
        return False


def is_port_available(host: str, port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind((host, port))
        return True
    except OSError:
        return False


def choose_launch_port(host: str, preferred: int, probe=None, available=None) -> tuple[str, int]:
    server_probe = probe or (lambda port: is_comfybatch_server(host, port))
    port_probe = available or (lambda port: is_port_available(host, port))
    if server_probe(preferred):
        return "reuse", preferred
    for port in range(preferred, preferred + 21):
        if port_probe(port):
            return "start", port
    raise OSError(f"端口 {preferred} 至 {preferred + 20} 均不可用")


def parse_prompt_indexes(expression: str, total: int) -> list[int]:
    selected: set[int] = set()
    for token in re.split(r"[,，;；\s]+", str(expression or "").strip()):
        if not token:
            continue
        match = re.fullmatch(r"(\d+)\s*[-—~至]\s*(\d+)", token)
        if match:
            start, end = int(match.group(1)), int(match.group(2))
            if start > end:
                start, end = end, start
            selected.update(range(start, end + 1))
        elif token.isdigit():
            selected.add(int(token))
        else:
            raise ValueError(f"无法识别的编号：{token}")
    invalid = sorted(index for index in selected if index < 1 or index > total)
    if invalid:
        raise ValueError(f"编号超出提示词范围 1-{total}：{invalid[0]}")
    return sorted(selected)


class Application:
    def __init__(self, settings_path: pathlib.Path | None = None, backup_settings_path: pathlib.Path | None = None) -> None:
        self.settings_path = pathlib.Path(settings_path) if settings_path else default_settings_path()
        backup = backup_settings_path if settings_path is not None else default_backup_settings_path()
        legacy = [] if settings_path is not None else legacy_settings_paths()
        self.settings_store = SettingsStore(self.settings_path, backup, legacy)
        # Loading is deliberately side-effect free. Persisting here looked like a
        # tidy migration, but it meant merely *importing* this module rewrote the
        # user's settings file -- a test, a script or a diagnostic would all touch
        # user data. Migration happens in memory, and the versioned shape is
        # written by the next real save.
        self._settings = self._load_settings()
        self.comfy_root = DEFAULT_COMFY_ROOT
        self.workflow_roots = [DEFAULT_WORKFLOW_ROOT]
        self.comfy_url = "http://127.0.0.1:8188"
        self.output_root = DEFAULT_OUTPUT_ROOT
        self.bundle = None
        self.last_import: dict[str, Any] | None = None
        #: Owns public-web validation, cover discovery and image verification.
        #: Kept injectable for hermetic tests; callers only see imported tasks.
        self.image_extractor = ImageExtractor()
        self._style_thumbnail_lookup: dict[tuple[str, str], str] = {}
        self.runner = BatchRunner(self.comfy_root, ComfyClient(self.comfy_url))
        self.lock = threading.RLock()
        #: Node schema from the last successful ``/object_info`` fetch. Installed
        #: process-wide so the compiler can validate parameters and detect
        #: missing resources/custom nodes before anything is submitted.
        self.schema: NodeSchemaRegistry = NodeSchemaRegistry.empty()
        self.reviews = ResultReviewStore(data_dir() / "reviews")
        #: User-approved resource replacements, keyed by workflow fingerprint.
        self.resource_rules = ResourceOverrideStore(data_dir() / "resource_rules")
        #: Workflow-level default parameter values ("工作流默认值" scope).
        self.workflow_params = WorkflowParamsStore(data_dir() / "workflow_params")
        self.last_preflight: dict[str, Any] = {}
        self.instance_id = uuid.uuid4().hex[:12]
        #: Pushes "something changed" to every open page instead of them polling.
        self.hub = StateHub()
        #: Single-writer guard so two pages cannot overwrite each other's config.
        self.lease = EditLease()
        #: Set by main() when this process won the single-instance race.
        self.is_primary = True
        self._stream_slots = threading.Semaphore(MAX_EVENT_STREAMS)
        #: Run id recovered from the review store on startup, so a reopened
        #: program still shows the previous session's images and decisions.
        self._restored_run_id = ""
        self._schema_checked = False
        # Cache only: constructing Application must not require a live ComfyUI,
        # so the network fetch is deferred to ensure_schema() (preflight/start).
        self.load_schema_cache()

    def acquire_stream_slot(self) -> bool:
        return self._stream_slots.acquire(blocking=False)

    def release_stream_slot(self) -> None:
        try:
            self._stream_slots.release()
        except ValueError:
            pass

    #: Granularity of the stream's wait, so a disconnected client is noticed
    #: within about a second instead of after a full heartbeat interval.
    STREAM_SLICE_SECONDS = 1.0

    def wait_for_change(self, last_seen: int) -> bool:
        """True when the version moved, False after one wait slice."""
        return self.hub.wait(last_seen, timeout=self.STREAM_SLICE_SECONDS) != last_seen

    def load_schema_cache(self) -> NodeSchemaRegistry:
        registry = NodeSchemaRegistry.empty()
        cache = schema_cache_path()
        if cache.exists():
            try:
                registry = NodeSchemaRegistry.from_path(cache)
            except (OSError, json.JSONDecodeError):
                registry = NodeSchemaRegistry.empty()
        self.schema = registry
        set_active_registry(registry)
        self.runner.adapter_registry = registry or None
        return registry

    def refresh_schema(self, *, force: bool = True) -> NodeSchemaRegistry:
        """Fetch ComfyUI's node definitions and cache them.

        Degrades to the on-disk cache so a cold start while ComfyUI is down still
        compiles with real validation instead of silently skipping it.
        """
        if self._schema_checked and not force:
            return self.schema
        registry = NodeSchemaRegistry.empty()
        try:
            payload = self.runner.client.object_info()
        except Exception:  # noqa: BLE001 - ComfyUI may simply be offline
            payload = None
        if NodeSchemaRegistry.looks_valid(payload):
            registry = NodeSchemaRegistry.from_object_info(payload)
        self._schema_checked = True
        if not registry:
            # Keep the previous (or cached) schema rather than replacing a good
            # one with nothing: a transient gateway hiccup must not disable
            # validation for the rest of the session.
            cached = self.load_schema_cache()
            return cached if cached else self.schema
        try:
            cache = schema_cache_path()
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(registry._info, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
        self.schema = registry
        set_active_registry(registry)
        self.runner.adapter_registry = registry
        return registry

    def ensure_schema(self) -> NodeSchemaRegistry:
        if self.schema:
            return self.schema
        return self.refresh_schema()

    def _inventory(self) -> ResourceInventory:
        return ResourceInventory(self.comfy_root, self.workflow_roots, self.runner.client)

    # ------------------------------------------------------------------ review

    def current_run_id(self) -> str:
        """The active run, or the most recent persisted one after a restart.

        The runner's state lives in memory only, but the confirmation decisions
        are on disk and the plan requires them to survive a restart. Falling back
        to the newest review document keeps the result page populated instead of
        blanking out every time the program is reopened.
        """
        run_id = str(self.runner.status().get("run_id") or "")
        if run_id:
            return run_id
        if not self._restored_run_id:
            self._restored_run_id = self.reviews.latest_run_id()
        return self._restored_run_id

    def results_payload(self) -> dict[str, Any]:
        """The three things the review page needs, and nothing about file paths.

        The frontend must not have to concatenate output directories and report
        files to show a result.
        """
        run_id = self.current_run_id()
        if not run_id:
            return {"run_id": "", "results": [], "review": {"total": 0, "by_status": {}}, "restored": False}
        active_run = str(self.runner.status().get("run_id") or "")
        if self.runner.status().get("results"):
            try:
                self.reviews.ingest(run_id, self.runner.status().get("results") or [])
            except OSError:
                pass
        items = self.reviews.list_for_run(run_id)
        # ``restored`` marks the previous session's results: they can still be
        # reviewed, but redoing needs a fresh run because the bundle and config
        # are not persisted.
        restored = run_id != active_run
        for item in items:
            item["restored"] = restored
        return {
            "run_id": run_id,
            "results": items,
            "review": self.reviews.summary(run_id),
            "statuses": list(REVIEW_STATUSES),
            "redo_modes": list(REDO_MODES),
            "restored": restored,
            "aborted_reason": self.runner.status().get("aborted_reason") or "",
            "seeds": self._seed_summary(items),
        }

    @staticmethod
    def _seed_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
        """Latest attempt's seed per item, for the review card."""
        summary: dict[str, Any] = {}
        for entry in items:
            seeds = ((entry.get("generation") or {}).get("seeds")) or {}
            if seeds:
                summary[str(entry.get("index"))] = seeds
        return summary

    def inspect_payload(self) -> dict[str, Any]:
        """Effective configuration, audit and blocking problems for the UI."""
        status = self.runner.status()
        audit = status.get("audit") or self.last_preflight.get("audit") or {}
        blocking = list(audit.get("blocking") or []) + list(audit.get("binding_blocking") or [])
        # The first three preflight layers report through ``errors`` and never
        # reach an audit, because a workflow they reject is never compiled. The
        # rail read only the audit, so a configuration that could not run showed
        # "没有阻断问题（未检查）" -- the opposite of what the check had just said.
        preflight_errors = list(self.last_preflight.get("errors") or [])
        effective = self.last_preflight.get("effective") or {}
        workflow_path = str(effective.get("workflow", {}).get("path") or "") if isinstance(effective.get("workflow"), dict) else ""
        fingerprint = str(audit.get("workflow_fingerprint") or "")
        inspected = bool(self.last_preflight)
        return {
            "run_id": status.get("run_id") or "",
            # ``inspected`` answers "has a check run", which is not the same as
            # "the checked values are usable": a rejected workflow still counts
            # as inspected, while the rail keeps marking its values unverified.
            "inspected": inspected,
            "ready": bool(self.last_preflight.get("ready")) if inspected else bool(audit.get("ready")) if audit else False,
            "audit": audit,
            "blocking": blocking,
            "preflight_errors": preflight_errors,
            # One number the UI can show, computed here rather than added up in
            # two places that could drift apart.
            "blocking_count": len(blocking) + len(preflight_errors),
            "effective": effective,
            "schema_nodes": len(self.schema),
            # Everything the drawer needs to offer an in-software replacement,
            # so the page never has to guess a path or a node id.
            "workflow_path": workflow_path,
            "fingerprint": fingerprint,
            "upscale_candidates": [
                item.get("value") for item in self.inventory().get("upscale_models", [])
            ],
            "model_directories": self.model_directories(),
            "rules": self.resource_rules.rules_for_fingerprint(fingerprint) if fingerprint else [],
            # Rules saved for this same file before it changed. Shown, never
            # applied automatically, so nothing is substituted silently.
            "stale_rules": self.resource_rules.stale_for(workflow_path, fingerprint) if workflow_path else [],
        }

    def blocked_preflight_payload(self, message: str) -> dict[str, Any]:
        """Inspection context that always carries the reason a check was blocked.

        A check can fail before the preflight records anything -- resolving the
        config is one such step -- and then ``preflight_errors`` is empty. The
        rail and the check panel sit on the same screen, so an empty list here
        reads as "no blocking problems" next to "不能生成". The message is
        therefore always represented as a problem, once.
        """
        payload = self.inspect_payload()
        if not payload["preflight_errors"]:
            payload["preflight_errors"] = [{
                "severity": SEVERITY_BLOCKING,
                "title": "预检未通过",
                "detail": message,
                "workflow_level": True,
            }]
        payload["inspected"] = True
        payload["ready"] = False
        payload["blocking_count"] = len(payload["blocking"]) + len(payload["preflight_errors"])
        return payload

    # ------------------------------------------------------------------ lease

    def lease_status(self, client_id: str = "") -> dict[str, Any]:
        return self.lease.status(client_id)

    def guard_write(self, client_id: str) -> None:
        """Raise when another page holds the editing lease.

        Only enforced for clients that identify themselves: the lease exists to
        stop two UI pages from diverging, not to lock out a script.
        """
        if not client_id:
            return
        granted, status = self.lease.claim(client_id)
        if not granted:
            holder = (status.get("holder") or {})
            raise PermissionError(
                "另一个页面正在编辑（页面标识 "
                f"{holder.get('client_id','?')}）。为避免两边保存不同配置互相覆盖，"
                "这里已改为只读。要在这里编辑，请点「接管编辑」。"
            )

    def lock_changed(self) -> None:
        """Bump the hub; every observable mutation calls this once."""
        self.hub.bump()

    def snapshot(self, client_id: str = "") -> dict[str, Any]:
        """Everything a page needs to render itself, in one payload."""
        status = self.runner.status()
        return {
            "instance_id": self.instance_id,
            "is_primary": self.is_primary,
            "lease": self.lease_status(client_id),
            "status": status,
            "bundle": self.bundle.to_dict() if self.bundle else None,
            "review": self.results_payload(),
            "schema_nodes": len(self.schema),
            "presets_rev": self.presets_rev(),
        }

    def params_payload(self, workflow_path: str = "") -> dict[str, Any]:
        """Workbench definition, saved defaults, and what is in effect now.

        The page needs all three so it can show, for every parameter, both the
        value you would set and the value that is currently winning -- that is
        what makes "the page equals the submitted graph" checkable by eye.
        """
        effective = self.last_preflight.get("effective") or {}
        if not workflow_path and isinstance(effective.get("workflow"), dict):
            workflow_path = str(effective["workflow"].get("path") or "")
        fingerprint = self.workflow_fingerprint(workflow_path)

        stored = self.workflow_params.for_fingerprint(fingerprint)
        audit = (self.runner.status().get("audit") or self.last_preflight.get("audit") or {})
        applied: dict[str, Any] = {}
        for trace in audit.get("overrides") or []:
            if trace.get("source") == "param":
                applied[trace.get("input")] = trace.get("value")

        # Task-level values, if any item carries them.
        task_values: dict[str, Any] = {}
        if self.bundle:
            for item in self.bundle.items:
                generation = item.metadata.get("generation") if isinstance(item.metadata, dict) else None
                if isinstance(generation, dict) and isinstance(generation.get("params"), dict):
                    task_values.setdefault("_assigned", 0)
                    task_values["_assigned"] = task_values["_assigned"] + 1

        return {
            **registry_payload(self.schema),
            "workflow_path": workflow_path,
            "fingerprint": fingerprint,
            "workflow_defaults": stored,
            "stale_defaults": self.workflow_params.stale_for(workflow_path, fingerprint) if workflow_path else [],
            "applied": applied,
            "task_assigned_count": task_values.get("_assigned", 0),
        }

    def assign_task_params(self, values: dict[str, Any], indexes: list[int]) -> PromptBundle:
        """Write task-scoped parameter values onto the selected items."""
        if self.bundle is None:
            raise ValueError("请先导入提示词合集")
        if not indexes:
            raise ValueError("请选择要应用的任务范围")
        for index in indexes:
            if index < 1 or index > len(self.bundle.items):
                raise ValueError(f"任务编号 {index} 超出范围")
            item = self.bundle.items[index - 1]
            metadata = dict(item.metadata)
            generation = dict(metadata.get("generation") or {})
            params = dict(generation.get("params") or {})
            for key, value in values.items():
                if value in (None, ""):
                    params.pop(key, None)
                else:
                    params[key] = value
            if params:
                generation["params"] = params
            else:
                generation.pop("params", None)
            metadata["generation"] = generation
            item.metadata = metadata
        return self.bundle

    def schema_payload(self) -> dict[str, Any]:
        return {
            "nodes": len(self.schema),
            "cached": schema_cache_path().exists(),
            "source": "ComfyUI /object_info" if self.schema else "未加载（ComfyUI 未连接且无缓存）",
        }

    def import_bundle(self, filename: str, raw: bytes, mapping: dict[str, Any] | None = None) -> tuple[PromptBundle, dict[str, Any] | None]:
        self.bundle = PromptBundleParser.parse(filename, raw, mapping=mapping)
        self.last_import = {"filename": filename, "raw": raw}
        info = PromptBundleParser.xlsx_mapping_info(raw) if pathlib.Path(filename).suffix.lower() == ".xlsx" else None
        if info is not None and mapping:
            info["selected"] = dict(mapping)
        return self.bundle, info

    def import_images(self, files: list[dict[str, Any]], mode: str = "ai_enhance") -> PromptBundle:
        if not files:
            raise ValueError("没有收到图片")
        if len(files) > 500:
            raise ValueError("单次最多导入 500 张图片")
        session_id = uuid.uuid4().hex[:12]
        input_root = self.comfy_root / "input"
        target_root = input_root / "ComfyBatch-V2" / "imports" / session_id
        target_root.mkdir(parents=True, exist_ok=True)
        modes = {
            "faithful_upscale": ("保真像素放大", "严格保持输入图片的内容、人物、服装、颜色和构图，只提高分辨率与细节清晰度。"),
            "ai_enhance": ("AI 高清重绘", "保持输入图片的主体、服装、颜色与构图一致，按所选工作流补充高质量细节。"),
            "outpaint": ("画布扩展", "保持输入图片主体和服装不变，根据目标比例自然扩展画布与周围场景。"),
        }
        mode = mode if mode in modes else "ai_enhance"
        mode_name, default_prompt = modes[mode]
        items: list[PromptItem] = []
        used_names: set[str] = set()
        for index, value in enumerate(files, 1):
            filename = str(value.get("filename") or f"image-{index:03d}.png")
            raw = bytes(value.get("raw") or b"")
            if not raw or len(raw) > 100 * 1024 * 1024:
                raise ValueError(f"图片 {filename} 为空或超过 100MB")
            suffix = pathlib.Path(filename).suffix.lower()
            if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
                raise ValueError(f"不支持的图片格式：{filename}")
            try:
                with Image.open(io.BytesIO(raw)) as image:
                    width, height = image.size
                    image.verify()
            except Exception as exc:
                raise ValueError(f"图片无法读取：{filename}") from exc
            stem = _safe_name(pathlib.Path(filename).stem, f"image-{index:03d}")
            safe_name = stem + suffix
            duplicate = 2
            while safe_name.lower() in used_names:
                safe_name = f"{stem}-{duplicate}{suffix}"
                duplicate += 1
            used_names.add(safe_name.lower())
            target = target_root / safe_name
            target.write_bytes(raw)
            relative = target.relative_to(input_root).as_posix()
            metadata = {
                "input_type": "image",
                "processing_mode": mode,
                "processing_mode_name": mode_name,
                "source_image": relative,
                "source_filename": filename,
                "source_dimensions": [width, height],
            }
            extraction = value.get("extraction")
            if isinstance(extraction, dict):
                # Provenance is intentionally additive: an extracted image is
                # still an ordinary imported image to every downstream workflow.
                metadata["extraction"] = {
                    "source_page": str(extraction.get("source_page") or ""),
                    "source_url": str(extraction.get("source_url") or ""),
                    "kind": str(extraction.get("kind") or ""),
                }
            items.append(PromptItem(
                title=_safe_name(str(value.get("title") or stem), stem),
                prompt=default_prompt,
                negative_prompt="",
                metadata=metadata,
            ))
        self.bundle = PromptBundle(f"图片合集-{session_id}", items, "images")
        self.last_import = None
        return self.bundle

    def extract_images(self, url: str, mode: str = "ai_enhance") -> tuple[PromptBundle, list[dict[str, Any]]]:
        """Fetch public page images and put them through the normal import path."""
        extracted = self.image_extractor.extract(url)
        files = [
            {
                "filename": item.filename,
                "raw": item.raw,
                "title": item.title,
                "extraction": {
                    "source_page": item.source_page,
                    "source_url": item.source_url,
                    "kind": item.kind,
                },
            }
            for item in extracted
        ]
        bundle = self.import_images(files, mode)
        details = [
            {
                "title": item.title or pathlib.Path(item.filename).stem,
                "source_url": item.source_url,
                "kind": item.kind,
                "dimensions": list(item.dimensions),
            }
            for item in extracted
        ]
        return bundle, details

    def remap_import(self, mapping: dict[str, Any]) -> tuple[PromptBundle, dict[str, Any]]:
        if not self.last_import or pathlib.Path(self.last_import["filename"]).suffix.lower() != ".xlsx":
            raise ValueError("请先导入一个 Excel 文件")
        bundle, info = self.import_bundle(self.last_import["filename"], self.last_import["raw"], mapping)
        return bundle, info or {}

    def _load_settings(self) -> dict:
        return self.settings_store.load()

    def _save_settings(self) -> None:
        self.settings_store.save(self._settings)

    def lora_profiles(self) -> dict[str, dict]:
        profiles = self._settings.get("lora_profiles") or {}
        return dict(profiles) if isinstance(profiles, dict) else {}

    def style_lora_presets(self) -> dict[str, dict]:
        presets = self._settings.get("style_lora_presets") or {}
        return dict(presets) if isinstance(presets, dict) else {}

    def presets_rev(self) -> str:
        """A cheap fingerprint of the preset library, safe to send every frame.

        Another open page must notice when this backend's preset set changes --
        but the SSE snapshot is written every time any state moves, so shipping
        all presets here would waste bandwidth. Instead ship a short digest of
        the whole library (id, name and content included, so add, rename, edit
        and delete all flip it). ``sort_keys`` makes the digest independent of
        the dict's insertion order, so a mere reorder does not look like a
        change. Presets are a handful of tiny dicts, so hashing them per frame
        is microseconds -- far cheaper than the JSON the frame already carries.
        """
        payload = self.style_lora_presets()
        tombstones = len(self._settings.get("deleted_style_lora_presets") or {})
        digest = hashlib.sha1(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()[:12]
        return f"{len(payload)}-{tombstones}-{digest}"

    @staticmethod
    def image_presets() -> list[dict]:
        return [dict(item) for item in IMAGE_PRESETS]

    @staticmethod
    def image_preset(preset_id: str) -> dict:
        preset = next((item for item in IMAGE_PRESETS if item["id"] == str(preset_id or "").strip()), None)
        if not preset:
            raise ValueError("所选图像规格已经不存在")
        return dict(preset)

    def save_style_lora_preset(self, value: dict) -> dict:
        name = str(value.get("name") or "").strip()
        if not name:
            raise ValueError("预设名称不能为空")
        preset_id = str(value.get("id") or "").strip() or uuid.uuid4().hex[:12]
        styles = []
        for item in list(value.get("styles") or [])[:4]:
            catalog = str(item.get("catalog") or "").strip()
            style_name = str(item.get("name") or "").strip()
            if catalog and style_name:
                styles.append({"catalog": catalog, "name": style_name})
        loras = []
        for item in list(value.get("loras") or []):
            lora_name = str(item.get("name") or "").strip()
            if lora_name:
                loras.append({
                    "name": lora_name,
                    "strength": max(-2.0, min(2.0, float(item.get("strength", 1)))),
                    "use_triggers": bool(item.get("use_triggers", True)),
                })
        if not styles and not loras:
            raise ValueError("预设至少需要一个风格或 LoRA")
        with self.lock:
            presets = self._settings.setdefault("style_lora_presets", {})
            duplicate = next((item for key, item in presets.items() if key != preset_id and item.get("name") == name), None)
            if duplicate:
                raise ValueError(f"已经存在同名预设：{name}")
            preset = {"id": preset_id, "name": name, "styles": styles, "loras": loras}
            presets[preset_id] = preset
            self._save_settings()
        return preset

    def assign_style_lora_preset(self, preset_id: str, indexes: list[int]) -> PromptBundle:
        if self.bundle is None:
            raise ValueError("请先导入提示词合集")
        preset_id = str(preset_id or "").strip()
        if preset_id and preset_id not in self.style_lora_presets():
            raise ValueError("所选预设已经不存在")
        total = len(self.bundle.items)
        invalid = [index for index in indexes if index < 1 or index > total]
        if invalid:
            raise ValueError(f"编号超出提示词范围 1-{total}")
        for index in sorted(set(indexes)):
            metadata = self.bundle.items[index - 1].metadata
            if preset_id:
                metadata["style_lora_preset_id"] = preset_id
            else:
                metadata.pop("style_lora_preset_id", None)
                metadata.pop("style_lora_preset_name", None)
        return self.bundle

    def assign_image_preset(self, preset_id: str, indexes: list[int]) -> PromptBundle:
        if self.bundle is None:
            raise ValueError("请先导入提示词合集")
        preset_id = str(preset_id or "").strip()
        preset = self.image_preset(preset_id) if preset_id else None
        total = len(self.bundle.items)
        invalid = [index for index in indexes if index < 1 or index > total]
        if invalid:
            raise ValueError(f"编号超出提示词范围 1-{total}")
        for index in sorted(set(indexes)):
            metadata = self.bundle.items[index - 1].metadata
            generation = dict(metadata.get("generation") or {})
            if preset:
                generation["aspect_ratio"] = preset["aspect_ratio"]
                generation["megapixels"] = preset["megapixels"]
                metadata["image_preset_id"] = preset["id"]
                metadata["image_preset_name"] = preset["name"]
            else:
                generation.pop("aspect_ratio", None)
                generation.pop("megapixels", None)
                metadata.pop("image_preset_id", None)
                metadata.pop("image_preset_name", None)
            if generation:
                metadata["generation"] = generation
            else:
                metadata.pop("generation", None)
        return self.bundle

    def delete_style_lora_preset(self, preset_id: str) -> None:
        """Delete, and record the deletion so it cannot come back.

        A hard delete was the bug: because the store used to union the preset map
        across copies, a single stale copy reinstated it. The tombstone makes the
        deletion stick no matter which copy is read.
        """
        with self.lock:
            presets = self._settings.setdefault("style_lora_presets", {})
            removed = presets.pop(preset_id, None)
            if removed is None:
                raise ValueError("预设已经不存在")
            tombstones = self._settings.setdefault("deleted_style_lora_presets", {})
            tombstones[preset_id] = {
                "deleted_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "name": str(removed.get("name") or ""),
            }
            if self.bundle:
                for item in self.bundle.items:
                    if item.metadata.get("style_lora_preset_id") == preset_id:
                        item.metadata.pop("style_lora_preset_id", None)
                        item.metadata.pop("style_lora_preset_name", None)
            self._save_settings()

    def deleted_style_lora_presets(self) -> dict[str, Any]:
        """Deletion records, so the page can offer a deliberate restore."""
        merged = self.settings_store.tombstones().get("deleted_style_lora_presets") or {}
        merged.update(self._settings.get("deleted_style_lora_presets") or {})
        return merged

    def restore_default_style_lora_presets(self) -> dict[str, Any]:
        """Re-create the preset library that shipped with the program.

        This is the *only* path that may bring a deleted preset back, and it has
        to be asked for explicitly: clearing the tombstones without restoring the
        entity would leave the deletion undone but the preset still missing.
        """
        with self.lock:
            tombstones = dict(self._settings.get("deleted_style_lora_presets") or {})
            presets = self._settings.setdefault("style_lora_presets", {})
            restored: list[str] = []
            for preset in DEFAULT_STYLE_LORA_PRESETS:
                preset_id = str(preset.get("id") or "")
                if not preset_id or preset_id not in tombstones:
                    continue
                entry = copy.deepcopy(preset)
                entry.setdefault("id", preset_id)
                presets[preset_id] = entry
                tombstones.pop(preset_id, None)
                restored.append(preset_id)
            self._settings["deleted_style_lora_presets"] = tombstones
            self._save_settings()
        return {"restored": restored, "presets": list(presets.values())}

    def _resolve_styles(self, styles: list[dict], inventory: dict) -> list[dict]:
        lookup = {
            (str(item.get("library") or ""), str(item.get("name") or "")): item
            for item in inventory.get("styles", [])
        }
        resolved = []
        for item in styles:
            key = (str(item.get("catalog") or ""), str(item.get("name") or ""))
            canonical = lookup.get(key, {})
            resolved.append({**canonical, **item, "catalog": key[0], "name": key[1]})
        return resolved

    def _resolve_loras(self, loras: list[dict]) -> list[dict]:
        profiles = self.lora_profiles()
        resolved = []
        for item in loras:
            profile = profiles.get(str(item.get("name") or ""), {})
            merged = {**profile, **item}
            if profile.get("trigger_words"):
                merged["trigger_words"] = list(profile["trigger_words"])
                merged["trigger_status"] = profile.get("trigger_status", "manual")
            resolved.append(merged)
        return resolved

    def resolve_bundle_presets(self) -> PromptBundle:
        if self.bundle is None:
            raise ValueError("请先导入提示词合集")
        inventory = self._inventory().snapshot()
        presets = self.style_lora_presets()
        items = []
        for item in self.bundle.items:
            metadata = copy.deepcopy(item.metadata)
            preset = presets.get(str(metadata.get("style_lora_preset_id") or ""))
            if preset:
                generation = dict(metadata.get("generation") or {})
                generation["styles"] = self._resolve_styles(list(preset.get("styles") or []), inventory)
                generation["loras"] = self._resolve_loras(list(preset.get("loras") or []))
                metadata["generation"] = generation
                metadata["style_lora_preset_name"] = preset["name"]
            else:
                metadata.pop("style_lora_preset_id", None)
                metadata.pop("style_lora_preset_name", None)
            items.append(PromptItem(item.title, item.prompt, metadata, item.negative_prompt))
        return PromptBundle(self.bundle.name, items, self.bundle.source_format)

    def save_lora_profile(self, value: dict) -> dict:
        name = str(value.get("name") or "").strip()
        if not name:
            raise ValueError("LoRA 名称不能为空")
        words = value.get("trigger_words") or []
        if isinstance(words, str):
            words = [part.strip() for part in words.replace("，", ",").split(",")]
        cleaned: list[str] = []
        for word in words:
            text = str(word).strip()
            if text and text not in cleaned:
                cleaned.append(text)
        profile = {
            "display_name": str(value.get("display_name") or name).strip(),
            "trigger_words": cleaned,
            "trigger_status": "manual" if cleaned else "unknown",
            "use_triggers": bool(value.get("use_triggers", True)),
        }
        with self.lock:
            profiles = self._settings.setdefault("lora_profiles", {})
            profiles[name] = profile
            self._save_settings()
        return profile

    def delete_lora_profile(self, name: str) -> None:
        """Delete one saved LoRA profile and leave a tombstone, same as presets."""
        with self.lock:
            profiles = self._settings.setdefault("lora_profiles", {})
            removed = profiles.pop(name, None)
            if removed is None:
                raise ValueError("该 LoRA 档案已经不存在")
            tombstones = self._settings.setdefault("deleted_lora_profiles", {})
            tombstones[name] = {
                "deleted_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "name": str(removed.get("name") or name),
            }
            self._save_settings()

    def configure(self, value: dict) -> None:
        with self.lock:
            self.comfy_root = pathlib.Path(value.get("comfy_root") or self.comfy_root)
            roots = value.get("workflow_roots") or [str(root) for root in self.workflow_roots]
            self.workflow_roots = [pathlib.Path(root) for root in roots if str(root).strip()]
            self.comfy_url = str(value.get("comfy_url") or self.comfy_url).rstrip("/")
            self.output_root = pathlib.Path(value.get("output_root") or self.output_root)
            if self.runner.status()["status"] not in {"running", "paused", "starting"}:
                self.runner = BatchRunner(self.comfy_root, ComfyClient(self.comfy_url))

    def inventory(self) -> dict:
        result = self._inventory().snapshot()
        self._style_thumbnail_lookup = {
            (str(item.get("library") or ""), str(item.get("name") or "")): str(item.get("thumbnail") or "")
            for item in result.get("styles", [])
            if item.get("thumbnail")
        }
        profiles = self.lora_profiles()
        for lora in result.get("loras", []):
            profile = profiles.get(str(lora.get("value") or ""))
            if profile:
                lora.update(profile)
            lora.setdefault("display_name", lora.get("name") or lora.get("value"))
        result.update({
            "comfy_url": self.comfy_url,
            "workflow_roots": [str(x) for x in self.workflow_roots],
            "output_root": str(self.output_root),
            "style_lora_presets": list(self.style_lora_presets().values()),
            "image_presets": self.image_presets(),
            #: The six purposes come from the server so the page cannot drift from
            #: the rules that actually decide whether a workflow fits.
            "purposes": purpose_catalog(),
        })
        try:
            info = self.runner.client.json("/system_stats", timeout=4)
            result["comfy_connected"] = True
            result["comfy_system"] = info
        except Exception as exc:
            result["comfy_connected"] = False
            result["comfy_error"] = str(exc)
        return result

    def style_thumbnail(self, library: str, name: str) -> tuple[str, pathlib.Path | str]:
        key = (str(library), str(name))
        thumbnail = self._style_thumbnail_lookup.get(key)
        if not thumbnail:
            rows = self._inventory().snapshot().get("styles", [])
            self._style_thumbnail_lookup = {
                (str(item.get("library") or ""), str(item.get("name") or "")): str(item.get("thumbnail") or "")
                for item in rows if item.get("thumbnail")
            }
            thumbnail = self._style_thumbnail_lookup.get(key)
        if not thumbnail:
            raise ValueError("该风格没有预览图")
        parsed = urlparse(thumbnail)
        if parsed.scheme in {"http", "https"}:
            return "url", thumbnail
        if parsed.path == "/view":
            query = parse_qs(parsed.query)
            filename = str(query.get("filename", [""])[0])
            subfolder = str(query.get("subfolder", [""])[0])
            input_root = (self.comfy_root / "input").resolve()
            candidate = (input_root / subfolder / filename).resolve()
            if not filename or not candidate.is_relative_to(input_root) or not candidate.is_file():
                raise ValueError("本地风格预览图不存在")
            return "file", candidate
        raise ValueError("不支持的风格预览地址")

    def update_bundle(self, values: list[dict]) -> PromptBundle:
        if self.bundle is None:
            raise ValueError("请先导入提示词合集")
        items: list[PromptItem] = []
        for index, value in enumerate(values, 1):
            title = str(value.get("title") or f"提示词 {index:03d}").strip()
            prompt = str(value.get("prompt") or "").strip()
            negative_prompt = str(value.get("negative_prompt") or "").strip()
            if not prompt:
                raise ValueError(f"第 {index} 条提示词为空")
            repeat = max(1, min(100, int(value.get("repeat") or 1)))
            metadata = dict(value.get("metadata") or {})
            for copy_index in range(repeat):
                copy_title = title if repeat == 1 else f"{title}-{copy_index + 1:02d}"
                items.append(PromptItem(copy_title, prompt, metadata, negative_prompt))
        if not items:
            raise ValueError("至少保留一条提示词")
        self.bundle = PromptBundle(self.bundle.name, items, self.bundle.source_format)
        return self.bundle

    def prepare_config(self, config: BatchConfig) -> BatchConfig:
        inventory = self.inventory()
        style_lookup = {
            (str(item.get("library") or ""), str(item.get("name") or "")): item
            for item in inventory.get("styles", [])
        }
        requested = config.styles or ([{"catalog": config.style_library, "name": config.style_name}] if config.style_name else [])
        resolved: list[dict] = []
        for item in requested:
            key = (str(item.get("catalog") or ""), str(item.get("name") or ""))
            canonical = style_lookup.get(key, {})
            resolved.append({**canonical, **item, "catalog": key[0], "name": key[1]})
        config.styles = resolved

        profiles = self.lora_profiles()
        config.loras = [
            {**profiles.get(str(item.get("name") or ""), {}), **item}
            for item in config.loras
        ]

        # Saved replacements are additive and never overwrite an explicit choice
        # made for this batch.
        fingerprint = self.workflow_fingerprint(config.workflow_path)
        if fingerprint:
            # Workflow defaults are the floor; an explicit batch value wins.
            defaults = self.workflow_params.for_fingerprint(fingerprint)
            if defaults:
                config.params = {**defaults, **(config.params or {})}
            saved = self.resource_rules.for_fingerprint(fingerprint)
            merged = {node: dict(values) for node, values in saved.items()}
            for node, values in (config.resource_overrides or {}).items():
                merged.setdefault(str(node), {}).update(values)
            config.resource_overrides = merged
        return config

    def workflow_fingerprint(self, workflow_path: str) -> str:
        """Structural fingerprint of the workflow, or ``""``.

        Structural, not a whole-file hash: a whole-file hash changes when a node
        is merely dragged on the canvas, which would silently disable a saved
        replacement rule. See ``structural_fingerprint``.
        """
        if not workflow_path:
            return ""
        try:
            payload = json.loads(pathlib.Path(workflow_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return ""
        return structural_fingerprint(payload)

    def model_directories(self) -> dict[str, str]:
        """Model folders that exist, for the "open the model folder" action."""
        models = self.comfy_root / "models"
        wanted = ("upscale_models", "loras", "checkpoints", "diffusion_models", "vae", "text_encoders", "unet")
        found: dict[str, str] = {}
        for name in wanted:
            candidate = models / name
            if candidate.is_dir():
                found[name] = str(candidate)
        return found

    def validate_start(self, config: BatchConfig) -> dict:
        try:
            self.runner.client.json("/system_stats", timeout=4)
        except Exception as exc:
            raise ValueError("ComfyUI 未启动或无法连接，请先启动 ComfyUI 后再生成") from exc
        inventory = self._inventory()
        compatible = inventory.compatible_models(config.workflow_path, config.workflow_variant)
        # Compare by resolved file name, not by raw string: the inventory reports
        # a model relative to its model root ("Krea2/krea2_turbo.safetensors")
        # while a loader node may name the same file bare, and the plain ``==``
        # that used to be here rejected models the user actually has installed.
        wanted = inventory.model_key(config.model)
        if not wanted or not any(inventory.model_key(item.get("value")) == wanted for item in compatible):
            families = sorted({str(item.get("family")) for item in compatible if item.get("family")})
            family_text = "、".join(families) or "该工作流原有模型类型"
            raise ValueError(f"模型“{config.model}”与所选工作流不兼容；请选择：{family_text}")
        adapter = Krea2WorkflowAdapter.from_path(config.workflow_path, self.ensure_schema())
        task_negative = next((item.negative_prompt for item in (self.bundle.items if self.bundle else []) if item.negative_prompt), "")
        preset_negative = ""
        if self.bundle:
            try:
                resolved_bundle = self.resolve_bundle_presets()
                preset_negative = next((
                    str(style.get("negative_prompt") or "").strip()
                    for item in resolved_bundle.items
                    for style in ((item.metadata.get("generation") or {}).get("styles") or [])
                    if str(style.get("negative_prompt") or "").strip()
                ), "")
            except Exception:
                preset_negative = ""
        negative_probe = task_negative or preset_negative
        capability_config = copy.deepcopy(config)
        if self.bundle:
            try:
                resolved_bundle = self.resolve_bundle_presets()
                first_generation = next((
                    item.metadata.get("generation")
                    for item in resolved_bundle.items
                    if isinstance(item.metadata, dict) and isinstance(item.metadata.get("generation"), dict)
                ), None)
                if isinstance(first_generation, dict):
                    capability_config.model = str(first_generation.get("model") or capability_config.model)
                    capability_config.style_library = str(first_generation.get("style_library") or capability_config.style_library)
                    capability_config.style_name = str(first_generation.get("style_name") or capability_config.style_name)
                    if isinstance(first_generation.get("styles"), list):
                        capability_config.styles = copy.deepcopy(first_generation["styles"])
                    if isinstance(first_generation.get("loras"), list):
                        capability_config.loras = copy.deepcopy(first_generation["loras"])
                    capability_config.aspect_ratio = str(first_generation.get("aspect_ratio") or capability_config.aspect_ratio)
                    capability_config.megapixels = max(0.25, min(4.0, float(first_generation.get("megapixels") or capability_config.megapixels)))
            except Exception:
                pass
        if negative_probe and not capability_config.negative_prompt:
            capability_config.negative_prompt = negative_probe
        source_image = next((
            str(item.metadata.get("source_image") or "")
            for item in (self.bundle.items if self.bundle else [])
            if isinstance(item.metadata, dict) and item.metadata.get("source_image")
        ), "")
        report = adapter.capabilities(capability_config, source_image=source_image)
        if report["required_injected_nodes"]:
            try:
                object_info = self.runner.client.json("/object_info", timeout=10)
                if isinstance(object_info, dict) and any(isinstance(value, dict) and "input" in value for value in object_info.values()):
                    missing = [name for name in report["required_injected_nodes"] if name not in object_info]
                    if missing:
                        report["errors"].append("ComfyUI 缺少注入节点：" + "、".join(missing))
                        report["ready"] = False
            except Exception as exc:
                report["warnings"].append("无法核查 ComfyUI 节点清单：" + str(exc))
        if not report["ready"]:
            raise ValueError("；".join(report["errors"]))
        adapter.build("工作流连接与提示词检查", capability_config, "ComfyBatch-V2/preflight", task_negative=negative_probe, source_image=source_image)

        # Stale rules would otherwise look like "my replacement stopped working".
        stale = self.resource_rules.stale_for(
            capability_config.workflow_path, self.workflow_fingerprint(capability_config.workflow_path)
        )
        if stale:
            names = "、".join(f"节点 {rule.get('node_id')} 的 {rule.get('input_name')}" for rule in stale[:4])
            report["warnings"].append(
                f"这个工作流文件改动过了，之前保存的 {len(stale)} 条替换规则已暂停生效（{names}）。"
                f"如果节点编号仍然对应，可在「生效检查与依赖」里点「沿用旧替换规则」重新启用。"
            )

        # Fourth preflight layer: audit the graph that would actually be
        # submitted, so a missing resource or an out-of-range value is reported
        # here instead of arriving later as an opaque HTTP 400 repeated 45 times.
        audit = adapter.audit(capability_config, branch=str(capability_config.workflow_variant or ""))
        blocking = list(audit.get("blocking") or []) + list(audit.get("binding_blocking") or [])
        report["audit"] = audit
        report["blocking"] = blocking
        report["upscale_candidates"] = [
            item.get("value") for item in self.inventory().get("upscale_models", [])
        ]
        if blocking:
            report["ready"] = False
            report["errors"].extend(item.get("detail") or item.get("title") or "" for item in blocking)
        # Recorded even on failure, so the page can render the blocking problems
        # with their candidate lists and fixes.
        self.last_preflight = {"effective": report, "audit": audit}
        if blocking:
            raise ValueError("；".join(item.get("detail") or item.get("title") or "预检未通过" for item in blocking))
        return report


APP = Application()


class Handler(BaseHTTPRequestHandler):
    server_version = f"ComfyBatchV2/{APP_VERSION}"

    def log_message(self, fmt: str, *args) -> None:
        print("[ComfyBatch] " + fmt % args)

    def _json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _stream_events(self, parsed) -> None:
        """Server-sent events: push a snapshot whenever server state changes.

        Replaces 650 ms polling as the primary channel. A closed tab raises
        BrokenPipeError on the next write, which ends the thread and frees the
        slot; the client count is capped so runaway reconnects cannot exhaust
        threads.
        """
        query = parse_qs(parsed.query)
        client_id = query.get("client_id", [""])[0]
        if not APP.acquire_stream_slot():
            self._json({"ok": False, "error": "事件流连接过多，请关闭多余页面后重试"}, 429)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        version = -1
        try:
            while True:
                if version >= 0 and not APP.wait_for_change(version):
                    # A closed tab can only be detected by writing, so a comment is
                    # written every slice rather than every heartbeat interval: it
                    # costs one tiny write per second per page and lets the slot be
                    # reclaimed within about a second instead of after 15.
                    self.wfile.write(HEARTBEAT_BYTES)
                    self.wfile.flush()
                    version = APP.hub.version
                    continue
                version = APP.hub.version
                payload = json.dumps(APP.snapshot(client_id), ensure_ascii=False)
                self.wfile.write(sse_frame("snapshot", payload))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, OSError):
            pass
        finally:
            APP.release_stream_slot()

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length).decode("utf-8")) if length else {}

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path in STATIC_FILES:
                name, content_type = STATIC_FILES[parsed.path]
                body = STATIC_ASSETS.get(parsed.path) or resource_path(name).read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                # The page and its assets must always match; a cached app.js
                # against a fresh page is a class of bug worth ruling out.
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
            elif parsed.path == "/api/inventory":
                self._json({"ok": True, "inventory": APP.inventory()})
            elif parsed.path == "/api/lora-profiles":
                self._json({"ok": True, "profiles": APP.lora_profiles()})
            elif parsed.path == "/api/deleted-presets":
                self._json({"ok": True, "deleted": APP.deleted_style_lora_presets(),
                            "shipped": len(DEFAULT_STYLE_LORA_PRESETS)})
            elif parsed.path == "/api/style-lora-presets":
                self._json({"ok": True, "presets": list(APP.style_lora_presets().values())})
            elif parsed.path == "/api/style-thumbnail":
                query = parse_qs(parsed.query)
                kind, value = APP.style_thumbnail(query.get("library", [""])[0], query.get("name", [""])[0])
                if kind == "url":
                    self.send_response(302)
                    self.send_header("Location", str(value))
                    self.end_headers()
                else:
                    body = pathlib.Path(value).read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", mimetypes.guess_type(str(value))[0] or "image/webp")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "public, max-age=86400")
                    self.end_headers()
                    self.wfile.write(body)
            elif parsed.path == "/api/status":
                # ``review`` carries the full review payload, not just the counts:
                # the page polls only this endpoint, so it must be able to render
                # the result board from it without a second request. Returning
                # only a summary here left the board permanently empty.
                query = parse_qs(parsed.query)
                self._json({
                    "ok": True,
                    "status": APP.runner.status(),
                    "bundle": APP.bundle.to_dict() if APP.bundle else None,
                    "review": APP.results_payload(),
                    "lease": APP.lease_status(query.get("client_id", [""])[0]),
                    "activated_at": APP.hub.activated_at,
                    "instance_id": APP.instance_id,
                    "is_primary": APP.is_primary,
                })
            elif parsed.path == "/api/results":
                self._json({"ok": True, "review": APP.results_payload()})
            elif parsed.path == "/api/inspect":
                self._json({"ok": True, "inspect": APP.inspect_payload()})
            elif parsed.path == "/api/events":
                self._stream_events(parsed)
            elif parsed.path == "/api/ping":
                # Read the version from one place; a hardcoded copy here drifted
                # out of step the moment the version was bumped.
                self._json({"ok": True, "instance_id": APP.instance_id, "app": "ComfyBatch",
                            "version": APP_VERSION, "is_primary": APP.is_primary})
            elif parsed.path == "/api/lease":
                query = parse_qs(parsed.query)
                self._json({"ok": True, "lease": APP.lease_status(query.get("client_id", [""])[0])})
            elif parsed.path == "/api/params":
                query = parse_qs(parsed.query)
                self._json({"ok": True, "params": APP.params_payload(query.get("workflow_path", [""])[0])})
            elif parsed.path == "/api/schema":
                self._json({"ok": True, "schema": APP.schema_payload()})
            elif parsed.path == "/api/image":
                query = parse_qs(parsed.query)
                requested = pathlib.Path(query.get("path", [""])[0]).resolve()
                allowed = {pathlib.Path(row["copied_to"]).resolve() for row in APP.runner.status().get("results", []) if row.get("copied_to")}
                if requested not in allowed or not requested.is_file():
                    raise ValueError("图片不属于当前批次")
                body = requested.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", mimetypes.guess_type(str(requested))[0] or "application/octet-stream")
                self.send_header("Content-Length", str(len(body)))
                # The file behind a path never changes, so a viewer reopen is a
                # cache hit instead of a re-download of the full-resolution PNG.
                self.send_header("Cache-Control", "private, max-age=604800")
                self.end_headers()
                self.wfile.write(body)
            elif parsed.path == "/api/preview":
                # Dedicated preview endpoint for anything rendering a grid of
                # thumbnails. Kept separate from ``/api/image`` on purpose: that
                # one must keep serving the untouched original for review and
                # download, while this one may downscale and cache aggressively.
                query = parse_qs(parsed.query)
                requested = pathlib.Path(query.get("path", [""])[0]).resolve()
                allowed = {pathlib.Path(row["copied_to"]).resolve() for row in APP.runner.status().get("results", []) if row.get("copied_to")}
                if requested not in allowed or not requested.is_file():
                    raise ValueError("图片不属于当前批次")
                try:
                    size = max(64, min(2048, int(query.get("size", [THUMBNAIL_SIZE])[0])))
                except ValueError:
                    size = THUMBNAIL_SIZE
                preview = APP.reviews.ensure_thumbnail(requested, size=size)
                body = preview.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", mimetypes.guess_type(str(preview))[0] or "image/jpeg")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "private, max-age=604800")
                self.end_headers()
                self.wfile.write(body)
            elif parsed.path == "/api/source-image":
                requested = pathlib.Path(parse_qs(parsed.query).get("path", [""])[0])
                input_root = (APP.comfy_root / "input").resolve()
                candidate = (input_root / requested).resolve()
                allowed = {
                    (input_root / str(item.metadata.get("source_image") or "")).resolve()
                    for item in (APP.bundle.items if APP.bundle else [])
                    if isinstance(item.metadata, dict) and item.metadata.get("source_image")
                }
                if candidate not in allowed or not candidate.is_relative_to(input_root) or not candidate.is_file():
                    raise ValueError("图片不属于当前导入批次")
                body = candidate.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", mimetypes.guess_type(str(candidate))[0] or "application/octet-stream")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "private, max-age=3600")
                self.end_headers()
                self.wfile.write(body)
            else:
                self._json({"ok": False, "error": "未找到接口"}, 404)
        except Exception as exc:
            self._json({"ok": False, "error": str(exc)}, 400)

    def do_POST(self) -> None:
        """Lease guard and change notification wrapped around the route dispatch.

        Keeping the guard here rather than in each branch means a new route
        cannot accidentally ship without it.
        """
        client_id = ""
        needs_lease = False
        try:
            value = self._read_json()
            client_id = str(value.get("client_id") or self.headers.get("X-ComfyBatch-Client") or "")
            needs_lease = self.path in WRITE_ROUTES
            if needs_lease:
                APP.guard_write(client_id)
            try:
                self._route_post(value)
            finally:
                # Notifying after the mutation is what makes the pushed snapshot
                # carry the new state rather than the previous one.
                if needs_lease:
                    APP.lock_changed()
        except PermissionError as exc:
            self._json({"ok": False, "error": str(exc), "lease": APP.lease_status(client_id)}, 409)
        except Exception as exc:
            self._json({"ok": False, "error": str(exc)}, 400)

    def _route_post(self, value: dict) -> None:
            if self.path == "/api/configure":
                APP.configure(value)
                self._json({"ok": True, "inventory": APP.inventory()})
            elif self.path == "/api/import":
                raw = base64.b64decode(value["base64"])
                bundle, mapping = APP.import_bundle(value["filename"], raw)
                self._json({"ok": True, "bundle": bundle.to_dict(), "mapping": mapping})
            elif self.path == "/api/import-images":
                files = [
                    {"filename": str(item.get("filename") or ""), "raw": base64.b64decode(item.get("base64") or "")}
                    for item in list(value.get("files") or [])
                ]
                bundle = APP.import_images(files, str(value.get("mode") or "ai_enhance"))
                self._json({"ok": True, "bundle": bundle.to_dict(), "mapping": None})
            elif self.path == "/api/extract-images":
                bundle, extracted = APP.extract_images(
                    str(value.get("url") or ""), str(value.get("mode") or "ai_enhance")
                )
                self._json({"ok": True, "bundle": bundle.to_dict(), "extracted": extracted, "mapping": None})
            elif self.path == "/api/remap-import":
                bundle, mapping = APP.remap_import(dict(value.get("mapping") or {}))
                self._json({"ok": True, "bundle": bundle.to_dict(), "mapping": mapping})
            elif self.path == "/api/start":
                if APP.bundle is None:
                    raise ValueError("请先导入提示词合集")
                config = APP.prepare_config(BatchConfig.from_dict(value))
                if not config.output_dir:
                    config.output_dir = str(APP.output_root)
                try:
                    report = APP.validate_start(config)
                except ValueError as exc:
                    self._json({
                        "ok": False,
                        "error": str(exc),
                        "preflight": APP.last_preflight.get("effective") or {},
                        **APP.inspect_payload(),
                    }, 400)
                    return
                pathlib.Path(config.output_dir).mkdir(parents=True, exist_ok=True)
                state = APP.runner.start(APP.resolve_bundle_presets(), config)
                self._json({"ok": True, "status": state, "preflight": report})
            elif self.path == "/api/preflight":
                try:
                    # ``prepare_config`` is inside the try on purpose: it resolves
                    # styles, LoRAs and the model, so it is one of the ways a check
                    # is blocked. Raised outside, its failure reached the page
                    # without the structured context -- no problems, no reason.
                    config = APP.prepare_config(BatchConfig.from_dict(value))
                    report = APP.validate_start(config)
                except ValueError as exc:
                    # A blocked preflight must still carry the structured context:
                    # the blocking problems with their candidate lists, the
                    # warnings (e.g. "your saved replacement rule went stale"),
                    # and the stale rules themselves. Returning only a message
                    # left the page unable to explain what happened.
                    self._json({
                        "ok": False,
                        "error": str(exc),
                        "preflight": APP.last_preflight.get("effective") or {},
                        **APP.blocked_preflight_payload(str(exc)),
                    }, 400)
                    return
                # Same context on success, so the page always has one shape to read.
                self._json({"ok": True, "preflight": report, **APP.inspect_payload()})
            elif self.path == "/api/update-bundle":
                bundle = APP.update_bundle(list(value.get("items") or []))
                self._json({"ok": True, "bundle": bundle.to_dict()})
            elif self.path == "/api/save-lora-profile":
                profile = APP.save_lora_profile(value)
                self._json({"ok": True, "profile": profile})
            elif self.path == "/api/delete-lora-profile":
                APP.delete_lora_profile(str(value.get("name") or ""))
                self._json({"ok": True, "profiles": APP.lora_profiles()})
            elif self.path == "/api/save-style-lora-preset":
                preset = APP.save_style_lora_preset(value)
                self._json({"ok": True, "preset": preset, "presets": list(APP.style_lora_presets().values())})
            elif self.path == "/api/delete-style-lora-preset":
                APP.delete_style_lora_preset(str(value.get("id") or ""))
                self._json({"ok": True, "presets": list(APP.style_lora_presets().values()), "bundle": APP.bundle.to_dict() if APP.bundle else None})
            elif self.path == "/api/restore-default-presets":
                # The only path that may reinstate a deleted preset, and it has to
                # be asked for. Tombstones left in place would make this a no-op.
                result = APP.restore_default_style_lora_presets()
                APP.lock_changed()
                self._json({"ok": True, "restored": result["restored"],
                            "presets": result["presets"],
                            "deleted": APP.deleted_style_lora_presets()})
            elif self.path == "/api/assign-style-lora-preset":
                indexes = [int(index) for index in list(value.get("indexes") or [])]
                if str(value.get("range") or "").strip():
                    if APP.bundle is None:
                        raise ValueError("请先导入提示词合集")
                    indexes.extend(parse_prompt_indexes(str(value["range"]), len(APP.bundle.items)))
                bundle = APP.assign_style_lora_preset(str(value.get("preset_id") or ""), indexes)
                self._json({"ok": True, "bundle": bundle.to_dict()})
            elif self.path == "/api/assign-image-preset":
                indexes = [int(index) for index in list(value.get("indexes") or [])]
                if str(value.get("range") or "").strip():
                    if APP.bundle is None:
                        raise ValueError("请先导入提示词合集")
                    indexes.extend(parse_prompt_indexes(str(value["range"]), len(APP.bundle.items)))
                bundle = APP.assign_image_preset(str(value.get("preset_id") or ""), indexes)
                self._json({"ok": True, "bundle": bundle.to_dict()})
            elif self.path == "/api/pause":
                self._json({"ok": True, "status": APP.runner.pause()})
            elif self.path == "/api/resume":
                self._json({"ok": True, "status": APP.runner.resume()})
            elif self.path == "/api/cancel":
                self._json({"ok": True, "status": APP.runner.cancel()})
            elif self.path == "/api/review/confirm":
                indexes = [int(item) for item in (value.get("indexes") or [])]
                if not indexes and value.get("index") is not None:
                    indexes = [int(value["index"])]
                if not indexes:
                    raise ValueError("请选择要确认的任务编号")
                APP.reviews.confirm(
                    APP.current_run_id(), indexes, str(value.get("status") or "已通过"), str(value.get("note") or "")
                )
                self._json({"ok": True, "review": APP.results_payload()})
            elif self.path == "/api/review/note":
                index = int(value.get("index") or 0)
                if not index:
                    raise ValueError("请提供任务编号")
                APP.reviews.confirm(APP.current_run_id(), [index], str(value.get("status") or "需重做"), str(value.get("note") or ""))
                self._json({"ok": True, "review": APP.results_payload()})
            elif self.path == "/api/review/redo":
                index = int(value.get("index") or 0)
                if not index:
                    raise ValueError("请提供要重做的任务编号")
                mode = str(value.get("mode") or "new_seed")
                prompt = str(value.get("prompt") or "").strip()
                if mode == "edited_prompt" and not prompt:
                    raise ValueError("改提示词重做必须提供修改后的提示词")
                run_id = APP.current_run_id()
                if run_id:
                    APP.reviews.confirm(run_id, [index], "需重做", str(value.get("note") or ""))
                raw_seed = value.get("seed")
                explicit_seed = int(raw_seed) if raw_seed not in (None, "") else None
                status = APP.runner.redo(
                    index, mode,
                    prompt=prompt,
                    note=str(value.get("note") or ""),
                    seed=explicit_seed,
                )
                self._json({"ok": True, "status": status, "mode": mode, "available_modes": list(REDO_MODES)})
            elif self.path == "/api/review/redo-batch":
                indexes = [int(item) for item in (value.get("indexes") or [])]
                if not indexes:
                    raise ValueError("请提供要重做的任务编号")
                mode = str(value.get("mode") or "new_seed")
                if mode == "edited_prompt":
                    raise ValueError("批量重做不支持「改提示词」，请逐张打回")
                raw_seed = value.get("seed")
                explicit_seed = int(raw_seed) if raw_seed not in (None, "") else None
                run_id = APP.current_run_id()
                if run_id:
                    # 与单张 redo 一致：先把全部 index 标记「需重做」并写入备注。
                    APP.reviews.confirm(run_id, indexes, "需重做", str(value.get("note") or ""))
                status = APP.runner.redo_many(indexes, mode, note=str(value.get("note") or ""), seed=explicit_seed)
                self._json({"ok": True, "status": status, "count": len(set(indexes)), "mode": mode})
            elif self.path == "/api/resource/apply":
                fingerprint = APP.workflow_fingerprint(str(value.get("workflow_path") or ""))
                node_id = str(value.get("node_id") or "").strip()
                input_name = str(value.get("input_name") or "").strip()
                replacement = str(value.get("value") or "").strip()
                if not node_id or not input_name or not replacement:
                    raise ValueError("需要提供节点编号、参数名与替换值")
                if not fingerprint:
                    raise ValueError("无法识别当前工作流，替换规则需要绑定到具体工作流指纹")
                scope = str(value.get("scope") or "batch")
                if scope == "permanent":
                    APP.resource_rules.remember(
                        fingerprint, node_id, input_name, replacement,
                        node_type=str(value.get("node_type") or ""),
                        note=str(value.get("note") or ""),
                        workflow_path=str(value.get("workflow_path") or ""),
                    )
                self._json({
                    "ok": True,
                    "scope": scope,
                    "fingerprint": fingerprint,
                    "rules": APP.resource_rules.rules_for_fingerprint(fingerprint),
                })
            elif self.path == "/api/resource/forget":
                fingerprint = APP.workflow_fingerprint(str(value.get("workflow_path") or ""))
                APP.resource_rules.forget(fingerprint, str(value.get("node_id") or ""), str(value.get("input_name") or ""))
                self._json({"ok": True, "rules": APP.resource_rules.rules_for_fingerprint(fingerprint)})
            elif self.path == "/api/lease/claim":
                client_id = str(value.get("client_id") or "")
                if not client_id:
                    raise ValueError("缺少页面标识")
                granted, status = APP.lease.claim(
                    client_id, str(value.get("label") or ""), force=bool(value.get("force"))
                )
                APP.lock_changed()
                self._json({"ok": True, "granted": granted, "lease": status})
            elif self.path == "/api/lease/renew":
                client_id = str(value.get("client_id") or "")
                renewed = APP.lease.renew(client_id)
                # A page that lost its lease must be told, not left thinking it can write.
                self._json({"ok": True, "renewed": renewed, "lease": APP.lease_status(client_id)})
            elif self.path == "/api/lease/release":
                client_id = str(value.get("client_id") or "")
                APP.lease.release(client_id)
                APP.lock_changed()
                self._json({"ok": True, "lease": APP.lease_status("")})
            elif self.path == "/api/activate":
                # A second launch asked this instance to come forward. Every page
                # reacts by jumping to the running task, so "activate the existing
                # window" is meaningful even before there is an embedded window.
                APP.hub.mark_activated()
                self._json({"ok": True, "status": APP.runner.status(), "instance_id": APP.instance_id})
            elif self.path == "/api/params/apply":
                workflow_path = str(value.get("workflow_path") or "")
                fingerprint = APP.workflow_fingerprint(workflow_path)
                raw = dict(value.get("params") or {})
                scope = str(value.get("scope") or "batch")
                indexes = [int(item) for item in (value.get("indexes") or [])]
                if not raw:
                    raise ValueError("没有要应用的参数")
                # Validate first and persist only what passed. Storing the raw
                # mapping would persist a rejected value, so a typo would come
                # back on the next run and look like it silently did nothing.
                valid, problems = resolve_params(raw, APP.schema)
                blocking = [problem for problem in problems if problem.workflow_level]
                cleared = [key for key, item in raw.items() if item in (None, "")]
                if cleared and scope == "workflow":
                    APP.workflow_params.forget(fingerprint, cleared)
                if scope == "workflow":
                    if not fingerprint:
                        raise ValueError("无法识别当前工作流，工作流默认值需要绑定到具体工作流")
                    if valid:
                        APP.workflow_params.remember(fingerprint, workflow_path, valid)
                elif scope == "task":
                    if APP.bundle is None:
                        raise ValueError("请先导入提示词合集")
                    if valid or cleared:
                        APP.assign_task_params({**valid, **{key: "" for key in cleared}}, indexes)
                elif scope == "batch" and value.get("remember"):
                    # 方案A(docs/15 §2)「记住这次的参数」: an explicit opt-in that
                    # equals the workflow-scope remember() -- batch stays batch-only
                    # unless the user checked the switch. Cleared keys are forgotten
                    # from the saved defaults, mirroring scope="workflow".
                    if not fingerprint:
                        raise ValueError("无法识别当前工作流，记住参数需要绑定到具体工作流")
                    if cleared:
                        APP.workflow_params.forget(fingerprint, cleared)
                    if valid:
                        APP.workflow_params.remember(fingerprint, workflow_path, valid)
                # Pass the path explicitly: params_payload must report the same
                # fingerprint the store just wrote to, not re-derive it from the
                # last preflight (which may be a different workflow entirely).
                self._json({
                    "ok": True,
                    "scope": scope,
                    "fingerprint": fingerprint,
                    "problems": [problem.to_dict() for problem in problems],
                    "params": APP.params_payload(workflow_path),
                    "bundle": APP.bundle.to_dict() if APP.bundle else None,
                    "blocking": [problem.to_dict() for problem in blocking],
                })
            elif self.path == "/api/params/clear":
                workflow_path = str(value.get("workflow_path") or "")
                fingerprint = APP.workflow_fingerprint(workflow_path)
                keys = [str(key) for key in (value.get("keys") or [])]
                APP.workflow_params.forget(fingerprint, keys or None)
                self._json({"ok": True, "params": APP.params_payload(workflow_path)})
            elif self.path == "/api/resource/reapply":
                workflow_path = str(value.get("workflow_path") or "")
                if not workflow_path:
                    raise ValueError("需要提供工作流路径")
                fingerprint = APP.workflow_fingerprint(workflow_path)
                APP.resource_rules.reapply(workflow_path, fingerprint)
                self._json({"ok": True, "fingerprint": fingerprint,
                            "rules": APP.resource_rules.rules_for_fingerprint(fingerprint)})
            elif self.path == "/api/resource/rules":
                fingerprint = APP.workflow_fingerprint(str(value.get("workflow_path") or ""))
                self._json({"ok": True, "fingerprint": fingerprint, "rules": APP.resource_rules.rules_for_fingerprint(fingerprint)})
            elif self.path == "/api/open-folder":
                if str(value.get("kind") or "") == "output":
                    raw_path = str(value.get("path") or "").strip()
                    if not raw_path:
                        raise ValueError("没有提供输出目录")
                    # 绝对路径校验必须在 resolve() 之前：resolve 会把相对路径
                    # 补全成「cwd/相对路径」，校验放在后面就成了死代码。
                    directory = pathlib.Path(raw_path).expanduser()
                    if not directory.is_absolute():
                        raise ValueError("请提供绝对路径的输出目录")
                    directory = directory.resolve()
                    if not directory.is_dir():
                        # 与 /api/start 的自动建目录行为一致：打开前顺手补建缺失的输出目录。
                        try:
                            directory.mkdir(parents=True, exist_ok=True)
                        except OSError as exc:
                            raise ValueError(f"输出目录创建失败：{exc}") from exc
                    os.startfile(str(directory))  # noqa: S606 - deliberate, user-requested
                    self._json({"ok": True, "folder": str(directory)})
                    return
                name = str(value.get("folder") or "")
                directory = APP.model_directories().get(name)
                if not directory:
                    raise ValueError(f"没有找到模型目录：{name}")
                # Explicitly user-initiated: this only opens a folder in Explorer.
                os.startfile(directory)  # noqa: S606 - deliberate, user-requested
                self._json({"ok": True, "folder": directory})
            elif self.path == "/api/reload-schema":
                registry = APP.refresh_schema()
                self._json({"ok": True, "schema": APP.schema_payload(), "nodes": len(registry)})
            else:
                self._json({"ok": False, "error": "未找到接口"}, 404)


def instance_name() -> str:
    """Namespace for the single-instance lock.

    Always "default" for the shipped program. A different name lets a developer
    run a second build side by side without fighting over the same lock, which is
    also how the test suite stays independent.
    """
    return os.environ.get("COMFYBATCH_INSTANCE_NAME", "default").strip() or "default"


def instance_mutex_name() -> str:
    name = instance_name()
    return INSTANCE_MUTEX_NAME if name == "default" else f"{INSTANCE_MUTEX_NAME}-{name}"


def instance_record_path() -> pathlib.Path:
    """Where the running instance records how to reach it.

    Deliberately **not** inside ``--data-dir``: the mutex is per user regardless of
    which data directory a launch uses, so the record has to live somewhere that
    every launch can find. Keeping it in the data directory meant a launch with a
    different ``--data-dir`` was blocked by the mutex yet could not locate the
    running instance, leaving no way forward.
    """
    local_data = pathlib.Path(os.environ.get("LOCALAPPDATA") or pathlib.Path.home() / "AppData" / "Local")
    name = instance_name()
    filename = "instance.json" if name == "default" else f"instance-{name}.json"
    return local_data / "ComfyBatch-S" / filename


def write_instance_file(*, port: int, url: str, instance_id: str) -> None:
    try:
        path = instance_record_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps({
            "pid": os.getpid(), "port": port, "url": url,
            "instance_id": instance_id, "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
    except OSError:
        pass


def clear_instance_file() -> None:
    try:
        instance_record_path().unlink(missing_ok=True)
    except OSError:
        pass


def read_instance_file() -> dict[str, Any]:
    try:
        payload = json.loads(instance_record_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def activate_existing_instance(existing: dict[str, Any]) -> bool:
    """Ask the running instance to bring itself forward.

    Verifies the instance id before acting, so a stale file left by a crashed
    process cannot make us talk to some unrelated program on the same port.
    """
    url = str(existing.get("url") or "")
    expected = str(existing.get("instance_id") or "")
    if not url:
        return False
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(url.rstrip("/") + "/api/ping", timeout=4) as response:
            info = json.loads(response.read().decode("utf-8"))
        if not info.get("ok") or str(info.get("instance_id") or "") != expected:
            return False
        request = urllib_request.Request(
            url.rstrip("/") + "/api/activate", data=b"{}", method="POST",
            headers={"Content-Type": "application/json"},
        )
        with opener.open(request, timeout=5) as response:
            response.read()
        return True
    except Exception:  # noqa: BLE001 - unreachable is a valid answer
        return False


def activate_and_show_existing(existing: dict[str, Any], *, open_browser: bool) -> bool:
    """Activate the primary instance and make its page visible when requested."""
    if not activate_existing_instance(existing):
        return False
    if open_browser:
        url = str(existing.get("url") or "")
        if url:
            webbrowser.open(url)
    return True


def main() -> None:
    global APP
    parser = argparse.ArgumentParser(description="ComfyBatch V2")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8790)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument(
        "--data-dir",
        default="",
        help="把设置、审核记录与节点定义缓存写到该目录，而不是 %%LOCALAPPDATA%%\\ComfyBatch-S。"
             "开发与联调时用它隔离，避免覆盖用户已有的预设与 LoRA 触发词。",
    )
    parser.add_argument("--comfy-url", default="", help="覆盖 ComfyUI 地址，默认 http://127.0.0.1:8188")
    parser.add_argument(
        "--instance-name",
        default="",
        help="单实例锁的命名空间。默认 default；开发时用别的名字可与正式实例并行运行。",
    )
    parser.add_argument(
        "--force-new-instance",
        action="store_true",
        help="即使检测到已有实例也强制启动一个新的（已有实例无响应时使用）。",
    )
    args = parser.parse_args()

    # Must be set before Application() is constructed below.
    if args.data_dir:
        os.environ["COMFYBATCH_DATA_DIR"] = str(pathlib.Path(args.data_dir).resolve())
    if args.instance_name:
        os.environ["COMFYBATCH_INSTANCE_NAME"] = args.instance_name
    if args.data_dir or args.comfy_url:
        APP = Application()
        if args.comfy_url:
            APP.comfy_url = args.comfy_url
            APP.runner = BatchRunner(APP.comfy_root, ComfyClient(args.comfy_url))
    if args.data_dir:
        print(f"数据目录：{APP.settings_path.parent}")

    STATIC_ASSETS.update(load_static_assets())
    missing = [name for route, (name, _ct) in STATIC_FILES.items() if not STATIC_ASSETS.get(route)]
    if missing:
        raise SystemExit("缺少前端文件：" + "、".join(missing))

    # Single instance: a named mutex makes the check atomic, so two launches
    # racing each other cannot both believe they are first. The loser does not
    # start a second backend -- it asks the running one to come forward.
    lock = InstanceLock(instance_mutex_name())
    if args.force_new_instance or lock.acquire():
        if args.force_new_instance:
            print("已按 --force-new-instance 跳过单实例检查。")
    else:
        existing = read_instance_file()
        if existing and activate_and_show_existing(existing, open_browser=not args.no_browser):
            print(f"ComfyBatch 已在运行，已切换到已有窗口：{existing.get('url', '')}")
            return
        # Never leave the user with no way forward: explain both exits.
        print("检测到已有实例，但它没有响应，无法切换过去。")
        print("如果确认那个进程已经不在了，可以任选其一：")
        print("  1) 关闭残留的 S.exe 进程后重试")
        print("  2) 加 --force-new-instance 强制启动一个新实例")
        print(f"     （实例记录：{instance_record_path()}）")
        return

    APP.is_primary = True
    server = None
    try:
        action, port = choose_launch_port(args.host, args.port)
        url = f"http://{args.host}:{port}/"
        if action == "reuse":
            # A server on our port answered but the mutex was free: a leftover
            # process from a crashed run. Adopt it rather than double-binding.
            if not args.no_browser:
                webbrowser.open(url)
            return
        server = ThreadingHTTPServer((args.host, port), Handler)
        write_instance_file(port=port, url=url, instance_id=APP.instance_id)
        print(f"ComfyBatch V{APP_VERSION} 已启动：{url}")
        if not args.no_browser:
            threading.Timer(0.8, lambda: webbrowser.open(url)).start()
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    finally:
        clear_instance_file()
        if server is not None:
            server.server_close()
        lock.release()


if __name__ == "__main__":
    main()
