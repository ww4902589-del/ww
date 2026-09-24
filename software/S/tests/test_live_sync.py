"""Live sync: the SSE stream, the editing lease and the single-instance lock.

These cover the three mechanisms that stop several open pages from corrupting
each other. All three were real gaps: pages could hold different configs without
noticing, two pages could overwrite each other's saves, and a second launch
started a whole second backend.
"""

from __future__ import annotations

import json
import pathlib
import socket
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from fakes import (  # noqa: E402
    SOURCE_ROOT,
    ConnectedClient,
    html_source,
    install_static_assets,
    page_source,
)

import comfybatch_hub  # noqa: E402
import comfybatch_v2_app as app_module  # noqa: E402
from comfybatch_hub import EditLease, InstanceLock, StateHub  # noqa: E402
from comfybatch_v2_app import Application, Handler, sse_frame  # noqa: E402


class StateHubTests(unittest.TestCase):
    def test_bump_moves_the_version_and_wakes_waiters(self):
        hub = StateHub()
        start = hub.version
        seen: list[int] = []

        def waiter():
            seen.append(hub.wait(start, timeout=3))

        thread = threading.Thread(target=waiter, daemon=True)
        thread.start()
        time.sleep(0.05)
        hub.bump()
        thread.join(timeout=3)
        self.assertEqual([start + 1], seen, "bump 必须唤醒等待者")

    def test_wait_times_out_when_nothing_changes(self):
        hub = StateHub()
        started = time.time()
        version = hub.wait(hub.version, timeout=0.2)
        self.assertEqual(hub.version, version)
        self.assertGreaterEqual(time.time() - started, 0.15)

    def test_activation_is_observable(self):
        hub = StateHub()
        self.assertEqual(0.0, hub.activated_at)
        hub.mark_activated()
        self.assertGreater(hub.activated_at, 0.0)


class EditLeaseTests(unittest.TestCase):
    def test_the_first_claim_holder_keeps_the_lease(self):
        lease = EditLease()
        granted, status = lease.claim("page-a")
        self.assertTrue(granted)
        self.assertTrue(status["mine"])
        self.assertEqual("page-a", status["holder"]["client_id"])

    def test_a_second_page_is_refused_and_told_who_holds_it(self):
        lease = EditLease()
        lease.claim("page-a")
        granted, status = lease.claim("page-b")
        self.assertFalse(granted, "第二个页面不得写入")
        self.assertFalse(status["mine"])
        self.assertEqual("page-a", status["holder"]["client_id"])

    def test_takeover_is_allowed_when_deliberate(self):
        lease = EditLease()
        lease.claim("page-a")
        granted, status = lease.claim("page-b", force=True)
        self.assertTrue(granted)
        self.assertEqual("page-b", status["holder"]["client_id"])

    def test_the_same_page_may_reclaim_and_renew(self):
        lease = EditLease()
        lease.claim("page-a")
        granted, _status = lease.claim("page-a")
        self.assertTrue(granted)
        self.assertTrue(lease.renew("page-a"))
        self.assertFalse(lease.renew("page-b"))

    def test_the_lease_expires_so_a_crashed_tab_cannot_lock_the_ui(self):
        lease = EditLease(ttl=0.05)
        lease.claim("page-a")
        time.sleep(0.08)
        granted, status = lease.claim("page-b")
        self.assertTrue(granted, "失效的租约必须能被别人接手")
        self.assertEqual("page-b", status["holder"]["client_id"])

    def test_release_frees_the_lease_for_others(self):
        lease = EditLease()
        lease.claim("page-a")
        self.assertTrue(lease.release("page-a"))
        self.assertFalse(lease.status()["held"])
        granted, _status = lease.claim("page-b")
        self.assertTrue(granted)

    def test_release_from_a_non_holder_is_refused(self):
        lease = EditLease()
        lease.claim("page-a")
        self.assertFalse(lease.release("page-b"), "非持有者不能释放别人的租约")


