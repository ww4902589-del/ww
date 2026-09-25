"""Several S instances sharing one data directory.

The program used to be single-instance: a Windows named mutex turned the second launch
away and told it to use the first window. The user asked for the opposite, so this file
pins the replacement contract, and it deliberately splits into two halves.

*The single-process half* pins the parts that are cheap to check in-process: the mutex is
gone, the port is claimed by binding rather than probing, instance records are per
instance, and a shared write never leaves a scratch file behind.

*The real two-process half* is the part that actually matters, because every failure mode
here is a cross-process one: a record one process deletes belongs to another, a fixed
temp name is only a problem when two processes write at once, and SQLite only complains
when a second connection arrives. Those tests start ``multi_instance_worker.py`` as a
genuine second interpreter and talk to it over HTTP, in the style of the existing
``test_segment_resume.py`` worker. Nothing here touches the user's own data directory:
every path is inside a ``TemporaryDirectory``, and ``LOCALAPPDATA`` is redirected so the
instance records land there too.
"""

from __future__ import annotations

import json
import os
import pathlib
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from fakes import app_source  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKER = pathlib.Path(__file__).resolve().parent / "multi_instance_worker.py"

import comfybatch_hub  # noqa: E402
import comfybatch_v2_app as app_module  # noqa: E402
from comfybatch_instances import (  # noqa: E402
    InstanceRecord,
    InstanceRegistry,
    PortUnavailable,
    bind_server,
    process_alive,
)
from comfybatch_shared import FileLock, atomic_write_text, read_json, revision_of  # noqa: E402


class _QuietHandler(BaseHTTPRequestHandler):
    """A handler that says nothing, for testing which port a bind lands on."""

    def do_GET(self):  # noqa: D102 - nothing to document
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):  # noqa: D102 - keep the test output clean
        pass


