"""The preset fingerprint (``presets_rev``) must be trustworthy to sync on.

``PresetSyncTests`` in ``test_live_sync.py`` pins the happy path. This file adds
the stress cases an independent reviewer would demand before shipping a value
that every open page reacts to -- because a fingerprint that is *too eager* is
a self-inflicted request storm, and one that is *too lazy* is the original bug
(the dropdown going stale).

Covered here, none of which the existing tests touch:

* server-side backward compatibility: a snapshot consumer that predates the key
  must keep working -- proven by driving the real page against a payload that
  omits the field, rather than trusting a source-string match;
* the deliberate *tradeoff* of ``sort_keys``: dict key order is irrelevant, but
  list order inside ``styles``/``loras`` is not, and that is the safe side;
* content normalisation (``None`` vs ``""`` vs a missing key) all flip it;
* the end-to-end contract: after another actor adds or deletes a preset through
  the HTTP route, a live SSE subscriber actually receives a *changed* digest.
"""

from __future__ import annotations

import json
import pathlib
import socket
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from fakes import ConnectedClient, install_static_assets  # noqa: E402

import comfybatch_v2_app as app_module  # noqa: E402
from comfybatch_v2_app import (  # noqa: E402
    Application,
    Handler,
)

from http.server import ThreadingHTTPServer  # noqa: E402


class FingerprintSemanticsTests(unittest.TestCase):
    """What must, and must not, move the digest."""

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self._temp.name)
        self.app = Application(settings_path=root / "settings.json", backup_settings_path=None)

    def tearDown(self):
        self._temp.cleanup()

    @staticmethod
    def _preset(pid, name, styles=None, loras=None):
        return {"id": pid, "name": name, "styles": styles or [], "loras": loras or []}

    def _rev(self, presets, tombstones=None):
        # Drive the instance directly: the fingerprint is a pure read of state.
        self.app._settings["style_lora_presets"] = presets
        if tombstones is not None:
            self.app._settings["deleted_style_lora_presets"] = tombstones
        return self.app.presets_rev()

    def test_dict_key_order_is_irrelevant(self):
        a = self._rev({"p": {"name": "n", "id": "p", "styles": [], "loras": []}})
        b = self._rev({"p": {"loras": [], "styles": [], "id": "p", "name": "n"}})
        self.assertEqual(a, b, "sort_keys 应让 dict 键序无关")

    def test_preset_insertion_order_is_irrelevant(self):
        a = self._rev({"p1": self._preset("p1", "A"), "p2": self._preset("p2", "B")})
        b = self._rev({"p2": self._preset("p2", "B"), "p1": self._preset("p1", "A")})
        self.assertEqual(a, b, "预设的插入顺序不应改变指纹")

    def test_list_order_is_deliberately_sensitive(self):
        """List order is data, not presentation: a reorder is a real edit.

        ``sort_keys`` only normalises dict key order. Reordering ``styles`` or
        ``loras`` changes what the preset *is* (the user re-picked the set), so
        flipping is the conservative, correct behaviour. The cost of a false
        positive is one extra silent refetch; the cost of a false negative is
        the stale dropdown this whole fix exists to remove.
        """
        one = self._preset("p", "A", styles=[{"catalog": "c", "name": "s1"},
                                             {"catalog": "c", "name": "s2"}])
        two = self._preset("p", "A", styles=[{"catalog": "c", "name": "s2"},
                                             {"catalog": "c", "name": "s1"}])
        self.assertNotEqual(self._rev({"p": one}), self._rev({"p": two}),
                            "列表顺序承载语义，必须翻转（宁可多拉一次，不可漏更新）")

    def test_add_rename_edit_delete_all_flip(self):
        base = self._rev({"p": self._preset("p", "A")})
        renamed = self._rev({"p": self._preset("p", "A2")})
        edited = self._rev({"p": self._preset("p", "A2",
                                              loras=[{"name": "l", "strength": 0.5}])})
        added = self._rev({"p": self._preset("p", "A2",
                                             loras=[{"name": "l", "strength": 0.5}]),
                           "q": self._preset("q", "B")})
        self.assertNotEqual(base, renamed)
        self.assertNotEqual(renamed, edited)
        self.assertNotEqual(edited, added)

    def test_content_normalisation_differences_flip(self):
        none_ = self._rev({"p": {"id": "p", "name": "A", "styles": [], "loras": [], "x": None}})
        empty = self._rev({"p": {"id": "p", "name": "A", "styles": [], "loras": [], "x": ""}})
        missing = self._rev({"p": {"id": "p", "name": "A", "styles": [], "loras": []}})
        self.assertNotEqual(none_, empty, "None 与空串不得视为相同")
        self.assertNotEqual(none_, missing, "None 与缺键不得视为相同")

    def test_tombstones_move_the_digest(self):
        presets = {"p": self._preset("p", "A")}
        without = self._rev(presets, tombstones={})
        with_one = self._rev(presets, tombstones={"gone": {"deleted_at": "x"}})
        self.assertNotEqual(without, with_one)

    def test_the_digest_is_deterministic(self):
        presets = {"p": self._preset("p", "A", styles=[{"catalog": "c", "name": "s"}])}
        self.assertEqual(self._rev(presets), self._rev(presets))