class InstanceLockTests(unittest.TestCase):
    def test_acquire_release_round_trip(self):
        lock = InstanceLock("ComfyBatch-test-lock-roundtrip")
        self.assertTrue(lock.acquire())
        self.assertTrue(lock.acquire(), "同一进程重复获取应当成功")
        lock.release()

    def test_a_second_process_cannot_acquire(self):
        """Simulated with a separate ctypes handle on the same mutex name."""
        first = InstanceLock("ComfyBatch-test-lock-exclusive")
        self.assertTrue(first.acquire())
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            handle = kernel32.CreateMutexW(None, False, "ComfyBatch-test-lock-exclusive")
            already = kernel32.GetLastError() == 183
            if handle:
                kernel32.CloseHandle(handle)
            self.assertTrue(already, "同名互斥体应当报告已存在")
        finally:
            first.release()


class SseFramingTests(unittest.TestCase):
    def test_frame_has_the_event_and_data_lines(self):
        frame = sse_frame("snapshot", '{"a":1}')
        self.assertEqual(b'event: snapshot\ndata: {"a":1}\n\n', frame)

    def test_heartbeat_is_a_comment(self):
        self.assertTrue(comfybatch_hub.SSE_HEARTBEAT_SECONDS > 0)


class LiveSyncRouteTests(unittest.TestCase):
    """Drive the real handler: SSE delivery, lease enforcement, activation."""

    @classmethod
    def setUpClass(cls):
        import tempfile

        cls._temp = tempfile.TemporaryDirectory()
        root = pathlib.Path(cls._temp.name)
        app = Application(settings_path=root / "settings.json", backup_settings_path=None)
        app.runner.client = ConnectedClient()
        app_module.APP = app
        install_static_assets(app_module)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        cls._temp.cleanup()

    @classmethod
    def request(cls, path, payload=None, timeout=10, headers=None):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        url = f"http://127.0.0.1:{cls.port}{path}"
        if payload is None:
            request = urllib.request.Request(url, method="GET", headers=headers or {})
        else:
            request = urllib.request.Request(
                url, data=json.dumps(payload).encode("utf-8"),
                method="POST", headers={"Content-Type": "application/json", **(headers or {})},
            )
        try:
            with opener.open(request, timeout=timeout) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_ping_identifies_the_instance(self):
        status, body = self.request("/api/ping")
        self.assertEqual(200, status)
        self.assertTrue(body["ok"])
        self.assertEqual(app_module.APP.instance_id, body["instance_id"])
        self.assertEqual("ComfyBatch", body["app"])

    def test_lease_routes_arbitrate_two_pages(self):
        self.request("/api/lease/release", {"client_id": "page-a"})
        self.request("/api/lease/release", {"client_id": "page-b"})

        status, body = self.request("/api/lease/claim", {"client_id": "page-a", "label": "A"})
        self.assertEqual(200, status)
        self.assertTrue(body["granted"])

        status, body = self.request("/api/lease/claim", {"client_id": "page-b"})
        self.assertTrue(body["granted"] is False, "第二个页面不该拿到租约")
        self.assertEqual("page-a", body["lease"]["holder"]["client_id"])

        # A write from the non-holder is refused with an explanation, not 500.
        status, body = self.request("/api/update-bundle", {
            "client_id": "page-b", "items": [{"title": "t", "prompt": "p"}],
        })
        self.assertEqual(409, status)
        self.assertIn("另一个页面正在编辑", body["error"])
        self.assertIn("接管编辑", body["error"])

    def test_the_holder_may_write_and_a_script_without_an_id_is_not_gated(self):
        self.request("/api/lease/release", {"client_id": "page-a"})
        self.request("/api/lease/claim", {"client_id": "page-a"})
        # A client without an id is automation, not a competing UI page.
        status, _body = self.request("/api/configure", {
            "comfy_root": str(app_module.APP.comfy_root),
            "comfy_url": "http://127.0.0.1:8188",
            "workflow_roots": [str(app_module.APP.workflow_roots[0])],
            "output_root": str(app_module.APP.output_root),
        })
        self.assertEqual(200, status, "无 client_id 的调用不应被租约拦截")
        app_module.APP.runner.client = ConnectedClient()

    def test_takeover_moves_the_lease_and_unblocks_writing(self):
        import base64

        raw = json.dumps({"items": [{"title": "接管", "prompt": "p"}]}).encode("utf-8")
        self.request("/api/import", {"filename": "lease.json", "base64": base64.b64encode(raw).decode("ascii")})
        self.request("/api/lease/claim", {"client_id": "page-takeover-a", "force": True})
        _status, body = self.request("/api/lease/claim", {"client_id": "page-takeover-b", "force": True})
        self.assertTrue(body["granted"])
        self.assertEqual("page-takeover-b", body["lease"]["holder"]["client_id"])

        status, _body = self.request("/api/update-bundle", {
            "client_id": "page-takeover-b", "items": [{"title": "t", "prompt": "p"}],
        })
        self.assertEqual(200, status, "接管后应当可以写入")
        self.request("/api/lease/release", {"client_id": "page-takeover-b"})

    def test_activate_endpoint_marks_the_hub(self):
        before = app_module.APP.hub.activated_at
        status, body = self.request("/api/activate", {})
        self.assertEqual(200, status)
        self.assertTrue(body["ok"])
        self.assertGreater(app_module.APP.hub.activated_at, before)

    def test_event_stream_pushes_a_snapshot_and_reacts_to_changes(self):
        """The real regression: a page must be told, not have to poll."""
        connection = socket.create_connection(("127.0.0.1", self.port), timeout=10)
        try:
            connection.sendall(
                b"GET /api/events?client_id=page-sse HTTP/1.1\r\n"
                b"Host: 127.0.0.1\r\nAccept: text/event-stream\r\n\r\n"
            )
            buffer = b""

            def read_until(marker: bytes, deadline: float) -> bytes:
                nonlocal buffer
                while marker not in buffer and time.time() < deadline:
                    connection.settimeout(max(0.1, deadline - time.time()))
                    try:
                        chunk = connection.recv(65536)
                    except socket.timeout:
                        break
                    if not chunk:
                        break
                    buffer += chunk
                return buffer

            got = read_until(b"event: snapshot", time.time() + 8)
            self.assertIn(b"event: snapshot", got, "连接后应立刻收到一次快照")
            self.assertIn(b'"instance_id"', got)

            # A change elsewhere must produce another frame without a poll.
            buffer = b""
            app_module.APP.lock_changed()
            got = read_until(b"event: snapshot", time.time() + 8)
            self.assertIn(b"event: snapshot", got, "状态变化后应主动推送")
        finally:
            connection.close()

    def test_event_stream_slots_are_released_when_a_client_leaves(self):
        app = app_module.APP
        before = app._stream_slots._value  # available permits
        connection = socket.create_connection(("127.0.0.1", self.port), timeout=10)
        connection.sendall(b"GET /api/events HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
        time.sleep(0.4)
        during = app._stream_slots._value
        connection.close()
        deadline = time.time() + 12
        while time.time() < deadline and app._stream_slots._value < before:
            time.sleep(0.2)
        self.assertLess(during, before, "连接期间应占用一个槽位")
        self.assertGreaterEqual(app._stream_slots._value, before, "断开后必须释放槽位")

    def test_status_carries_the_lease_so_polling_stays_correct(self):
        self.request("/api/lease/claim", {"client_id": "page-status", "force": True})
        _status, body = self.request("/api/status?client_id=page-status")
        self.assertTrue(body["lease"]["mine"])
        _status, other = self.request("/api/status?client_id=page-other")
        self.assertFalse(other["lease"]["mine"])
        self.assertTrue(other["lease"]["held"])
        self.request("/api/lease/release", {"client_id": "page-status"})


class PresetSyncTests(unittest.TestCase):
    """A page must learn that another page changed the preset library.

    The regression: ``snapshot()`` carried no preset information, so an already
    open page kept its stale dropdown until a manual reload. The fix ships a
    cheap fingerprint (``presets_rev``) so the page can refetch only when it
    actually changed -- but a fingerprint nobody checks is worthless, so these
    cover both the backend value and the page's handling of it.
    """

    def setUp(self):
        import tempfile

        self._temp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self._temp.name)
        self.app = Application(settings_path=root / "settings.json", backup_settings_path=None)

    def tearDown(self):
        self._temp.cleanup()

    def _add(self, name):
        return self.app.save_style_lora_preset(
            {"name": name, "styles": [{"catalog": "c", "name": "s"}], "loras": []}
        )

    def test_snapshot_carries_the_fingerprint(self):
        self.assertIn("presets_rev", self.app.snapshot(), "快照必须带上预设指纹")

    def test_the_fingerprint_flips_on_add_rename_edit_and_delete(self):
        base = self.app.presets_rev()
        preset = self._add("指纹测试")
        after_add = self.app.presets_rev()
        self.assertNotEqual(base, after_add, "新增预设必须改变指纹")

        self.app.save_style_lora_preset({
            "id": preset["id"], "name": "指纹测试-改名",
            "styles": [{"catalog": "c", "name": "s"}], "loras": [],
        })
        after_rename = self.app.presets_rev()
        self.assertNotEqual(after_add, after_rename, "改名必须改变指纹")

        self.app.save_style_lora_preset({
            "id": preset["id"], "name": "指纹测试-改名",
            "styles": [{"catalog": "c", "name": "s"}],
            "loras": [{"name": "l.safetensors", "strength": 0.7, "use_triggers": True}],
        })
        after_edit = self.app.presets_rev()
        self.assertNotEqual(after_rename, after_edit, "改内容也必须改变指纹")

        self.app.delete_style_lora_preset(preset["id"])
        self.assertNotEqual(after_edit, self.app.presets_rev(), "删除必须改变指纹")

    def test_the_fingerprint_is_stable_when_nothing_changes(self):
        self._add("稳定测试")
        self.assertEqual(self.app.presets_rev(), self.app.presets_rev(),
                         "没有变化时指纹不得抖动，否则每帧都会触发重拉")

    def test_the_snapshot_still_does_not_ship_the_bulk(self):
        self._add("体积测试")
        self.assertNotIn("style_lora_presets", self.app.snapshot(),
                         "快照每帧都发，不能整包塞进预设对象")

    def test_the_page_refetches_only_after_a_baseline_exists(self):
        """First frame records the baseline; only a later change triggers a pull."""
        js = page_source()
        self.assertIn("let presetsRev=''", js)
        self.assertIn("if(presetsRev&&payload.presets_rev!==presetsRev)", js)
        self.assertIn("fetchInventory(true)", js, "变化时必须静默重拉")

    def test_the_refetch_shares_one_request_body(self):
        """A second copy of the /api/configure body is exactly how this drifts."""
        js = page_source()
        needle = "workflow_roots:[$('workflowRoot').value]"
        self.assertEqual(1, js.count(needle), "配置请求体只能有一处")
        self.assertIn("async function fetchInventory(quiet)", js)

    def test_an_old_server_without_the_fingerprint_is_tolerated(self):
        """No presets_rev in the payload must behave exactly as before."""
        js = page_source()
        self.assertIn("typeof payload.presets_rev!=='undefined'", js,
                      "旧服务端下不得重拉或报错")


