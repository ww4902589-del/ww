"""Every HTTP route must respond.

Motivation: during development the three review routes were written to call
``self._read_json()`` again, even though ``do_POST`` had already consumed the
request body. The second read blocks on a socket that will never deliver more
bytes, so ``/api/review/confirm`` hung until the client gave up. Nothing in the
unit suite could see it; only a real request did.

``EveryRouteRespondsTests`` starts the real handler on an ephemeral port and
requires a response from every route, so a hang fails the suite instead of
freezing a user's page.
"""

from __future__ import annotations

import json
import pathlib
import re
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from fakes import (  # noqa: E402
    SOURCE_ROOT,
    ConnectedClient,
    html_source,
    install_static_assets,
    make_comfy_tree,
    page_source,
    ui_workflow,
    write_workflow,
)

import comfybatch_nodeschema  # noqa: E402
import comfybatch_v2_app as app_module  # noqa: E402
from comfybatch_v2_app import Application, Handler  # noqa: E402
from comfybatch_v2_core import ResultReviewStore  # noqa: E402

#: Long enough for real work, short enough that a hang fails rather than hangs CI.
RESPONSE_TIMEOUT = 20


class BodyReadIntegrityTests(unittest.TestCase):
    """Static guard for the exact defect that caused the hang."""

    def test_do_post_reads_the_request_body_exactly_once(self):
        source = (SOURCE_ROOT / "comfybatch_v2_app.py").read_text(encoding="utf-8")
        calls = len(re.findall(r"self\._read_json\(\)", source))
        self.assertEqual(
            1, calls,
            "do_POST 只能读取一次请求正文；重复读取会阻塞在已被消费的 socket 上，导致接口永久挂起",
        )

    def test_every_post_route_uses_the_already_read_value(self):
        """Routes after the first must branch on ``value``, not read again."""
        source = (SOURCE_ROOT / "comfybatch_v2_app.py").read_text(encoding="utf-8")
        body = source.split("def do_POST", 1)[1]
        self.assertNotIn("_read_json", body.replace("value = self._read_json()", ""))


