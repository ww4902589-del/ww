"""Persistent resource identities and versioned documents shared by atlas modules.

Legacy JSON files are imported once. SQLite is authoritative afterwards; JSON is
an atomic compatibility export, never a second writer that can revive old data.
"""
from __future__ import annotations
import copy
import hashlib
import json
import os
import pathlib
import sqlite3
import uuid
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor


class Conflict(ValueError):
    pass


def _path(value):
    return os.path.normpath(str(value)).replace('\\', '/').casefold()


def _uid_path(value):
    parts = str(value).rsplit('#', 2)
    return parts[0] if len(parts) == 3 else str(value)


def _hash_file(filename):
    h = hashlib.sha256()
    with open(filename, 'rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


class ResourceStore:
    def __init__(self, directory):
        self.directory = pathlib.Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.database = self.directory / 'resources.sqlite3'
        with self._connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS resources (
                    id TEXT PRIMARY KEY, digest TEXT NOT NULL,
                    path TEXT NOT NULL, uid TEXT NOT NULL,
                    bytes INTEGER NOT NULL, mtime INTEGER NOT NULL);
                CREATE INDEX IF NOT EXISTS resource_digest ON resources(digest);
                CREATE TABLE IF NOT EXISTS aliases (
                    alias TEXT PRIMARY KEY, resource_id TEXT NOT NULL
                    REFERENCES resources(id));
                CREATE TABLE IF NOT EXISTS documents (
                    name TEXT PRIMARY KEY, revision INTEGER NOT NULL,
                    payload TEXT NOT NULL);
            ''')

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.database, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA busy_timeout=30000')
        try:
            with db:
                yield db
        finally:
            db.close()

    def _export(self, name, value):
        p = self.directory / name
        tmp = p.with_name(p.name + '.' + uuid.uuid4().hex + '.tmp')
        try:
            tmp.write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding='utf8')
            os.replace(tmp, p)
        finally:
            if tmp.exists():
                tmp.unlink()

    def load(self, name, default=None):
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM documents WHERE name=?', (name,)).fetchone()
            if row is None:
                p = self.directory / name
                if p.exists():
                    # A corrupt historical file must be reported, never replaced by empty data.
                    value = json.loads(p.read_text(encoding='utf8'))
                else:
                    value = copy.deepcopy(default or {})
                revision = int(value.pop('revision', 0))
                db.execute('INSERT INTO documents VALUES(?,?,?)',
                           (name, revision, json.dumps(value, ensure_ascii=False)))
            else:
                value = json.loads(row['payload'])
                revision = row['revision']
        value['revision'] = revision
        return value

    def save(self, name, value, expected=None):
        self.load(name)
        payload = copy.deepcopy(value)
        payload.pop('revision', None)
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT revision FROM documents WHERE name=?', (name,)).fetchone()
            revision = row['revision']
            if expected is not None and expected != revision:
                raise Conflict('数据已被另一页面修改，请重新读取后再提交')
            revision += 1
            db.execute('UPDATE documents SET revision=?,payload=? WHERE name=?',
                       (revision, json.dumps(payload, ensure_ascii=False), name))
            payload['revision'] = revision
            self._export(name, payload)
        return payload

    def sync(self, name, baseline, desired, merge):
        # Optimistic loop: apply only this caller's changes to the most recent document.
        for _ in range(8):
            current = self.load(name)
            value = merge(baseline, desired, current)
            try:
                return self.save(name, value, expected=current['revision'])
            except Conflict:
                continue
        raise Conflict('保存请求较多，请稍后重试')

    def index(self, images):
        with self._connect() as db:
            old = {r['id']: dict(r) for r in db.execute('SELECT * FROM resources')}
            aliases = {r['alias']: r['resource_id'] for r in db.execute('SELECT * FROM aliases')}
        paths = {_path(im['path']) for im in images}
        jobs = []
        for im in images:
            p = im['path']
            if not os.path.isfile(p):
                continue
            st = os.stat(p)
            prior = old.get(aliases.get(_path(p)))
            if prior and prior['bytes'] == st.st_size and prior['mtime'] == st.st_mtime_ns:
                digest = prior['digest']
            else:
                digest = None
            jobs.append((im, st, prior, digest))
        def fingerprint(job):
            im, st, prior, digest = job
            digest = digest or _hash_file(im['path'])
            after = os.stat(im['path'])
            if (after.st_size, after.st_mtime_ns) != (st.st_size, st.st_mtime_ns):
                raise ValueError('索引期间图片正在变化：' + im['path'])
            return im, st, prior, digest
        with ThreadPoolExecutor(max_workers=3) as pool:
            prepared = list(pool.map(fingerprint, jobs))
        by_digest = {}
        for r in old.values():
            if _path(r['path']) not in paths:
                by_digest.setdefault(r['digest'], []).append(r)
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            claimed = set()
            for im, st, prior, digest in prepared:
                moved = [r for r in by_digest.get(digest, []) if r['id'] not in claimed]
                chosen = prior or (moved[0] if len(moved) == 1 else None)
                rid = chosen['id'] if chosen else 'res:' + uuid.uuid4().hex
                claimed.add(rid)
                im['resource_id'] = rid
                # Presentation identity; keep path-specific resources and saved state.
                im['content_hash'] = digest
                db.execute('''INSERT INTO resources VALUES(?,?,?,?,?,?)
                    ON CONFLICT(id) DO UPDATE SET digest=excluded.digest,path=excluded.path,
                    uid=excluded.uid,bytes=excluded.bytes,mtime=excluded.mtime''',
                           (rid, digest, im['path'], im['uid'], st.st_size, st.st_mtime_ns))
                for alias in (im['uid'], _path(im['path']), rid):
                    db.execute('INSERT OR REPLACE INTO aliases VALUES(?,?)', (alias, rid))
        return images

    def attach_content_hashes(self, images):
        """Restore presentation fingerprints for a pre-upgrade cached payload."""
        with self._connect() as db:
            rows = {r['id']: dict(r) for r in db.execute('SELECT * FROM resources')}
        for im in images:
            row = rows.get(im.get('resource_id'))
            if row and _path(row['path']) == _path(im['path']):
                im['content_hash'] = row['digest']
        return images

    def add_aliases(self, mapping):
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for old, current in mapping.items():
                row=db.execute('SELECT resource_id FROM aliases WHERE alias IN (?,?) LIMIT 1',
                               (current, _path(_uid_path(current)))).fetchone()
                if row:
                    for alias in (old, _path(_uid_path(old))):
                        db.execute('INSERT OR REPLACE INTO aliases VALUES(?,?)',(alias,row['resource_id']))

    def resolve(self, reference):
        with self._connect() as db:
            row = db.execute('''SELECT resources.uid FROM aliases
                JOIN resources ON resources.id=aliases.resource_id
                WHERE alias IN (?,?) ORDER BY alias=? DESC LIMIT 1''',
                (reference, _path(_uid_path(reference)), reference)).fetchone()
        return row['uid'] if row else reference

    def relocate(self, old_path, new_path, new_uid):
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT resource_id FROM aliases WHERE alias=?', (_path(old_path),)).fetchone()
            if row is None:
                return
            rid = row['resource_id']
            st = os.stat(new_path)
            db.execute('UPDATE resources SET path=?,uid=?,bytes=?,mtime=? WHERE id=?',
                       (new_path, new_uid, st.st_size, st.st_mtime_ns, rid))
            for alias in (_path(new_path), new_uid):
                db.execute('INSERT OR REPLACE INTO aliases VALUES(?,?)', (alias, rid))

    def canonical_marks(self, value):
        result = copy.deepcopy(value)
        for s in result.get('sets', []):
            mapped = {u: self.resolve(u) for u in s.get('paths', [])}
            s['paths'] = list(dict.fromkeys(mapped.values()))
            s['note'] = {mapped.get(k, self.resolve(k)): v for k, v in s.get('note', {}).items()}
        return result

    def aliases(self):
        with self._connect() as db:
            return {r['alias']: r['uid'] for r in db.execute('''SELECT alias,uid
                FROM aliases JOIN resources ON resources.id=aliases.resource_id''')
                if r['alias'] != r['uid']}


def merge_marks(baseline, desired, current):
    base = {s['id']: s for s in baseline.get('sets', [])}
    want = {s['id']: s for s in desired.get('sets', [])}
    now = {s['id']: copy.deepcopy(s) for s in current.get('sets', [])}
    for sid in base.keys() - want.keys():
        if sid in now and now[sid] != base[sid]:
            raise Conflict('另一页面修改了待删除的收藏夹，请核对后重试')
        now.pop(sid, None)
    for sid, s in want.items():
        b = base.get(sid)
        if b is None:
            if sid in now and now[sid] != s:
                raise Conflict('收藏夹标识冲突')
            now[sid] = copy.deepcopy(s)
            continue
        if s == b:
            continue
        if sid not in now:
            raise Conflict('收藏夹已在另一页面删除，未重新创建')
        n = now[sid]
        before, after = set(b.get('paths', [])), set(s.get('paths', []))
        remove = before - after
        n['paths'] = list(dict.fromkeys([p for p in n.get('paths', []) if p not in remove]
                                      + [p for p in s.get('paths', []) if p not in before]))
        for key in ('name', 'color'):
            if s.get(key) != b.get(key):
                if n.get(key) != b.get(key) and n.get(key) != s.get(key):
                    raise Conflict('收藏夹名称或颜色有冲突，请核对后重试')
                n[key] = s.get(key)
        bn, sn, nn = b.get('note', {}), s.get('note', {}), n.setdefault('note', {})
        for key in set(bn) | set(sn):
            if bn.get(key) == sn.get(key):
                continue
            if nn.get(key) != bn.get(key) and nn.get(key) != sn.get(key):
                raise Conflict('收藏备注有冲突，请核对后重试')
            if key in sn:
                nn[key] = sn[key]
            else:
                nn.pop(key, None)
    active = desired.get('active') if desired.get('active') != baseline.get('active') else current.get('active')
    if now and active not in now:
        active = next(iter(now))
    return {'sets': list(now.values()), 'active': active}


def merge_grades(baseline, desired, current):
    before, after = baseline.get('grades', {}), desired.get('grades', {})
    result = copy.deepcopy(current.get('grades', {}))
    for key in set(before) | set(after):
        if before.get(key) == after.get(key):
            continue
        if result.get(key) != before.get(key) and result.get(key) != after.get(key):
            raise Conflict('同一张图片的评级有冲突，请核对后重试')
        if key in after:
            result[key] = after[key]
        else:
            result.pop(key, None)
    return {'grades': result}