class InstanceFileTests(unittest.TestCase):
    def setUp(self):
        import os
        import tempfile

        self._temp = tempfile.TemporaryDirectory()
        self._previous = {key: os.environ.get(key) for key in (
            "COMFYBATCH_DATA_DIR", "LOCALAPPDATA", "COMFYBATCH_INSTANCE_NAME",
        )}
        os.environ["COMFYBATCH_DATA_DIR"] = self._temp.name
        os.environ["LOCALAPPDATA"] = self._temp.name
        os.environ.pop("COMFYBATCH_INSTANCE_NAME", None)

    def tearDown(self):
        import os

        for key, value in self._previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self._temp.cleanup()

    def test_round_trip(self):
        app_module.write_instance_file(port=8790, url="http://127.0.0.1:8790/", instance_id="abc")
        payload = app_module.read_instance_file()
        self.assertEqual(8790, payload["port"])
        self.assertEqual("abc", payload["instance_id"])
        app_module.clear_instance_file()
        self.assertEqual({}, app_module.read_instance_file())

    def test_instance_record_lives_inside_the_test_directory(self):
        self.assertEqual(
            pathlib.Path(self._temp.name) / "ComfyBatch-S" / "instance.json",
            app_module.instance_record_path(),
        )

    def test_activate_refuses_a_stale_or_foreign_file(self):
        """A crashed process's leftover file must not make us poke a stranger."""
        app_module.write_instance_file(port=1, url="http://127.0.0.1:1/", instance_id="ghost")
        self.assertFalse(app_module.activate_existing_instance(app_module.read_instance_file()))
        self.assertFalse(app_module.activate_existing_instance({}))
        self.assertFalse(app_module.activate_existing_instance({"url": "http://127.0.0.1:1/"}))

    @mock.patch.object(app_module.webbrowser, "open")
    @mock.patch.object(app_module, "activate_existing_instance", return_value=True)
    def test_duplicate_launch_opens_the_existing_page(self, activate, open_browser):
        existing = {"url": "http://127.0.0.1:8790/", "instance_id": "primary"}

        self.assertTrue(app_module.activate_and_show_existing(existing, open_browser=True))

        activate.assert_called_once_with(existing)
        open_browser.assert_called_once_with(existing["url"])

    @mock.patch.object(app_module.webbrowser, "open")
    @mock.patch.object(app_module, "activate_existing_instance", return_value=True)
    def test_no_browser_keeps_duplicate_launch_headless(self, _activate, open_browser):
        existing = {"url": "http://127.0.0.1:8790/", "instance_id": "primary"}

        self.assertTrue(app_module.activate_and_show_existing(existing, open_browser=False))

        open_browser.assert_not_called()

    def test_missing_record_discovers_running_local_service_and_opens_page(self):
        activated = []

        class LocalComfyBatch(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                payload = {"ok": True, "app": "ComfyBatch", "instance_id": "already-running", "is_primary": True}
                body = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                activated.append(self.path)
                body = b'{"ok":true}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = ThreadingHTTPServer(("127.0.0.1", 0), LocalComfyBatch)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with mock.patch.object(app_module.webbrowser, "open") as open_browser:
                found = app_module.show_existing_or_discover(
                    {}, "127.0.0.1", server.server_address[1], open_browser=True,
                )
            self.assertEqual("already-running", found["instance_id"])
            self.assertEqual(["/api/activate"], activated)
            open_browser.assert_called_once_with(f"http://127.0.0.1:{server.server_address[1]}/")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    @mock.patch.object(app_module.webbrowser, "open")
    def test_missing_record_does_not_open_a_foreign_or_remote_service(self, open_browser):
        with mock.patch.object(app_module, "activate_and_show_existing") as activate:
            self.assertEqual({}, app_module.show_existing_or_discover(
                {}, "0.0.0.0", 8790, open_browser=True,
            ))
            activate.assert_not_called()
        open_browser.assert_not_called()

    def test_missing_record_finds_primary_on_fallback_port(self):
        opener = mock.Mock()

        def answer(url, *, timeout):
            self.assertEqual(0.25, timeout)
            response = mock.MagicMock()
            payload = {"ok": True, "app": "ComfyBatch", "instance_id": "fallback", "is_primary": True}
            if url == "http://127.0.0.1:9000/api/ping":
                payload["app"] = "other"
            response.__enter__.return_value.read.return_value = json.dumps(payload).encode()
            return response

        opener.open.side_effect = answer
        with mock.patch.object(app_module, "build_opener", return_value=opener), \
                mock.patch.object(app_module, "activate_and_show_existing", return_value=True) as activate:
            found = app_module.show_existing_or_discover({}, "127.0.0.1", 9000, open_browser=True)
        self.assertEqual("http://127.0.0.1:9001/", found["url"])
        activate.assert_called_once_with(found, open_browser=True)


class InstanceNamespaceTests(unittest.TestCase):
    """A different --data-dir must not be able to trap a launch.

    Found on a real run: the mutex is per user, but the instance record lived in
    the data directory. A launch with a different --data-dir was therefore blocked
    by the mutex yet could not find the running instance -- and had no way
    forward.
    """

    def setUp(self):
        import os

        self._previous = {key: os.environ.get(key) for key in
                          ("COMFYBATCH_INSTANCE_NAME", "COMFYBATCH_DATA_DIR", "LOCALAPPDATA")}

    def tearDown(self):
        import os

        for key, value in self._previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_the_record_is_independent_of_the_data_directory(self):
        import os

        first = None
        for data_dir in ("C:/tmp/one", "C:/tmp/two"):
            os.environ["COMFYBATCH_DATA_DIR"] = data_dir
            path = app_module.instance_record_path()
            first = first or path
            self.assertEqual(first, path, "实例记录不能跟着 --data-dir 走")

    def test_a_named_instance_gets_its_own_mutex_and_record(self):
        import os

        os.environ.pop("COMFYBATCH_INSTANCE_NAME", None)
        default_mutex = app_module.instance_mutex_name()
        default_record = app_module.instance_record_path()

        os.environ["COMFYBATCH_INSTANCE_NAME"] = "devtest"
        self.assertNotEqual(default_mutex, app_module.instance_mutex_name())
        self.assertNotEqual(default_record, app_module.instance_record_path())
        self.assertIn("devtest", app_module.instance_mutex_name())

    def test_a_blank_name_falls_back_to_default(self):
        import os

        os.environ["COMFYBATCH_INSTANCE_NAME"] = "   "
        self.assertEqual("default", app_module.instance_name())
        self.assertEqual(app_module.INSTANCE_MUTEX_NAME, app_module.instance_mutex_name())

    def test_the_launcher_offers_a_way_out_of_a_stale_lock(self):
        from fakes import app_source

        source = app_source()
        self.assertIn("--force-new-instance", source)
        self.assertIn("--instance-name", source)
        self.assertIn("--force-new-instance 强制启动一个新实例", source,
                      "无法联系已有实例时必须告诉用户怎么继续")


class PageLiveSyncTests(unittest.TestCase):
    def test_page_connects_the_event_stream(self):
        html = page_source()
        for marker in ("connectEvents", "EventSource", "/api/events", "/api/lease/claim",
                       "/api/lease/release", "withClient", "isReadOnly", "takeOverLease",
                       "applyStatus", "applyLease", "onActivated"):
            with self.subTest(marker=marker):
                self.assertIn(marker, html)

    def test_polling_remains_as_a_fallback(self):
        """SSE is the primary channel, but a drop must not leave a dead page."""
        html = page_source()
        self.assertIn("setTimeout(connectEvents", html)
        self.assertIn("/api/status?client_id=", html)

    def test_read_only_pages_mirror_the_editor(self):
        """A viewer must actually follow the editor, not keep a stale copy."""
        html = page_source()
        self.assertIn("(!bundle||(typeof isReadOnly==='function'&&isReadOnly()))", html)

    def test_read_only_pages_explain_and_offer_takeover(self):
        html = page_source()
        self.assertIn("只读", html)
        self.assertIn("接管编辑", html)

    def test_write_buttons_are_gated(self):
        html = page_source()
        for name in ("preflightAction", "startBatch"):
            start = html.find(f"async function {name}(")
            self.assertNotEqual(-1, start, name)
            self.assertIn("isReadOnly", html[start:start + 200], f"{name} 必须检查只读状态")


if __name__ == "__main__":
    unittest.main()