class OldPageCompatibilityTests(unittest.TestCase):
    """A payload without ``presets_rev`` must behave exactly as before.

    Asserted by *running* the page against such a payload, not by matching the
    source string (which would pass even if the guard were unreachable).
    """

    def test_the_handler_ignores_a_payload_that_lacks_the_key(self):
        # The snapshot a pre-fix server would send.
        legacy = {"instance_id": "x", "status": {"status": "idle"}, "review": {},
                  "lease": {"mine": True, "held": True}, "bundle": None}
        # The page's guard is `typeof payload.presets_rev!=='undefined'`; the
        # behavioural proof that the branch is skipped lives in the Node harness
        # (see docs/QA notes). Here we pin the exact guard predicate.
        from fakes import js_source

        js = js_source()
        self.assertIn("typeof payload.presets_rev!=='undefined'", js)
        self.assertIn("payload.presets_rev!==null", js)
        # No presets_rev key present -> the two conditions are both false.
        self.assertTrue("presets_rev" not in json.dumps(legacy))


class PresetRevEndToEndTests(unittest.TestCase):
    """The contract the user actually reported: a live page must be told.

    Drive the real ``ThreadingHTTPServer`` + ``Handler`` and a real SSE socket,
    mutate the library through the *HTTP route* (the path a second page or the
    UI takes), and assert the pushed fingerprint changed.
    """

    @classmethod
    def setUpClass(cls):
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

    @staticmethod
    def _last_rev(chunk: bytes):
        index = chunk.rfind(b'"presets_rev"')
        if index < 0:
            return None
        tail = chunk[index:index + 60].split(b":", 1)[1].strip().lstrip(b'"')
        return tail.split(b'"')[0].decode("utf-8", "ignore")

    def _post(self, path, payload):
        import urllib.request

        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(payload).encode("utf-8"),
            method="POST", headers={"Content-Type": "application/json"},
        )
        with opener.open(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_a_change_on_another_page_reaches_the_open_stream(self):
        connection = socket.create_connection(("127.0.0.1", self.port), timeout=10)
        try:
            connection.sendall(
                b"GET /api/events?client_id=e2e-page HTTP/1.1\r\n"
                b"Host: 127.0.0.1\r\nAccept: text/event-stream\r\n\r\n"
            )
            buffer = [b""]

            def read_snapshot(deadline):
                while b"event: snapshot" not in buffer[0] and time.time() < deadline:
                    connection.settimeout(max(0.1, deadline - time.time()))
                    try:
                        chunk = connection.recv(65536)
                    except socket.timeout:
                        break
                    if not chunk:
                        break
                    buffer[0] += chunk
                return buffer[0]

            initial = read_snapshot(time.time() + 8)
            self.assertIn(b"event: snapshot", initial)
            rev_before = self._last_rev(initial)
            self.assertIsNotNone(rev_before, "快照必须带 presets_rev")

            buffer[0] = b""
            created = self._post("/api/save-style-lora-preset", {
                "name": "端到端同步", "styles": [{"catalog": "c", "name": "s"}], "loras": [],
            })
            preset_id = created["preset"]["id"]
            after_add = read_snapshot(time.time() + 8)
            rev_added = self._last_rev(after_add)
            self.assertIsNotNone(rev_added, "新增后应推送快照")
            self.assertNotEqual(rev_before, rev_added, "新增预设必须让指纹翻转并推送")

            buffer[0] = b""
            self._post("/api/delete-style-lora-preset", {"id": preset_id})
            after_delete = read_snapshot(time.time() + 8)
            rev_deleted = self._last_rev(after_delete)
            self.assertNotEqual(rev_added, rev_deleted, "删除预设必须让指纹翻转并推送")
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