class EveryRouteRespondsTests(unittest.TestCase):
    """Start the real handler and require a response from every route."""

    @classmethod
    def setUpClass(cls):
        cls._temp = tempfile.TemporaryDirectory()
        root = pathlib.Path(cls._temp.name)
        comfy = make_comfy_tree(root)
        (comfy / "models" / "diffusion_models" / "Krea2-red.safetensors").write_bytes(b"")
        workflow = write_workflow(root / "workflow.json", ui_workflow(model="Krea2-red.safetensors"))

        app = Application(settings_path=root / "settings.json", backup_settings_path=None)
        app.comfy_root = comfy
        app.workflow_roots = [root]
        app.output_root = root / "out"
        app.runner.client = ConnectedClient()
        EveryRouteRespondsTests.TEST_SCHEMA = comfybatch_nodeschema.NodeSchemaRegistry.from_object_info({
            "KSampler": {"input": {"required": {
                "sampler_name": ["COMBO", {"options": ["euler", "dpmpp_2m"]}],
                "scheduler": ["COMBO", {"options": ["simple", "karras"]}],
                "steps": ["INT", {"default": 20, "min": 1, "max": 200}],
            }}},
            "KSamplerAdvanced": {"input": {"required": {
                "sampler_name": ["COMBO", {"options": ["euler", "dpmpp_2m"]}],
                "scheduler": ["COMBO", {"options": ["simple", "karras"]}],
                "add_noise": ["COMBO", {"options": ["enable", "disable"]}],
                "return_with_leftover_noise": ["COMBO", {"options": ["disable", "enable"]}],
                "steps": ["INT", {"default": 20, "min": 1, "max": 200}],
            }}},
            "UpscaleModelLoader": {"input": {"required": {
                "model_name": ["COMBO", {"options": ["RealESRGAN_x4plus_anime_6B.pth"]}],
            }}},
        })
        app.schema = EveryRouteRespondsTests.TEST_SCHEMA
        comfybatch_nodeschema.set_active_registry(app.schema)

        app_module.APP = app
        install_static_assets(app_module)

        cls.workflow_path = str(workflow)
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

    # ------------------------------------------------------------------ helpers

    @classmethod
    def url(cls, path: str) -> str:
        return f"http://127.0.0.1:{cls.port}{path}"

    @classmethod
    def request(cls, path: str, payload=None) -> tuple[int, str]:
        """Return ``(status, body)``; any HTTP status counts as a response."""
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        if payload is None:
            request = urllib.request.Request(cls.url(path), method="GET")
        else:
            request = urllib.request.Request(
                cls.url(path), data=json.dumps(payload).encode("utf-8"),
                method="POST", headers={"Content-Type": "application/json"},
            )
        try:
            with opener.open(request, timeout=RESPONSE_TIMEOUT) as response:
                return response.status, response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8")

    # -------------------------------------------------------------------- tests

    def test_get_routes_respond(self):
        for path in ("/", "/api/status", "/api/results", "/api/inspect", "/api/schema",
                     "/api/inventory", "/api/lora-profiles", "/api/style-lora-presets"):
            with self.subTest(path=path):
                status, body = self.request(path)
                # /api/inventory scans the real workflow directory, which is a temp root.
                self.assertIn(status, (200, 400), f"{path} returned {status}")
                if path != "/":
                    self.assertIn("ok", body)

    def test_unknown_route_is_a_clean_404(self):
        status, body = self.request("/api/does-not-exist")
        self.assertEqual(404, status)
        self.assertIn("未找到接口", body)

    def test_prompt_import_route_responds(self):
        raw = json.dumps({"items": [{"title": "验收", "prompt": "一条提示词"}]}).encode("utf-8")
        import base64

        status, body = self.request("/api/import", {
            "filename": "routes.json", "base64": base64.b64encode(raw).decode("ascii"),
        })
        self.assertEqual(200, status)
        self.assertTrue(json.loads(body)["ok"])

    def test_review_routes_respond_and_do_not_hang(self):
        """The regression: confirm/note/redo must answer, not block.

        State is reset first so the rejection assertions are deterministic: the
        store, runner and recovered-run id are all shared, so a sibling test's
        results would otherwise make these calls succeed.
        """
        import base64

        raw = json.dumps({"items": [{"title": "验收", "prompt": "一条提示词"}]}).encode("utf-8")
        self.request("/api/import", {"filename": "routes.json", "base64": base64.b64encode(raw).decode("ascii")})

        app = app_module.APP
        app.runner._state.update({"run_id": "", "results": [], "status": "idle"})
        original_reviews = app.reviews
        app._restored_run_id = ""
        app.reviews = ResultReviewStore(pathlib.Path(tempfile.mkdtemp()) / "empty")
        try:
            # With no run at all these are rejected, but they must answer at once.
            for path, payload in (
                ("/api/review/confirm", {"index": 1, "status": "已通过"}),
                ("/api/review/note", {"index": 1, "note": "备注"}),
                ("/api/review/redo", {"index": 1, "mode": "new_seed"}),
            ):
                with self.subTest(path=path):
                    status, body = self.request(path, payload)
                    self.assertEqual(400, status, f"{path} 应当被拒绝但必须立即返回")
                    self.assertFalse(json.loads(body)["ok"])
        finally:
            app.reviews = original_reviews

    def test_status_carries_the_full_review_payload(self):
        """The page polls only ``/api/status``, so it must be able to render the
        result board from that one response.

        Returning just a summary here left the review board permanently empty on
        a real run, because ``renderReview`` found no ``results`` array.
        """
        app = app_module.APP
        run_id = "status-payload-run"
        app.runner._state.update({"run_id": run_id, "results": [{
            "index": 1, "title": "状态载荷", "status": "completed", "copied_to": "",
            "generation": {"workflow_variant": "save:1"}, "quality": {}, "attempts": [],
        }]})
        app._restored_run_id = run_id

        status, body = self.request("/api/status")
        self.assertEqual(200, status)
        review = json.loads(body).get("review")
        self.assertIsInstance(review, dict, "/api/status 必须带完整审核载荷")
        for key in ("run_id", "results", "review", "statuses", "redo_modes"):
            with self.subTest(key=key):
                self.assertIn(key, review)
        self.assertTrue(review["results"], "审图看板需要 results 数组")
        self.assertEqual("save:1", review["results"][0]["generation"]["workflow_variant"])

    def test_review_responses_use_one_consistent_shape(self):
        """Every review-bearing response nests the payload under ``review``."""
        import base64

        raw = json.dumps({"items": [{"title": "形状", "prompt": "一条提示词"}]}).encode("utf-8")
        self.request("/api/import", {"filename": "routes.json", "base64": base64.b64encode(raw).decode("ascii")})

        app = app_module.APP
        run_id = "shape-run"
        app.runner._state.update({"run_id": run_id, "results": [{
            "index": 1, "title": "形状", "status": "completed", "copied_to": "",
            "generation": {}, "quality": {}, "attempts": [],
        }]})
        app._restored_run_id = run_id
        # Polling status first is what ingests the run into the review store;
        # the page does the same before a user can click 通过.
        self.request("/api/status")

        status, body = self.request("/api/review/confirm", {"index": 1, "status": "已通过"})
        self.assertEqual(200, status)
        payload = json.loads(body)
        self.assertIn("review", payload)
        self.assertNotIn("results", payload, "审核响应应统一嵌在 review 下，避免两种形状")
        self.assertEqual(1, payload["review"]["review"]["by_status"]["已通过"])

    def test_previous_run_is_recovered_after_a_restart(self):
        """Reopening the program must still show the last run's images and decisions.

        The runner keeps its state in memory only, so without recovery the result
        page would blank out on every restart even though the confirmation
        decisions are on disk.
        """
        import base64

        raw = json.dumps({"items": [{"title": "重启恢复", "prompt": "一条提示词"}]}).encode("utf-8")
        self.request("/api/import", {"filename": "routes.json", "base64": base64.b64encode(raw).decode("ascii")})

        app = app_module.APP
        run_id = "restart-recovery-run"
        app.reviews.ingest(run_id, [{
            "index": 1, "title": "重启恢复", "status": "completed", "copied_to": "",
            "generation": {}, "quality": {}, "attempts": [{"attempt": 1}],
        }])
        app.reviews.confirm(run_id, [1], "已通过", "重启前已通过")

        # Simulate a restart: no in-memory run state, recovery switched off.
        app.runner._state.update({"run_id": "", "results": [], "status": "idle"})
        app._restored_run_id = ""

        status, body = self.request("/api/results")
        self.assertEqual(200, status)
        payload = json.loads(body)["review"]
        self.assertTrue(payload["run_id"], "重启后应恢复最近一次运行的记录")
        self.assertTrue(payload["restored"], "恢复的结果应标记为 restored")
        matching = [item for item in payload["results"] if item["index"] == 1]
        self.assertTrue(matching)
        self.assertEqual("已通过", matching[0]["review_status"])
        self.assertEqual("重启前已通过", matching[0]["note"])

    def test_review_confirm_succeeds_with_recorded_results(self):
        """End-to-end through HTTP: ingest results, confirm, read them back."""
        raw = json.dumps({"items": [{"title": "验收", "prompt": "一条提示词"}]}).encode("utf-8")
        import base64

        self.request("/api/import", {"filename": "routes.json", "base64": base64.b64encode(raw).decode("ascii")})

        app = app_module.APP
        run_id = "route-test-run"
        app.runner._state.update({
            "run_id": run_id,
            "results": [{
                "index": 1, "title": "验收", "status": "completed",
                "copied_to": "", "generation": {}, "quality": {}, "attempts": [],
            }],
        })
        app.reviews.ingest(run_id, app.runner.status()["results"])

        status, body = self.request("/api/review/confirm", {"index": 1, "status": "已通过", "note": "通过"})
        self.assertEqual(200, status, body)
        payload = json.loads(body)
        self.assertTrue(payload["ok"])
        self.assertEqual(1, payload["review"]["review"]["by_status"]["已通过"])
        self.assertEqual("已通过", payload["review"]["results"][0]["review_status"])

    def test_redo_batch_endpoint_dispatches_to_one_batch_redo(self):
        """V2.19 batch redo: the endpoint validates cheap things itself and
        hands the validated selection to ``BatchRunner.redo_many`` exactly once."""
        app = app_module.APP
        calls: list[dict] = []

        class FakeRunner:
            def status(self):
                return {"run_id": "", "results": [], "status": "idle"}

            def redo_many(self, indexes, mode, note="", seed=None):
                calls.append({"indexes": list(indexes), "mode": mode, "note": note, "seed": seed})
                return {"run_id": "", "results": [], "status": "starting"}

        original_runner = app.runner
        app.runner = FakeRunner()
        try:
            status, body = self.request("/api/review/redo-batch", {
                "indexes": [1, 2], "mode": "new_seed", "note": "批量打回",
            })
            self.assertEqual(200, status, body)
            payload = json.loads(body)
            self.assertTrue(payload["ok"])
            self.assertEqual(2, payload["count"])
            self.assertEqual("new_seed", payload["mode"])
            self.assertEqual(
                [{"indexes": [1, 2], "mode": "new_seed", "note": "批量打回", "seed": None}],
                calls,
            )

            # edited_prompt stays a per-image-only mode, refused before the runner runs.
            status, body = self.request("/api/review/redo-batch", {"indexes": [1], "mode": "edited_prompt"})
            self.assertEqual(400, status)
            self.assertIn("改提示词", json.loads(body)["error"])

            # An empty selection is the user forgetting to tick anything.
            status, body = self.request("/api/review/redo-batch", {"indexes": [], "mode": "new_seed"})
            self.assertEqual(400, status)
            self.assertFalse(json.loads(body)["ok"])
            self.assertEqual(1, len(calls), "被拒绝的请求不得触达 runner")
        finally:
            app.runner = original_runner

    def test_delete_lora_profile_route_saves_then_deletes(self):
        name = f"qa-route-profile-{time.time_ns()}.safetensors"
        status, body = self.request("/api/save-lora-profile", {"name": name, "display_name": "QA路由"})
        self.assertEqual(200, status, body)

        status, body = self.request("/api/delete-lora-profile", {"name": name})
        self.assertEqual(200, status, body)
        payload = json.loads(body)
        self.assertTrue(payload["ok"])
        self.assertNotIn(name, payload["profiles"], "删除成功后档案列表里不应再有它")

        status, body = self.request("/api/delete-lora-profile", {"name": name})
        self.assertEqual(400, status)
        self.assertIn("已经不存在", json.loads(body)["error"])

    def test_open_folder_creates_a_missing_output_directory(self):
        base = pathlib.Path(tempfile.mkdtemp(prefix="comfybatch-open-folder-"))
        missing = base / "new-output" / "sub"
        try:
            # startfile would open a real Explorer window in the test process.
            with mock.patch("comfybatch_v2_app.os.startfile") as startfile:
                status, body = self.request("/api/open-folder", {"kind": "output", "path": str(missing)})
            self.assertEqual(200, status, body)
            payload = json.loads(body)
            self.assertTrue(payload["ok"])
            self.assertTrue(missing.is_dir(), "缺失的输出目录应被自动补建")
            startfile.assert_called_once_with(str(missing))
        finally:
            shutil.rmtree(base, ignore_errors=True)

    def test_open_folder_rejects_a_relative_output_path(self):
        with mock.patch("comfybatch_v2_app.os.startfile") as startfile:
            status, body = self.request("/api/open-folder", {"kind": "output", "path": "qa-relative-probe"})
            self.assertEqual(400, status, body)
            self.assertFalse(json.loads(body)["ok"])
            startfile.assert_not_called()
        # Defence against the failure mode this test guards: a relative path
        # must never be silently created beside the working directory.
        shutil.rmtree(pathlib.Path.cwd() / "qa-relative-probe", ignore_errors=True)

    def test_configuration_and_run_control_routes_respond(self):
        status, _body = self.request("/api/configure", {
            "comfy_root": str(app_module.APP.comfy_root),
            "comfy_url": "http://127.0.0.1:8188",
            "workflow_roots": [str(app_module.APP.workflow_roots[0])],
            "output_root": str(app_module.APP.output_root),
        })
        self.assertEqual(200, status)
        app_module.APP.runner.client = ConnectedClient()

        for path in ("/api/pause", "/api/resume", "/api/cancel", "/api/reload-schema"):
            with self.subTest(path=path):
                status, body = self.request(path, {})
                self.assertEqual(200, status, body)

    def test_preflight_route_responds_even_when_it_rejects(self):
        status, body = self.request("/api/preflight", {
            "workflow_path": self.workflow_path,
            "model": "Krea2-red.safetensors",
            "aspect_ratio": "16:9 (Widescreen)",
            "megapixels": 0.6,
        })
        self.assertIn(status, (200, 400))
        self.assertIn("ok", body)

    def test_blocked_preflight_still_explains_itself(self):
        """A 400 must carry the blocking problems and warnings, not just a message.

        Found on a real run: the page could only show "不能生成" because the
        structured payload was discarded when ``ok`` was false.
        """
        import base64

        raw = json.dumps({"items": [{"title": "预检", "prompt": "一条提示词"}]}).encode("utf-8")
        self.request("/api/import", {"filename": "routes.json", "base64": base64.b64encode(raw).decode("ascii")})
        app_module.APP.runner.client = ConnectedClient()

        status, body = self.request("/api/preflight", {
            "workflow_path": self.workflow_path,
            "model": "definitely-not-a-real-model.safetensors",
            "aspect_ratio": "16:9 (Widescreen)",
            "megapixels": 0.6,
        })
        self.assertEqual(400, status)
        payload = json.loads(body)
        self.assertFalse(payload["ok"])
        self.assertTrue(payload.get("error"))
        # The structured context the page needs to explain the failure.
        for key in ("preflight", "blocking", "audit", "workflow_path", "stale_rules",
                    "upscale_candidates", "model_directories", "rules"):
            with self.subTest(key=key):
                self.assertIn(key, payload)

    def test_stale_replacement_rule_is_reported_not_dropped(self):
        """Rules saved before the workflow changed must be surfaced, never silent."""
        import base64

        raw = json.dumps({"items": [{"title": "过期规则", "prompt": "一条提示词"}]}).encode("utf-8")
        self.request("/api/import", {"filename": "routes.json", "base64": base64.b64encode(raw).decode("ascii")})
        app = app_module.APP
        app.runner.client = ConnectedClient()

        fingerprint = app.workflow_fingerprint(self.workflow_path)
        app.resource_rules.remember(
            "000000000000", "11", "model_name", "SomethingElse.pth",
            workflow_path=self.workflow_path,
        )
        try:
            status, body = self.request("/api/inspect")
            self.assertEqual(200, status)
            inspect = json.loads(body)["inspect"]
            stale = [rule for rule in inspect["stale_rules"] if rule["workflow_path"] == self.workflow_path]
            self.assertEqual(1, len(stale), "改动工作流后旧规则应被列为过期并提示")
            self.assertEqual("000000000000", stale[0]["workflow_fingerprint"])
            self.assertNotEqual("000000000000", fingerprint)

            # Re-applying moves it onto the current fingerprint.
            status, _body = self.request("/api/resource/reapply", {"workflow_path": self.workflow_path})
            self.assertEqual(200, status)
            _status, body = self.request("/api/inspect")
            inspect = json.loads(body)["inspect"]
            self.assertEqual(
                [], [r for r in inspect["stale_rules"] if r["workflow_path"] == self.workflow_path],
                "沿用之后不应再被列为过期",
            )
            self.assertTrue(
                any(r["input_name"] == "model_name" for r in inspect["rules"]),
                "沿用之后规则应对当前指纹生效",
            )
        finally:
            app.resource_rules.forget(fingerprint, "11", "model_name")

    def test_params_endpoint_lists_the_workbench(self):
        # A sibling test calls /api/reload-schema, so restore the schema.
        app_module.APP.schema = EveryRouteRespondsTests.TEST_SCHEMA
        comfybatch_nodeschema.set_active_registry(app_module.APP.schema)
        app_module.APP.runner.client = ConnectedClient()
        status, body = self.request("/api/params")
        self.assertEqual(200, status)
        params = json.loads(body)["params"]
        groups = {group["id"]: group for group in params["groups"]}
        self.assertIn("common", groups)
        self.assertIn("advanced", groups)
        keys = {p["key"] for group in groups.values() for p in group["params"]}
        for expected in ("steps", "cfg", "denoise", "upscale_by", "upscale_model",
                         "tile_size", "sampler_name", "scheduler", "steps_stage1", "steps_stage2"):
            with self.subTest(key=expected):
                self.assertIn(expected, keys)
        # Every enum must carry its live candidates, or the page shows a free-text box.
        sampler = next(p for p in groups["advanced"]["params"] if p["key"] == "sampler_name")
        self.assertTrue(sampler.get("options"))

    def test_params_apply_rejects_bad_values_without_storing_them(self):
        # A sibling test calls /api/reload-schema, so restore the schema.
        app_module.APP.schema = EveryRouteRespondsTests.TEST_SCHEMA
        comfybatch_nodeschema.set_active_registry(app_module.APP.schema)
        app = app_module.APP
        app.runner.client = ConnectedClient()
        status, body = self.request("/api/params/apply", {
            "workflow_path": self.workflow_path, "scope": "workflow",
            "params": {"steps": 99999, "cfg": 7.5},
        })
        self.assertEqual(200, status)
        payload = json.loads(body)
        # The bad value is reported and, crucially, not persisted...
        self.assertEqual(1, len(payload["blocking"]))
        stored = payload["params"]["workflow_defaults"]
        self.assertNotIn("steps", stored, "越界值不能写进工作流默认值")
        # ...while the good value alongside it still is.
        self.assertEqual(7.5, stored.get("cfg"))
        # And it really is not in the file either.
        fingerprint = payload["fingerprint"]
        self.assertNotIn("steps", app.workflow_params.for_fingerprint(fingerprint))
        self.request("/api/params/clear", {"workflow_path": self.workflow_path, "keys": ["cfg"]})

    def test_params_apply_stores_workflow_defaults_by_fingerprint(self):
        # A sibling test calls /api/reload-schema, so restore the schema.
        app_module.APP.schema = EveryRouteRespondsTests.TEST_SCHEMA
        comfybatch_nodeschema.set_active_registry(app_module.APP.schema)
        app = app_module.APP
        app.runner.client = ConnectedClient()
        status, body = self.request("/api/params/apply", {
            "workflow_path": self.workflow_path, "scope": "workflow",
            "params": {"cfg": 7.5, "steps_stage2": 30},
        })
        self.assertEqual(200, status)
        payload = json.loads(body)
        self.assertEqual(app.workflow_fingerprint(self.workflow_path), payload["fingerprint"])
        defaults = payload["params"]["workflow_defaults"]
        self.assertEqual(7.5, defaults["cfg"])
        self.assertEqual(30, defaults["steps_stage2"])

        # And they survive a fresh Application on the same data directory.
        reopened = Application(settings_path=app.settings_path, backup_settings_path=None)
        fingerprint = reopened.workflow_fingerprint(self.workflow_path)
        self.assertEqual({"cfg": 7.5, "steps_stage2": 30}, reopened.workflow_params.for_fingerprint(fingerprint))

        # Clean up so ordering cannot affect other tests.
        self.request("/api/params/clear", {"workflow_path": self.workflow_path, "keys": ["cfg", "steps_stage2"]})

    def test_batch_scope_remembers_only_when_the_switch_is_on(self):
        """方案A(docs/15 §2)「记住这次的参数」：batch 应用默认不留痕，勾选开关才等于 remember()。"""
        # A sibling test calls /api/reload-schema, so restore the schema.
        app_module.APP.schema = EveryRouteRespondsTests.TEST_SCHEMA
        comfybatch_nodeschema.set_active_registry(app_module.APP.schema)
        app = app_module.APP
        app.runner.client = ConnectedClient()
        fingerprint = app.workflow_fingerprint(self.workflow_path)

        # Plain batch scope: nothing may be persisted.
        status, _body = self.request("/api/params/apply", {
            "workflow_path": self.workflow_path, "scope": "batch",
            "params": {"cfg": 6.5},
        })
        self.assertEqual(200, status)
        self.assertEqual({}, app.workflow_params.for_fingerprint(fingerprint),
                         "批次范围不能悄悄写进工作流默认值")

        # Same request with the explicit remember switch: now it persists.
        status, _body = self.request("/api/params/apply", {
            "workflow_path": self.workflow_path, "scope": "batch", "remember": True,
            "params": {"cfg": 6.5},
        })
        self.assertEqual(200, status)
        self.assertEqual({"cfg": 6.5}, app.workflow_params.for_fingerprint(fingerprint))

        # Clean up so ordering cannot affect other tests.
        self.request("/api/params/clear", {"workflow_path": self.workflow_path, "keys": ["cfg"]})

    def test_task_scope_writes_onto_selected_items_only(self):
        import base64

        raw = json.dumps({"items": [{"title": "一", "prompt": "p1"}, {"title": "二", "prompt": "p2"}]}).encode("utf-8")
        self.request("/api/import", {"filename": "routes.json", "base64": base64.b64encode(raw).decode("ascii")})
        app = app_module.APP

        status, _body = self.request("/api/params/apply", {
            "workflow_path": self.workflow_path, "scope": "task", "indexes": [2],
            "params": {"steps_stage2": 33},
        })
        self.assertEqual(200, status)
        first, second = app.bundle.items[0], app.bundle.items[1]
        self.assertNotIn("params", first.metadata.get("generation") or {})
        self.assertEqual({"steps_stage2": 33}, (second.metadata.get("generation") or {}).get("params"))

    def test_task_scope_without_a_selection_is_rejected(self):
        import base64

        raw = json.dumps({"items": [{"title": "一", "prompt": "p"}]}).encode("utf-8")
        self.request("/api/import", {"filename": "routes.json", "base64": base64.b64encode(raw).decode("ascii")})
        status, body = self.request("/api/params/apply", {
            "workflow_path": self.workflow_path, "scope": "task", "indexes": [], "params": {"steps": 20},
        })
        self.assertEqual(400, status)
        self.assertFalse(json.loads(body)["ok"])

    def test_preset_routes_respond(self):
        for path, payload in (
            ("/api/save-lora-profile", {"name": "x.safetensors", "display_name": "示例", "trigger_words": ["a"]}),
            ("/api/save-style-lora-preset", {"name": "路线测试", "styles": [], "loras": [{"name": "x.safetensors", "strength": 0.5}]}),
            ("/api/delete-style-lora-preset", {"id": "nonexistent"}),
            ("/api/assign-style-lora-preset", {"preset_id": "", "indexes": [1]}),
            ("/api/assign-image-preset", {"preset_id": "square-m", "indexes": [1]}),
            ("/api/update-bundle", {"items": [{"title": "验收", "prompt": "一条提示词"}]}),
            ("/api/remap-import", {"mapping": {"title": "", "positive": "", "negative": "", "metadata": ""}}),
        ):
            with self.subTest(path=path):
                status, body = self.request(path, payload)
                self.assertIn(status, (200, 400), f"{path} returned {status}")
                self.assertIn("ok", body)


if __name__ == "__main__":
    unittest.main()
