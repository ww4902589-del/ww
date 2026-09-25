"""A real second (or third) S process, for the tests that need genuine concurrency.

Started as a plain interpreter rather than through the test loader, so it puts ``src`` and
``tests`` on its own ``sys.path`` -- the same pattern ``segment_process_worker.py`` uses.

Modes
-----
``serve <data_dir> <instance_name>``
    Start the real HTTP server on an ephemeral port against ``data_dir``, register this
    process's instance record, print a single JSON line describing where it is listening,
    then serve until it is terminated.
``settings <settings_path> <key> <rounds>``
    Write the shared settings document ``rounds`` times, each time with a different value
    for ``key``. Used to show that concurrent whole-document writes are serialised rather
    than interleaved.
``library <db_path> <run_id> <count>``
    Index ``count`` rows for ``run_id`` into the shared SQLite library.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import comfybatch_v2_app as app_module  # noqa: E402
from comfybatch_instances import bind_server  # noqa: E402


def serve(data_dir: str, instance_name: str, preferred_port: str = "0") -> int:
    from http.server import ThreadingHTTPServer

    os.environ["COMFYBATCH_DATA_DIR"] = data_dir
    os.environ["COMFYBATCH_INSTANCE_NAME"] = instance_name
    app = app_module.Application()
    app_module.APP = app
    app_module.STATIC_ASSETS.update(app_module.load_static_assets())
    server, port = bind_server(
        "127.0.0.1", int(preferred_port),
        lambda host, candidate: app_module.ExclusiveThreadingHTTPServer(
            (host, candidate), app_module.Handler),
    )
    url = f"http://127.0.0.1:{port}/"
    app_module.write_instance_file(port=port, url=url, instance_id=app.instance_id)
    print(json.dumps({"ready": True, "port": port, "url": url,
                      "instance_id": app.instance_id,
                      "instance_name": app_module.instance_name()}), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app_module.clear_instance_file(app.instance_id)
        server.server_close()
    return 0


def hold_port(port: str, seconds: str) -> int:
    """Bind ``port`` exclusively and hold it, so another process cannot have it.

    Uses the same server class the application binds with: the whole point of that
    class is that a second process *cannot* share the port, and a test that held the
    port with a plain socket would prove nothing about it.
    """
    from http.server import ThreadingHTTPServer

    server, actual = bind_server(
        "127.0.0.1", int(port),
        lambda host, candidate: app_module.ExclusiveThreadingHTTPServer(
            (host, candidate), ThreadingHTTPServer.__mro__[1]
        ),
    )
    print(json.dumps({"holding": actual}), flush=True)
    time.sleep(float(seconds))
    server.server_close()
    return 0


def write_settings(settings_path: str, key: str, rounds: str) -> int:
    """Write the shared settings ``rounds`` times, refreshing when another window won.

    A whole-document save is refused when the file moved on since this process read it --
    that is the point of the revision check. A real window reacts by reloading and
    re-applying, so the retry here models the user-visible loop; the count of *successful*
    writes is what the test counts on.
    """
    from comfybatch_v2_app import SettingsConflict, SettingsStore

    store = SettingsStore(pathlib.Path(settings_path), None)
    store.load()
    written = 0
    # No deadline: under load the two writers can trade the lock for a while, and a
    # give-up would turn a slow machine into a red test rather than a finding. The test's
    # own communicate(timeout=...) is what catches a genuine hang.
    for index in range(int(rounds)):
        attempt = 0
        while True:
            try:
                store.save({key: index})
                written += 1
                break
            except SettingsConflict:
                attempt += 1
                # Back off before reloading: without the pause two processes bounce off
                # each other's lock and can starve one side indefinitely, which is a load
                # artefact rather than a property of the store.
                store.load()
                # Back off a little more each time so the two writers cannot keep
                # colliding on the same half-second boundary.
                time.sleep(min(0.05, 0.005 * (attempt + 1)))
    print(json.dumps({"ok": True, "written": written}), flush=True)
    return 0


def write_library(db_path: str, run_id: str, count: str, start_at: str = "1") -> int:
    from comfybatch_library import LibraryStore

    store = LibraryStore(pathlib.Path(db_path))
    if not store.available:
        print(json.dumps({"ok": False, "warning": store.last_warning}), flush=True)
        return 1
    document = {"run_id": run_id, "items": {}}
    first = int(start_at)
    for index in range(first, first + int(count)):
        document["items"][str(index)] = {
            "index": index,
            "title": f"{run_id} 第 {index} 张",
            "status": "completed",
            "copied_to": f"{run_id}-{index}.png",
            "prompt": f"prompt {run_id} {index}",
            "compiled_prompt": f"compiled {run_id} {index}",
            "review_status": "待确认",
            "generation": {"seeds": {"305": index * 11}},
            "attempts": [{"attempt": 1, "prompt_id": f"{run_id}-{index}"}],
        }
    counters = store.sync_run(run_id, document)
    print(json.dumps({"ok": True, "counters": counters}), flush=True)
    return 0


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "serve":
        return serve(sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else "0")
    if mode == "settings":
        return write_settings(sys.argv[2], sys.argv[3], sys.argv[4])
    if mode == "hold":
        return hold_port(sys.argv[2], sys.argv[3])
    if mode == "library":
        return write_library(sys.argv[2], sys.argv[3], sys.argv[4],
                             sys.argv[5] if len(sys.argv) > 5 else "1")
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
