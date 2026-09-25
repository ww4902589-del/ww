"""分段与续跑的两条路由，走真实 HTTP。

``test_app_routes.py`` 存在的理由是：一条会阻塞的路由会把用户的页面冻住。
这两条路由是同一类风险面——它们要释放一个正握着运行名额的队列——所以用同样的
办法对待：临时端口上的真实 handler、真实请求、背后是真实批次。

与 ``test_app_routes.py`` 分开成类，是为了让这里对 ``APP`` 的替换不会影响那边的
断言（那边的注释已经记过一次同样的坑）。
"""

from __future__ import annotations

import base64
import json
import os
import pathlib
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from fakes import (  # noqa: E402
    ScriptedComfy,
    install_static_assets,
    make_comfy_tree,
    ui_workflow,
    write_workflow,
)

import comfybatch_nodeschema  # noqa: E402
import comfybatch_v2_app as app_module  # noqa: E402
from comfybatch_v2_app import Application, Handler  # noqa: E402
from comfybatch_v2_core import BatchRunner, RunProgressStore  # noqa: E402

RESPONSE_TIMEOUT = 20
#: A batch of four with two per segment: two boundaries to reason about.
BATCH_SIZE = 4
SEGMENT_SIZE = 2
#: 工作流里的 UNETLoader / 样式节点值必须落在 ``object_info.json`` 快照给的候选里，
#: 否则预检会在「资源清单」和「候选列表」两关把批次挡在门外——那是预检该做的事，
#: 挡下来只说明夹具用的是占位值，不是真实值。
MODEL = "krea2_turbo_int8_convrot.safetensors"
STYLE = "fooocus_styles"


def aligned_workflow(model: str = MODEL, style: str = STYLE, prompt: str = "old prompt") -> dict:
    """``ui_workflow`` 的占位值换成真快照认得的候选值。

    只看 ``easy stylesSelector`` 的 styles（节点 52）—— ``ui_workflow`` 为了通用
    写死了 ``old_styles``，而真快照的候选从 ``fooocus_styles`` 起。
    """
    graph = ui_workflow(model=model, prompt=prompt)
    for node in graph["nodes"]:
        if node.get("id") == 52:
            node["widgets_values"][0] = style
    return graph


class RouteClient(ScriptedComfy):
    """``ScriptedComfy`` plus the ``json`` probe ``validate_start`` always makes."""

    def json(self, path, method="GET", payload=None, timeout=30):
        return {"system": "ok"}


class SegmentRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._temp = tempfile.TemporaryDirectory()
        cls.root = pathlib.Path(cls._temp.name)
        # 快照默认写在应用数据目录；测试必须把它挪开，否则会往用户真实的
        # runs/ 里写批次进度。
        cls._previous_data_dir = os.environ.get("COMFYBATCH_DATA_DIR")
        os.environ["COMFYBATCH_DATA_DIR"] = cls._temp.name

        comfy = make_comfy_tree(cls.root)
        cls.comfy = comfy
        (comfy / "models" / "diffusion_models" / MODEL).write_bytes(b"")
        cls.workflow = write_workflow(cls.root / "workflow.json", aligned_workflow())
        # 类级默认值；``setUp`` 会按用例换成独立的快照目录。
        cls.store = RunProgressStore(cls.root / "runs")

        app = Application(settings_path=cls.root / "settings.json", backup_settings_path=None)
        app.comfy_root = comfy
        app.workflow_roots = [cls.root]
        app.output_root = cls.root / "out"
        # 这些用例要走完整的 ``/api/start`` 预检，所以 schema 必须是工作流里
        # 每一个节点都在的那种。手写一份最小 schema 只会让预检报「未安装该节点
        # 类型」——预检挡住的正是它该挡的东西，问题出在夹具而不是产品。
        # 与其他几个测试一致，直接吃仓库里那份真 object_info 快照。
        cls.schema = comfybatch_nodeschema.NodeSchemaRegistry.from_path(
            pathlib.Path(__file__).resolve().parent / "fixtures" / "object_info.json"
        )
        assert cls.schema.class_types, "object_info.json 夹具没读到节点，预检会全线报缺节点"
        app.schema = cls.schema
        comfybatch_nodeschema.set_active_registry(cls.schema)
        cls.replace_runner(app)

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
        if cls._previous_data_dir is None:
            os.environ.pop("COMFYBATCH_DATA_DIR", None)
        else:
            os.environ["COMFYBATCH_DATA_DIR"] = cls._previous_data_dir
        cls._temp.cleanup()

    @classmethod
    def replace_runner(cls, app) -> None:
        """Install a fresh runner over the *current test's* snapshot store.

        「重启」用例要的就是这个：换掉进程内的一切，只留磁盘上的快照。
        所以新 runner 必须指向 ``setUp`` 给本用例建的那份目录，
        否则它扫的是一个空目录，页面自然看不到可续跑的批次。
        """
        runner = BatchRunner(cls.comfy, RouteClient(output_file=cls.comfy / "output" / "seg.png"), progress_store=cls.store)
        runner.adapter_registry = cls.schema
        app.runner = runner

    # ------------------------------------------------------------------ helpers

    @classmethod
    def request(cls, path: str, payload=None) -> tuple[int, str]:
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

    @classmethod
    def url(cls, path: str) -> str:
        return f"http://127.0.0.1:{cls.port}{path}"

    @classmethod
    def status(cls) -> dict:
        _code, body = cls.request("/api/status")
        return json.loads(body)

    @classmethod
    def wait_for_status(cls, expected: str, timeout: float = 20.0) -> dict:
        deadline = time.time() + timeout
        payload = cls.status()
        while payload["status"]["status"] != expected and time.time() < deadline:
            time.sleep(0.02)
            payload = cls.status()
        return payload

    @classmethod
    def import_bundle(cls, count: int = BATCH_SIZE) -> None:
        raw = json.dumps({"items": [{"title": f"分段{i:02d}", "prompt": f"提示词 {i}"} for i in range(1, count + 1)]}).encode("utf-8")
        code, body = cls.request("/api/import", {
            "filename": "segment.json", "base64": base64.b64encode(raw).decode("ascii"),
        })
        assert code == 200, body

    @classmethod
    def start_payload(cls, **overrides) -> dict:
        payload = {
            "workflow_path": str(cls.workflow),
            "model": MODEL,
            "aspect_ratio": "16:9 (Widescreen)",
            "megapixels": 0.6,
            "output_dir": str(cls.root / "out"),
            # 夹具不是真 PNG，打开画质检查会把它判成坏图；分段与续跑不依赖画质。
            "single_subject_guard": False,
            "max_retries": 0,
            "segment_size": SEGMENT_SIZE,
        }
        payload.update(overrides)
        return payload

    def setUp(self):
        # 每个用例都从一个空闲 runner 和空快照目录开始，避免相互影响。
        # 目录按用例命名，同时写回类属性，好让 ``replace_runner`` 指的也是它。
        cls = type(self)
        cls.store = RunProgressStore(cls.root / f"runs-{self._testMethodName}")
        app_module.APP.runner = BatchRunner(
            cls.comfy, RouteClient(output_file=cls.comfy / "output" / "seg.png"), progress_store=cls.store
        )
        app_module.APP.runner.adapter_registry = cls.schema
        app_module.APP.bundle = None
        app_module.APP.last_preflight = {}

    # -------------------------------------------------------------------- tests

    def test_status_reports_whether_a_run_can_be_resumed(self):
        payload = type(self).status()

        self.assertIn("resumable", payload, "状态里没有 resumable，页面无从显示续跑")
        self.assertIsNone(payload["resumable"], "还没跑过批次却已经报告可续跑")

    def test_a_batch_stops_at_the_segment_boundary_over_http(self):
        cls = type(self)
        cls.import_bundle()

        code, body = cls.request("/api/start", cls.start_payload())
        self.assertEqual(200, code, body)

        payload = cls.wait_for_status("segment")

        self.assertEqual("segment", payload["status"]["status"], "批次没有在分段边界停下")
        self.assertEqual(SEGMENT_SIZE + 1, payload["status"]["next_index"])
        self.assertEqual(BATCH_SIZE, payload["status"]["total"])
        self.assertEqual(SEGMENT_SIZE, len(payload["status"]["results"]))
        # 队列还活着的时候不能同时劝人续跑：那是往同一个 run 里塞第二个队列。
        self.assertIsNone(payload["resumable"])

    def test_start_runs_only_the_requested_original_numbers(self):
        cls = type(self)
        cls.import_bundle(count=6)
        with mock.patch.object(app_module.APP, "validate_start", wraps=app_module.APP.validate_start) as preflight:
            code, body = cls.request("/api/start", cls.start_payload(task_range="3-4", segment_size=0))
        self.assertEqual(200, code, body)
        self.assertEqual(["分段03", "分段04"], [item.title for item in preflight.call_args.args[1].items])

        payload = cls.wait_for_status("completed")
        self.assertEqual(2, payload["status"]["total"])
        self.assertEqual([3, 4], [row["index"] for row in payload["status"]["results"]])
        self.assertEqual([3, 4], cls.store.latest()["selected_indexes"])
        self.assertTrue(payload["status"]["audit"], "非 1 开始的范围没有留下图审计")

    def test_invalid_task_range_is_a_clean_400(self):
        cls = type(self)
        cls.import_bundle(count=6)
        code, body = cls.request("/api/start", cls.start_payload(task_range="99"))
        self.assertEqual(400, code, body)
        self.assertFalse(json.loads(body)["ok"])

    def test_manual_preflight_uses_the_same_selected_numbers(self):
        cls = type(self)
        cls.import_bundle(count=6)
        with mock.patch.object(app_module.APP, "validate_start", wraps=app_module.APP.validate_start) as preflight:
            code, body = cls.request("/api/preflight", cls.start_payload(task_range="3-4"))
        self.assertEqual(200, code, body)
        self.assertEqual(["分段03", "分段04"], [item.title for item in preflight.call_args.args[1].items])

    def test_next_segment_is_reachable_and_finishes_the_batch(self):
        cls = type(self)
        cls.import_bundle()
        cls.request("/api/start", cls.start_payload())
        cls.wait_for_status("segment")

        code, body = cls.request("/api/segment/next", {})
        self.assertEqual(200, code, body)

        payload = cls.wait_for_status("completed")

        self.assertEqual(BATCH_SIZE, payload["status"]["completed"], "「下一段」没有把剩下的任务跑完")
        self.assertIsNone(payload["status"]["next_index"])
        self.assertIsNone(payload["resumable"], "全部跑完了却还在劝人续跑")

    def test_next_segment_without_a_waiting_segment_is_a_clean_400(self):
        status, body = type(self).request("/api/segment/next", {})

        self.assertEqual(400, status, body)
        self.assertFalse(json.loads(body)["ok"])
        self.assertIn("分段", body)

    def test_resume_without_a_snapshot_is_a_clean_400(self):
        status, body = type(self).request("/api/segment/resume", {})

        self.assertEqual(400, status, body)
        self.assertIn("续跑", body)

    def test_the_resume_route_continues_the_run_and_gives_the_page_its_prompts_back(self):
        cls = type(self)
        cls.import_bundle()
        cls.request("/api/start", cls.start_payload())
        cls.wait_for_status("segment")

        # 模拟一次重启：新进程、新 runner、新客户端，只剩磁盘上的快照。
        cls.replace_runner(app_module.APP)
        app_module.APP.bundle = None

        payload = cls.status()
        self.assertIsNotNone(payload["resumable"], "重启后页面看不到可续跑的批次")
        self.assertEqual(SEGMENT_SIZE + 1, payload["resumable"]["next_index"])

        code, body = cls.request("/api/segment/resume", {})
        self.assertEqual(200, code, body)
        response = json.loads(body)

        self.assertTrue(response["ok"])
        self.assertIsNotNone(response["bundle"], "续跑后没有把提示词合集还给页面")
        self.assertEqual(BATCH_SIZE, len(response["bundle"]["items"]))

        finished = cls.wait_for_status("completed")
        self.assertEqual(BATCH_SIZE, finished["status"]["completed"], "续跑没有把剩下的跑完")
        # 干净跑完时 snapshot_error 是空串（见 core 的状态骨架），非空即代表快照没写成。
        self.assertEqual("", finished["status"]["snapshot_error"], "续跑过程中快照落盘报错了")


if __name__ == "__main__":
    unittest.main()