def get(url: str, timeout: float = 10.0) -> tuple[int, bytes]:
    request = urllib.request.Request(url, headers={"User-Agent": "multi-instance-test/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def free_port() -> int:
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def terminate_tree(proc) -> None:
    """Stop a started process *and its children*.

    A frozen onefile build runs as two processes, so ``Popen.terminate()`` reaches only
    the bootloader and leaves the server holding its port -- the same reason the
    deployment path stops by process tree.
    """
    if proc is None or proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
    else:
        proc.terminate()
    try:
        proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        proc.kill()


class NoProcessWideLockTests(unittest.TestCase):
    """The single-instance gate must not come back.

    It is the one thing that would silently make "two windows, one data directory"
    impossible again, and it would do it by exiting -- the least visible failure there is,
    especially since the packaged build has no console.
    """

    def test_the_hub_no_longer_offers_a_process_wide_lock(self):
        self.assertFalse(hasattr(comfybatch_hub, "InstanceLock"),
                         "hub 不应再提供进程级单实例锁")
        # The editing lease must stay: it guards two *pages in one process*, which is
        # a different problem from two processes, and removing the mutex is not a
        # reason to drop it.
        self.assertTrue(hasattr(comfybatch_hub, "EditLease"))

    def test_the_launcher_does_not_gate_on_a_lock(self):
        source = app_source()
        for gone in ("InstanceLock", "instance_mutex_name", "INSTANCE_MUTEX_NAME",
                     "choose_launch_port"):
            self.assertNotIn(gone, source, f"{gone} 不应再出现在启动路径里")

    def test_the_deprecated_flag_still_parses_and_says_so(self):
        source = app_source()
        self.assertIn("--force-new-instance", source)
        self.assertIn("已废弃", source, "废弃的选项必须说明自己已废弃，不能留成误导性开关")
        self.assertNotIn("已按 --force-new-instance 跳过单实例检查", source)


class BindServerTests(unittest.TestCase):
    """The port is claimed by binding it, so there is no probe-then-bind race."""

    class FakeFactory:
        def __init__(self, busy: set[int] | None = None, actual: dict[int, int] | None = None):
            self.busy = busy or set()
            self.actual = actual or {}
            self.tried: list[int] = []
            self.servers: list[object] = []

        def __call__(self, host: str, port: int):
            self.tried.append(port)
            if port in self.busy:
                raise OSError(10048, "address already in use")

            class Server:
                def __init__(self, server_address):
                    self.server_address = server_address

                def server_close(self):
                    pass

            server = Server((host, self.actual.get(port, port)))
            self.servers.append(server)
            return server

    def test_it_takes_the_first_free_port_after_the_occupied_one(self):
        factory = self.FakeFactory(busy={8790})
        server, port = bind_server("127.0.0.1", 8790, factory)
        self.assertEqual(8791, port)
        self.assertEqual([8790, 8791], factory.tried)
        self.assertEqual(8791, server.server_address[1])

    def test_it_reports_the_port_the_socket_really_got(self):
        """``--port 0`` means "any free port"; announcing 0 would send the browser nowhere."""
        factory = self.FakeFactory(actual={0: 54_321})
        _server, port = bind_server("127.0.0.1", 0, factory)
        self.assertEqual(54_321, port)

    def test_an_exhausted_range_names_the_ports_it_tried(self):
        factory = self.FakeFactory(busy=set(range(8790, 8800)))
        with self.assertRaises(PortUnavailable) as caught:
            bind_server("127.0.0.1", 8790, factory, span=9)
        message = str(caught.exception)
        self.assertIn("8790", message)
        self.assertIn("8799", message)
        self.assertIn("--port", message, "必须告诉用户还可以怎么做")

    def test_a_real_occupied_port_pushes_the_next_instance_along(self):
        # The blocker has to be the same class the app binds with, because that class's
        # exclusivity is what makes an occupied port fail at all on Windows.
        blocker = app_module.ExclusiveThreadingHTTPServer(("127.0.0.1", 0), _QuietHandler)
        self.addCleanup(blocker.server_close)
        taken = blocker.server_address[1]
        server, port = bind_server(
            "127.0.0.1", taken,
            lambda host, candidate: app_module.ExclusiveThreadingHTTPServer((host, candidate), _QuietHandler),
        )
        self.addCleanup(server.server_close)
        self.assertNotEqual(taken, port, "被占用的端口必须让步给下一个")
        self.assertEqual(port, server.server_address[1])


class InstanceRegistryTests(unittest.TestCase):
    """Records are per instance, so one process cannot unregister another."""

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self._env = mock.patch.dict(
            os.environ, {"LOCALAPPDATA": self._temp.name, "COMFYBATCH_INSTANCE_NAME": "default"}
        )
        self._env.start()
        self.addCleanup(self._env.stop)

    def register(self, instance_id: str, port: int, *, pid: int | None = None) -> pathlib.Path:
        return app_module.write_instance_file(
            port=port, url=f"http://127.0.0.1:{port}/", instance_id=instance_id
        )

    def test_each_instance_writes_its_own_file(self):
        first = self.register("aaaa", 8790)
        second = self.register("bbbb", 8791)
        self.assertNotEqual(first, second, "两个实例必须各写各的记录文件")
        self.assertTrue(first.is_file() and second.is_file())
        self.assertEqual(8790, json.loads(first.read_text(encoding="utf-8"))["port"])
        self.assertEqual(8791, json.loads(second.read_text(encoding="utf-8"))["port"])

    def test_clearing_one_instance_leaves_the_other(self):
        self.register("aaaa", 8790)
        self.register("bbbb", 8791)
        self.assertTrue(app_module.clear_instance_file("aaaa"))
        registry = app_module.instance_registry()
        self.assertIsNone(registry.read("aaaa"))
        self.assertIsNotNone(registry.read("bbbb"),
                             "一个实例退出不得删掉另一个正在运行的实例的记录")

    def test_running_instances_lists_only_processes_that_exist(self):
        self.register("live", 8790)
        registry = app_module.instance_registry()
        stale = InstanceRecord(instance_id="dead", pid=0x7FFF_FFF0, port=8799,
                               url="http://127.0.0.1:8799/", started_at=time.time() - 3600)
        registry.register(stale)
        # An hour old and belonging to a pid that does not exist: prunable.
        listed = {record["instance_id"] for record in app_module.running_instances()}
        self.assertIn("live", listed)
        self.assertNotIn("dead", listed, "进程已不存在的记录应当被回收")
        self.assertIsNone(registry.read("dead"), "回收必须真的删掉文件")

    def test_a_recent_dead_record_is_kept_until_the_grace_period_passes(self):
        registry = app_module.instance_registry()
        registry.register(InstanceRecord(instance_id="just-died", pid=0x7FFF_FFF0, port=8799,
                                         url="http://127.0.0.1:8799/", started_at=time.time()))
        self.assertIsNotNone(registry.read("just-died"),
                             "刚启动就被判定死亡的记录不能立刻删——另一个进程可能只是还没开始监听")

    def test_records_do_not_follow_the_data_directory(self):
        """A launch with another --data-dir must still find the instances.

        Asserted against an independently built path. The earlier version compared
        ``write_instance_file``'s return value with ``instance_registry().root / "cccc.json"``
        -- the same function's own idea of the root -- so it would have passed even if
        ``instance_root()`` had started honouring ``--data-dir``.
        """
        os.environ["COMFYBATCH_DATA_DIR"] = "C:/tmp/one"
        first = self.register("cccc", 8790)
        alternate = pathlib.Path(self._temp.name) / "another-data-dir"
        os.environ["COMFYBATCH_DATA_DIR"] = str(alternate)

        expected = pathlib.Path(self._temp.name) / "ComfyBatch-S" / "instances" / "cccc.json"
        self.assertEqual(expected, first,
                         "实例记录必须落在 LOCALAPPDATA 下，不随 --data-dir 移动")
        self.assertFalse(alternate.exists(), "写记录不该顺便在数据目录里造东西")
        self.assertEqual(expected, app_module.instance_registry().root / "cccc.json")

    def test_the_registry_directory_holds_one_file_per_instance(self):
        self.register("aaaa", 8790)
        self.register("bbbb", 8791)
        files = sorted(path.name for path in app_module.instance_registry().root.glob("*.json"))
        self.assertEqual(["aaaa.json", "bbbb.json"], files)


class SharedWriteTests(unittest.TestCase):
    """Unique scratch names, an atomic publish, and a lock that actually excludes."""

    def test_no_scratch_file_survives_a_write(self):
        with tempfile.TemporaryDirectory() as temp:
            target = pathlib.Path(temp) / "settings.json"
            atomic_write_text(target, '{"a": 1}')
            self.assertEqual('{"a": 1}', target.read_text(encoding="utf-8"))
            self.assertEqual([], list(pathlib.Path(temp).glob("*.tmp")),
                             "写入结束后不得留下临时文件")

    def test_the_scratch_name_is_unique_per_write(self):
        from comfybatch_shared import unique_temp_for

        with tempfile.TemporaryDirectory() as temp:
            target = pathlib.Path(temp) / "settings.json"
            first, second = unique_temp_for(target), unique_temp_for(target)
            self.assertNotEqual(first, second, "两次写入不能挑同一个临时文件名")
            self.assertEqual(target.parent, first.parent,
                             "临时文件必须与目标同目录，否则替换不再是原子的")
            self.assertTrue(first.name.startswith(target.name))

    def test_a_lock_excludes_a_second_process(self):
        script = (
            "import sys, pathlib, time;"
            f"sys.path.insert(0, r'{ROOT / 'src'}');"
            "from comfybatch_shared import FileLock;"
            "lock = FileLock(pathlib.Path(sys.argv[1]));"
            "lock.acquire();"
            "print('HELD', flush=True);"
            "time.sleep(float(sys.argv[2]))"
        )
        with tempfile.TemporaryDirectory() as temp:
            target = pathlib.Path(temp) / "resource_rules.json"
            holder = subprocess.Popen([sys.executable, "-c", script, str(target), "3"],
                                      stdout=subprocess.PIPE, text=True)
            self.addCleanup(holder.wait)
            self.assertEqual("HELD", holder.stdout.readline().strip())
            try:
                with FileLock(target, timeout=0.4):
                    self.fail("另一个进程持锁时不应该拿到锁")
            except Exception as exc:  # FileLockTimeout
                self.assertIn("超时", str(exc))

    def test_a_stale_lock_is_broken_instead_of_blocking_forever(self):
        with tempfile.TemporaryDirectory() as temp:
            target = pathlib.Path(temp) / "settings.json"
            lock_path = target.with_name(target.name + ".lock")
            lock_path.write_text("99999-0", encoding="utf-8")
            old = time.time() - 3600
            os.utime(lock_path, (old, old))
            with FileLock(target, timeout=1.0, stale_after=30.0) as lock:
                self.assertGreaterEqual(lock.broken_stale, 1)

    def test_the_revision_moves_forward_on_every_write(self):
        from comfybatch_shared import locked_json_update

        with tempfile.TemporaryDirectory() as temp:
            target = pathlib.Path(temp) / "resource_rules.json"
            for expected in range(1, 4):
                outcome = locked_json_update(target, lambda _doc: {"rules": {"a": "b"}})
                self.assertTrue(outcome.applied, outcome.reason)
                self.assertEqual(expected, outcome.revision)
            self.assertEqual(3, revision_of(read_json(target)))

    def test_a_stale_writer_is_told_instead_of_overwriting(self):
        from comfybatch_shared import locked_json_update

        with tempfile.TemporaryDirectory() as temp:
            target = pathlib.Path(temp) / "settings.json"
            locked_json_update(target, lambda _doc: {"presets": ["a"]})
            # A window that read revision 1 and then waits while another writes revision 2
            # must not silently roll that change back.
            locked_json_update(target, lambda _doc: {"presets": ["a", "b"]})
            outcome = locked_json_update(target, lambda _doc: {"presets": ["c"]},
                                        expected_revision=1)
            self.assertFalse(outcome.applied)
            self.assertTrue(outcome.stale)
            self.assertIn("另一窗口", outcome.reason)
            self.assertEqual(["a", "b"], read_json(target)["presets"])


class RealTwoProcessTests(unittest.TestCase):
    """Two interpreters, one data directory -- the case the whole stage exists for."""

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory(prefix="comfybatch-multi-")
        self.addCleanup(self._temp.cleanup)
        self.root = pathlib.Path(self._temp.name)
        self.data_dir = self.root / "data"
        self.data_dir.mkdir()
        self.profile = self.root / "profile"
        (self.profile / "Local").mkdir(parents=True)
        self.processes: list[subprocess.Popen] = []
        self.addCleanup(self._stop_all)

    def _env(self) -> dict:
        env = dict(os.environ)
        env.update({
            "LOCALAPPDATA": str(self.profile / "Local"),
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUNBUFFERED": "1",
        })
        return env

    def _stop_all(self):
        for proc in self.processes:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()

    def start_instance(self, name: str, preferred: int = 0) -> dict:
        proc = subprocess.Popen(
            [sys.executable, str(WORKER), "serve", str(self.data_dir), name, str(preferred)],
            env=self._env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        self.processes.append(proc)
        line = proc.stdout.readline()
        if not line:
            self.fail(f"实例没有启动：{proc.stderr.read()[:2000]}")
        ready = json.loads(line)
        deadline = time.time() + 20
        while time.time() < deadline:
            status, _body = get(f"http://127.0.0.1:{ready['port']}/api/ping")
            if status == 200:
                return ready
            time.sleep(0.2)
        self.fail("实例报告已就绪，但 /api/ping 一直没有响应")

    def registry(self) -> InstanceRegistry:
        return InstanceRegistry(self.profile / "Local" / "ComfyBatch-S" / "instances")

    # ------------------------------------------------------------------ tests

    def test_two_instances_share_one_data_directory_on_their_own_ports(self):
        first = self.start_instance("first")
        second = self.start_instance("second")
        self.assertNotEqual(first["port"], second["port"], "两个实例必须各自监听一个端口")
        self.assertNotEqual(first["instance_id"], second["instance_id"])

        for ready in (first, second):
            status, body = get(f"http://127.0.0.1:{ready['port']}/api/ping")
            self.assertEqual(200, status)
            payload = json.loads(body)
            self.assertEqual(ready["instance_id"], payload["instance_id"])
            self.assertTrue(payload["ok"])
            self.assertEqual(ready["instance_name"], payload["instance_name"])
            self.assertIn("pid", payload, "ping 必须能对上实例记录里的进程")
            self.assertNotIn("is_primary", payload,
                             "多实例下不能有任何一个自称主实例")

        # Each window can be opened on its own URL and serves the real page.
        for ready in (first, second):
            status, body = get(f"http://127.0.0.1:{ready['port']}/")
            self.assertEqual(200, status)
            self.assertIn(b"ComfyBatch", body)

        records = {record.instance_id: record for record in self.registry().records()}
        self.assertEqual({first["instance_id"], second["instance_id"]}, set(records),
                         "两个实例都必须留下自己的记录")

    def test_the_page_can_list_the_other_instances(self):
        first = self.start_instance("first")
        second = self.start_instance("second")
        status, body = get(f"http://127.0.0.1:{first['port']}/api/instances")
        self.assertEqual(200, status)
        payload = json.loads(body)
        listed = {row["instance_id"] for row in payload["instances"]}
        self.assertIn(first["instance_id"], listed)
        self.assertIn(second["instance_id"], listed)
        self.assertEqual(first["instance_id"], payload["this_instance_id"])

    def test_closing_one_instance_leaves_the_other_serving_and_registered(self):
        first = self.start_instance("first")
        second = self.start_instance("second")
        first_proc = self.processes[0]
        first_proc.terminate()
        first_proc.wait(timeout=15)

        status, body = get(f"http://127.0.0.1:{second['port']}/api/ping")
        self.assertEqual(200, status, "另一个实例必须继续可用")
        self.assertEqual(second["instance_id"], json.loads(body)["instance_id"])

        records = {record.instance_id: record for record in self.registry().records()}
        self.assertIn(second["instance_id"], records, "退出者不得删掉仍在运行的实例记录")

    def test_concurrent_settings_writes_stay_serialised_and_readable(self):
        settings = self.data_dir / "settings.json"
        procs = [
            subprocess.Popen([sys.executable, str(WORKER), "settings", str(settings), key, "25"],
                             env=self._env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            for key in ("alpha", "beta")
        ]
        landed = 0
        for proc in procs:
            out, err = proc.communicate(timeout=600)
            self.assertEqual(0, proc.returncode, (out + err)[:2000])
            report = json.loads(out.strip().splitlines()[-1])
            self.assertTrue(report["ok"], f"一个写入进程放弃了：{out}{err}")
            landed += report["written"]
        payload = json.loads(settings.read_text(encoding="utf-8"))
        # The invariant that matters: every write that reported success bumped the revision
        # exactly once, which can only hold if the lock serialised the read-compute-write.
        self.assertEqual(landed, payload["revision"],
                         "每次成功写入都必须且只能推进一次 revision")
        self.assertEqual(50, landed, "两侧各 25 次写入都应最终落盘")
        # ``save`` writes the whole document, so the last writer legitimately wins for the
        # keys it owns -- what must never happen is a *torn* file holding half of each.
        # Exactly one writer's key survives, complete and within its own value range.
        surviving = [key for key in ("alpha", "beta") if key in payload]
        self.assertEqual(1, len(surviving),
                         "整档写入是「最后写者胜」：不能把两个窗口的字段混成一份从未存在过的文档")
        self.assertIn(payload[surviving[0]], range(25), "存活的必须是某一次完整写入")
        self.assertEqual([], [p.name for p in self.data_dir.glob("*.tmp")],
                         "并发写入结束后不得留下临时文件")
        self.assertEqual([], [p.name for p in self.data_dir.glob("*.lock")],
                         "锁必须被释放")

    def test_a_stale_settings_window_is_told_instead_of_overwriting(self):
        """The window that has been open longest is the one that loses data silently.

        Both stores here are the real one; the second one reads the file, then the first
        one writes, and then the second tries to save what it had -- which would roll the
        first one's change back. It must be refused, and the file must still hold the
        first one's change.
        """
        from comfybatch_v2_app import SettingsConflict, SettingsStore

        path = self.data_dir / "settings.json"
        first = SettingsStore(path, None)
        first.load()
        first.save({"alpha": "from the first window"})

        second = SettingsStore(path, None)
        second.load()

        first.save({"alpha": "changed again by the first window"})

        with self.assertRaises(SettingsConflict) as caught:
            second.save({"beta": "from the second window"})
        self.assertIn("另一个窗口", str(caught.exception))
        stored = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual("changed again by the first window", stored["alpha"])
        self.assertNotIn("beta", stored, "被拒绝的写入不得改动文件")

    def test_concurrent_library_writes_leave_a_readable_database(self):
        db = self.data_dir / "library.db"
        procs = [
            subprocess.Popen([sys.executable, str(WORKER), "library", str(db), run_id, "20", str(start)],
                             env=self._env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            for run_id, start in (("run-a", "1"), ("run-b", "1"))
        ]
        for proc in procs:
            out, err = proc.communicate(timeout=180)
            self.assertEqual(0, proc.returncode, (out + err)[:2000])
            self.assertTrue(json.loads(out.strip().splitlines()[-1])["ok"], out)

        from comfybatch_library import LibraryStore

        store = LibraryStore(db)
        self.assertTrue(store.available, store.last_warning)
        stats = store.stats()
        self.assertEqual(40, stats["works"], "两个进程各写 20 条，必须都在库里")
        works = store.query(limit=100)["items"]
        self.assertEqual({"run-a", "run-b"}, {row["run_id"] for row in works})


class ProcessAliveTests(unittest.TestCase):
    def test_an_existing_but_unqueryable_process_counts_as_alive(self):
        """Access denied means "there is a process you may not ask about".

        Windows pid 4 is the system process: OpenProcess refuses it with
        ERROR_ACCESS_DENIED. Reading that as death would let one window delete a
        sibling's instance record -- for example a window started elevated, which is
        exactly the case the registry promises to protect.
        """
        self.assertTrue(process_alive(4), "不可查询但存在的进程不得判定为已消失")

    def test_a_pid_that_cannot_exist_is_not_alive(self):
        self.assertFalse(process_alive(0xFFFF_FFF0))
        self.assertFalse(process_alive(0))


if __name__ == "__main__":
    unittest.main()
