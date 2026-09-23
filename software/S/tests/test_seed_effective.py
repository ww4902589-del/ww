"""真实种子：读回、判定、如实显示。

「种子显示并确保实际生效」的核心不是再多印一行数字，而是分清三件事：

* **提交种子** —— 我们请求 ComfyUI 用的值；
* **实际种子** —— 这张图真正用的值，权威来源是成图自己内嵌的提示（其次才是
  ``/history``，因为历史会被清、成图不会）；
* **未核对** —— 两处都读不到。它必须是第三种状态，不能被写成"种子没生效"。

第三条是最容易被做错的一条：把"没读到"当成"没生效"，就会让每一张旧图、
每一个改过元数据的文件都背上一个假的罪名。
"""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from fakes import SOURCE_ROOT, api_workflow, write_workflow  # noqa: E402

from comfybatch_nodeschema import (  # noqa: E402
    NodeSchemaRegistry,
    executed_graph,
    seed_plan,
    seed_report,
    set_active_registry,
    workflow_seed_map,
)
from comfybatch_v2_core import (  # noqa: E402
    BatchConfig,
    BatchRunner,
    ImageSeedInspector,
    Krea2WorkflowAdapter,
    real_seed_evidence,
)

FIXTURES = SOURCE_ROOT.parent / "tests" / "fixtures"


def png_with_prompt(path: pathlib.Path, graph: dict | None) -> pathlib.Path:
    """Write a PNG that carries a ComfyUI-style ``prompt`` chunk (or none)."""
    from PIL import Image, PngImagePlugin

    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (48, 48), "white")
    if graph is None:
        image.save(path)
        return path
    info = PngImagePlugin.PngInfo()
    info.add_text("prompt", json.dumps(graph, ensure_ascii=False))
    image.save(path, pnginfo=info)
    return path


def seed_graph(seed: int) -> dict:
    """A minimal API graph whose concrete seed lives on a ``Seed (rgthree)`` node."""
    graph = api_workflow()
    graph["20"] = {"class_type": "Seed (rgthree)", "inputs": {"seed": seed}}
    graph["5"]["inputs"]["seed"] = ["20", 0]
    return graph


class ExecutedGraphTests(unittest.TestCase):
    def test_reads_the_graph_out_of_a_history_entry(self):
        graph = {"9": {"class_type": "KSampler", "inputs": {"seed": 5}}}
        self.assertEqual(graph, executed_graph({"prompt": [1, "pid", graph, {}, []]}))
        self.assertEqual(graph, executed_graph({"prompt": graph}))

    def test_unreadable_history_is_empty_not_guessed(self):
        for entry in (None, {}, {"prompt": "nope"}, {"prompt": [1, 2]}, {"prompt": [1, 2, "x"]}, []):
            self.assertEqual({}, executed_graph(entry), f"{entry!r} 不该被读成图")


class SeedReportTests(unittest.TestCase):
    def test_matching_seeds_are_effective(self):
        report = seed_report({"20.seed": 12345}, {"20.seed": 12345})
        self.assertTrue(report["available"])
        self.assertTrue(report["effective"])
        self.assertEqual({}, report["mismatched"])

    def test_a_different_seed_names_both_values(self):
        report = seed_report({"20.seed": 12345}, {"20.seed": 999})
        self.assertFalse(report["effective"])
        self.assertEqual({"20.seed": {"submitted": 12345, "executed": 999}}, report["mismatched"])

    def test_a_submitted_seed_that_never_reached_the_graph_is_reported(self):
        report = seed_report({"20.seed": 1, "13.seed": 2}, {"20.seed": 1})
        self.assertFalse(report["effective"])
        self.assertEqual(["13.seed"], report["missing"])

    def test_an_unreadable_seed_is_unknown_never_failed(self):
        report = seed_report({"20.seed": 1}, {}, available=False)
        self.assertFalse(report["available"])
        self.assertIsNone(report["effective"], "读不到实际种子时不允许判定为 False")
        self.assertEqual({}, report["mismatched"])


class WorkflowSeedMapTests(unittest.TestCase):
    """The map must never dress a leftover widget value up as a seed."""

    def setUp(self):
        self.registry = NodeSchemaRegistry.from_path(FIXTURES / "object_info.json")
        set_active_registry(self.registry)

    def test_a_widget_promoted_to_a_link_is_not_reported_as_its_value(self):
        workflow = {
            "nodes": [
                {"id": 35, "type": "Seed (rgthree)", "mode": 0, "inputs": [],
                 "widgets_values": [685212704886219, "", "", ""],
                 "outputs": [{"name": "SEED", "type": "INT", "links": [34]}]},
                {"id": 28, "type": "KSampler", "mode": 0,
                 "inputs": [
                     {"name": "seed", "type": "INT", "widget": {"name": "seed"}, "link": 34},
                     {"name": "steps", "type": "INT", "widget": {"name": "steps"}, "link": None},
                 ],
                 "widgets_values": [1044160720030426, "fixed", 8]},
            ],
            "links": [[34, 35, 0, 28, 4, "INT"]],
        }
        found = workflow_seed_map(workflow, self.registry)
        self.assertEqual(685212704886219, found["35"]["value"])
        self.assertIsNone(found["28"]["value"], "连了线的 seed 必须报 None，而不是 leftover 值")
        self.assertEqual("35", found["28"]["source"])

    def test_branch_filter_narrows_the_answer(self):
        workflow = {
            "nodes": [
                {"id": 1, "type": "Seed (rgthree)", "mode": 0, "inputs": [], "widgets_values": [11]},
                {"id": 2, "type": "Seed (rgthree)", "mode": 4, "inputs": [], "widgets_values": [22]},
            ],
            "links": [],
        }
        self.assertEqual({"1": 11, "2": 22}, {k: v["value"] for k, v in workflow_seed_map(workflow, self.registry, include_muted=True).items()})
        self.assertEqual({"1"}, set(workflow_seed_map(workflow, self.registry, ["1"])))


class SeedPlanTests(unittest.TestCase):
    def setUp(self):
        self.registry = NodeSchemaRegistry.from_path(FIXTURES / "object_info.json")
        set_active_registry(self.registry)

    def test_the_seed_generator_makes_the_consumers_controllable(self):
        """The rgthree shape every real Krea2 workflow uses."""
        workflow = {
            "nodes": [
                {"id": 20, "type": "Seed (rgthree)", "mode": 0, "inputs": [], "widgets_values": [-1],
                 "outputs": [{"name": "SEED", "type": "INT", "links": [25, 37]}]},
                {"id": 21, "type": "KSamplerAdvanced", "mode": 0,
                 "inputs": [{"name": "noise_seed", "type": "INT", "widget": {"name": "noise_seed"}, "link": 37}],
                 "widgets_values": [123, "randomize"]},
            ],
            "links": [[37, 20, 0, 21, 1, "INT"]],
        }
        plan = seed_plan(workflow, self.registry)
        self.assertEqual({"20.seed": -1}, plan["pinnable"])
        self.assertEqual([], plan["uncontrollable"])
        self.assertTrue(plan["linked"]["21.noise_seed"]["controllable"])
        self.assertEqual("", plan["warning"])

    def test_a_seed_from_a_node_without_a_seed_parameter_is_flagged(self):
        """The silent case: a fixed seed that reaches nothing at all."""
        workflow = {
            "nodes": [
                {"id": 2, "type": "PrimitiveInt", "mode": 0,
                 "inputs": [{"name": "value", "widget": {"name": "value"}, "link": None}],
                 "widgets_values": [12345], "outputs": [{"name": "INT", "type": "INT", "links": [77]}]},
                {"id": 3, "type": "KSampler", "mode": 0,
                 "inputs": [{"name": "seed", "type": "INT", "widget": {"name": "seed"}, "link": 77}],
                 "widgets_values": [1]},
            ],
            "links": [[77, 2, 0, 3, 1, "INT"]],
        }
        plan = seed_plan(workflow, self.registry)
        self.assertEqual({}, plan["pinnable"])
        self.assertEqual(["3.seed"], plan["uncontrollable"])
        self.assertIn("固定种子不会生效", plan["warning"])
        self.assertIn("2", plan["warning"], "告警要指出种子来自哪个节点")
        self.assertIn("PrimitiveInt", plan["warning"], "告警要说出上游节点类型")

    def test_a_producer_in_a_muted_branch_still_counts_as_controllable(self):
        """Branch selection uses ``mode``；不能因为上游被 mute 就误报不可控。"""
        workflow = {
            "nodes": [
                {"id": 268, "type": "Seed (rgthree)", "mode": 4, "inputs": [], "widgets_values": [597831106137247],
                 "outputs": [{"name": "SEED", "type": "INT", "links": [296]}]},
                {"id": 211, "type": "KSamplerAdvanced", "mode": 4,
                 "inputs": [{"name": "noise_seed", "type": "INT", "widget": {"name": "noise_seed"}, "link": 296}],
                 "widgets_values": [1, "randomize"]},
            ],
            "links": [[296, 268, 0, 211, 1, "INT"]],
        }
        plan = seed_plan(workflow, self.registry, ["211", "268"])
        self.assertEqual(["268.seed"], list(plan["pinnable"]))
        self.assertEqual([], plan["uncontrollable"])
        self.assertEqual("", plan["warning"])


class ImageSeedInspectorTests(unittest.TestCase):
    def test_reads_the_seed_recorded_inside_the_image(self):
        with tempfile.TemporaryDirectory() as temp:
            path = png_with_prompt(pathlib.Path(temp) / "out.png", seed_graph(4001))
            result = ImageSeedInspector.inspect(path)
            self.assertTrue(result["read"])
            self.assertEqual({"20.seed": 4001}, result["seeds"])

    def test_an_image_without_the_chunk_is_not_guessed(self):
        with tempfile.TemporaryDirectory() as temp:
            path = png_with_prompt(pathlib.Path(temp) / "plain.png", None)
            result = ImageSeedInspector.inspect(path)
            self.assertFalse(result["read"])
            self.assertEqual({}, result["seeds"])
            self.assertTrue(result["reason"])

    def test_a_broken_file_degrades_instead_of_raising(self):
        with tempfile.TemporaryDirectory() as temp:
            path = pathlib.Path(temp) / "broken.png"
            path.write_bytes(b"not an image")
            result = ImageSeedInspector.inspect(path)
            self.assertFalse(result["read"])
            self.assertFalse(result["read"] and result["seeds"])
            self.assertTrue(result["reason"])
            missing = ImageSeedInspector.inspect(pathlib.Path(temp) / "nope.png")
            self.assertFalse(missing["read"])

    def test_a_non_json_chunk_is_rejected_with_a_reason(self):
        from PIL import Image, PngImagePlugin

        with tempfile.TemporaryDirectory() as temp:
            path = pathlib.Path(temp) / "bad.png"
            info = PngImagePlugin.PngInfo()
            info.add_text("prompt", "{not json")
            Image.new("RGB", (8, 8), "white").save(path, pnginfo=info)
            result = ImageSeedInspector.inspect(path)
            self.assertFalse(result["read"])
            self.assertIn("JSON", result["reason"])

    def test_an_oversized_chunk_is_skipped_rather_than_parsed(self):
        original = ImageSeedInspector.MAX_CHUNK
        ImageSeedInspector.MAX_CHUNK = 16
        try:
            with tempfile.TemporaryDirectory() as temp:
                path = png_with_prompt(pathlib.Path(temp) / "big.png", seed_graph(1))
                result = ImageSeedInspector.inspect(path)
                self.assertFalse(result["read"])
                self.assertIn("过大", result["reason"])
        finally:
            ImageSeedInspector.MAX_CHUNK = original


class RealSeedEvidenceTests(unittest.TestCase):
    def test_the_image_outranks_the_execution_history(self):
        with tempfile.TemporaryDirectory() as temp:
            path = png_with_prompt(pathlib.Path(temp) / "out.png", seed_graph(7))
            entry = {"prompt": [1, "pid", seed_graph(99), {}, []]}
            evidence = real_seed_evidence(path, entry)
            self.assertEqual({"20.seed": 7}, evidence["seeds"])
            self.assertEqual("图片内嵌提示", evidence["source"])

    def test_history_is_used_when_the_image_carries_nothing(self):
        with tempfile.TemporaryDirectory() as temp:
            path = png_with_prompt(pathlib.Path(temp) / "plain.png", None)
            entry = {"prompt": [1, "pid", seed_graph(99), {}, []]}
            evidence = real_seed_evidence(path, entry)
            self.assertEqual({"20.seed": 99}, evidence["seeds"])
            self.assertEqual("执行历史", evidence["source"])

    def test_neither_source_reads_means_unverified(self):
        with tempfile.TemporaryDirectory() as temp:
            path = png_with_prompt(pathlib.Path(temp) / "plain.png", None)
            evidence = real_seed_evidence(path, {})
            self.assertFalse(evidence["available"])
            self.assertEqual("未核对", evidence["source"])
            self.assertTrue(evidence["reason"])


class BatchSeedEvidenceTests(unittest.TestCase):
    """The whole chain: submit -> image -> report -> review card."""

    class StampComfy:
        """Writes PNGs that carry the prompt ComfyUI would have recorded.

        ``replace_seed`` simulates a node that overrides the seed on its own, and
        ``omit_metadata`` simulates a file whose chunk is gone (re-exported,
        stripped, not a PNG at all).
        """

        def __init__(self, output_dir: pathlib.Path, *, replace_seed: int | None = None, omit_metadata: bool = False):
            self.output_dir = output_dir
            self.replace_seed = replace_seed
            self.omit_metadata = omit_metadata
            self.graphs: list[dict] = []
            self.calls = 0

        def submit(self, graph, client_id):
            self.calls += 1
            self.graphs.append(graph)
            name = f"stamp{self.calls}.png"
            if self.omit_metadata:
                png_with_prompt(self.output_dir / name, None)
            else:
                stamped = json.loads(json.dumps(graph))
                if self.replace_seed is not None:
                    stamped["20"]["inputs"]["seed"] = int(self.replace_seed)
                png_with_prompt(self.output_dir / name, stamped)
            return f"prompt-{self.calls}"

        def output(self, prompt_id):
            index = int(str(prompt_id).split("-")[-1])
            return {"filename": f"stamp{index}.png", "subfolder": "", "type": "output"}

        def poll(self, prompt_id):
            image = self.output(prompt_id)
            return {"state": "done", "images": [image], "problems": [], "entry": {}}

        def interrupt(self):
            pass

    def setUp(self):
        self.registry = NodeSchemaRegistry.from_path(FIXTURES / "object_info.json")
        set_active_registry(self.registry)

    def run_batch(self, comfy, root: pathlib.Path, *, seed: int | None, items: int = 1, graph: dict | None = None):
        workflow = write_workflow(root / "workflow.json", graph if graph is not None else seed_graph(1))
        runner = BatchRunner(root / "ComfyUI", comfy)
        payload = {"items": [
            {"title": f"测试{i}", "prompt": "服装提示词"} for i in range(1, items + 1)
        ]}
        from comfybatch_v2_core import PromptBundleParser

        bundle = PromptBundleParser.parse("one.json", json.dumps(payload).encode())
        config = BatchConfig(str(workflow), "red.safetensors", "anime", "Cel",
                             output_dir=str(root / "collection"), seed=seed)
        runner.start(bundle, config)
        deadline = time.time() + 5
        while runner.status()["status"] in {"starting", "running"} and time.time() < deadline:
            time.sleep(0.01)
        status = runner.status()
        self.assertEqual("completed", status["status"], status.get("aborted_reason") or status)
        report = json.loads(pathlib.Path(status["report"]).read_text(encoding="utf-8"))
        return status, report

    def test_the_reported_real_seed_is_the_one_printed_in_the_image(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            (root / "ComfyUI" / "output").mkdir(parents=True)
            status, report = self.run_batch(self.StampComfy(root / "ComfyUI" / "output"), root, seed=24680)
            generation = status["results"][0]["generation"]
            self.assertEqual({"20.seed": 24680}, generation["seeds"], "提交种子应为固定值")
            self.assertEqual({"20.seed": 24680}, generation["real_seeds"])
            self.assertEqual("图片内嵌提示", generation["seed_source"])
            self.assertTrue(generation["seed_check"]["effective"])
            self.assertFalse(report["seed_stats"]["mismatched"])
            self.assertEqual({"24680": 1}, report["seed_stats"]["real_values"])
            self.assertEqual({"24680": 1}, report["seed_stats"]["values"])

    def test_a_seed_that_did_not_survive_is_reported_against_the_image(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            (root / "ComfyUI" / "output").mkdir(parents=True)
            comfy = self.StampComfy(root / "ComfyUI" / "output", replace_seed=111)
            status, report = self.run_batch(comfy, root, seed=24680)
            generation = status["results"][0]["generation"]
            self.assertEqual({"20.seed": 24680}, generation["seeds"])
            self.assertEqual({"20.seed": 111}, generation["real_seeds"], "实际值必须来自成图，而不是提交图")
            self.assertFalse(generation["seed_check"]["effective"])
            self.assertEqual([1], [item["index"] for item in report["seed_stats"]["mismatched"]])
            self.assertEqual("图片内嵌提示", report["seed_stats"]["mismatched"][0]["source"])

    def test_an_unreadable_seed_is_listed_as_unverified_not_mismatched(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            (root / "ComfyUI" / "output").mkdir(parents=True)
            comfy = self.StampComfy(root / "ComfyUI" / "output", omit_metadata=True)
            status, report = self.run_batch(comfy, root, seed=24680)
            generation = status["results"][0]["generation"]
            self.assertIsNone(generation["seed_check"]["effective"], "读不到就不许判定失败")
            self.assertFalse(generation["seed_check"]["available"])
            self.assertEqual("未核对", generation["seed_source"])
            self.assertEqual([1], report["seed_stats"]["unverified_indexes"])
            self.assertEqual([], report["seed_stats"]["mismatched"])

    def test_random_mode_gives_every_image_its_own_real_seed(self):
        """随机模式下每张图的实际种子必须各不相同 —— 而且是读回来的证据。"""
        from fakes import ui_workflow

        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            (root / "ComfyUI" / "output").mkdir(parents=True)
            status, report = self.run_batch(
                self.StampComfy(root / "ComfyUI" / "output"), root, seed=None, items=3, graph=ui_workflow(),
            )
            real = [next(iter(row["generation"]["real_seeds"].values())) for row in status["results"]]
            self.assertEqual(3, len(real))
            self.assertEqual(3, len(set(real)), f"每张图应有各自的实际种子：{real}")
            for row in status["results"]:
                self.assertTrue(row["generation"]["seed_check"]["effective"])
            self.assertEqual({}, report["seed_stats"]["real_duplicates"])

    def test_an_api_graph_keeps_its_own_seed_and_that_is_now_visible(self):
        """API 图按原样提交，随机模式下种子仍是工作流里那一个。

        这不是本任务要改的语义（改它会动到「API 图原样提交」这条既有约定，而且
        本项目所有真实工作流都是 UI 格式），但新的读回把它从「用户看不见」变成
        「报告里写明」：三张图用了同一个种子，会出现在 real_duplicates 里。
        """
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            (root / "ComfyUI" / "output").mkdir(parents=True)
            status, report = self.run_batch(self.StampComfy(root / "ComfyUI" / "output"), root, seed=None, items=3)
            real = [next(iter(row["generation"]["real_seeds"].values())) for row in status["results"]]
            self.assertEqual(1, len(set(real)))
            self.assertEqual({str(real[0]): 3}, report["seed_stats"]["real_duplicates"])
            for row in status["results"]:
                self.assertTrue(row["generation"]["seed_check"]["effective"])


class CapabilitySeedTests(unittest.TestCase):
    """Preflight must say it before the run, not after."""

    def setUp(self):
        self.registry = NodeSchemaRegistry.from_path(FIXTURES / "object_info.json")
        set_active_registry(self.registry)

    def test_the_real_workflow_reports_what_a_fixed_seed_reaches(self):
        name = "workflow_Krea2-高清生图优化流_整合.json"
        config = BatchConfig(
            workflow_path=str(FIXTURES / name),
            model="redcraftHybridH3A2A_30Krea2.safetensors",
            aspect_ratio="16:9 (Widescreen)",
            megapixels=0.9,
            workflow_variant="save:285",
        )
        adapter = Krea2WorkflowAdapter.from_path(config.workflow_path, self.registry)
        report = adapter.capabilities(config)
        self.assertTrue(report["ready"])
        pins = report["seed"]["pinnable"]
        # 该分支的两个 Seed (rgthree) 节点，正是编译图里真正会被固定种子的两处。
        self.assertEqual({"268.seed", "276.seed"}, set(pins))
        self.assertEqual([], report["seed"]["uncontrollable"])
        self.assertEqual("", report["seed"]["warning"])

    def test_an_unpinnable_workflow_says_so_in_the_preflight_warnings(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            workflow = root / "workflow.json"
            workflow.write_text(json.dumps({
                "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "m.safetensors", "weight_dtype": "default"}},
                "2": {"class_type": "PrimitiveStringMultiline", "inputs": {"value": "old"}},
                "3": {"class_type": "easy stylesSelector", "inputs": {"styles": "old", "select_styles": "old", "positive": ["2", 0]}},
                "4": {"class_type": "ResolutionSelector", "inputs": {"aspect_ratio": "1:1", "megapixels": 1, "multiple": 32}},
                "20": {"class_type": "PrimitiveInt", "inputs": {"value": 12345}},
                "5": {"class_type": "KSampler", "inputs": {"model": ["1", 0], "seed": ["20", 0]}},
                "6": {"class_type": "SaveImage", "inputs": {"filename_prefix": "old", "images": ["5", 0]}},
            }, ensure_ascii=False), encoding="utf-8")
            config = BatchConfig(str(workflow), "m.safetensors", "anime", "Cel")
            adapter = Krea2WorkflowAdapter.from_path(config.workflow_path, self.registry)
            report = adapter.capabilities(config)
            self.assertTrue(report["ready"], report["errors"])
            self.assertEqual([], list(report["seed"]["pinnable"]))
            self.assertEqual(["5.seed"], report["seed"]["uncontrollable"])
            self.assertTrue(any("固定种子不会生效" in item for item in report["warnings"]))


class SeedDisplayAssetTests(unittest.TestCase):
    """The page must be able to tell the three states apart."""

    def test_the_review_card_shows_the_real_seed_and_its_source(self):
        from fakes import js_source

        code = js_source()
        self.assertIn("实际种子", code)
        self.assertIn("real_seeds", code)
        self.assertIn("未核对", code)
        self.assertIn("seedCheckHtml", code)
        self.assertIn("seedBadgeHtml", code)
        self.assertIn("种子未生效", code)

    def test_the_rail_has_a_seed_row_wired_to_the_plan(self):
        from fakes import css_source, html_source, js_source

        self.assertIn('id="railSeed"', html_source())
        self.assertIn("railSeed", js_source())
        self.assertIn("seedFactText", js_source())
        self.assertIn("固定种子将写入", js_source())

    def test_the_duplicate_finder_prefers_the_real_seed(self):
        from fakes import js_source

        code = js_source()
        self.assertIn("seedKeyOf", code)
        # 同一张图的实际种子重复＝必然同图；用提交值判会漏掉被上游改写的种子。
        self.assertIn("seedKeyOf(row)", code)

    def test_seed_note_styling_uses_the_page_tokens(self):
        from fakes import css_source

        css = css_source()
        self.assertIn(".seed-note", css)
        self.assertIn(".badge.seed-unchecked", css)
        self.assertNotIn("#3a2510", css.split(".badge.seed-unchecked")[1][:200])


if __name__ == "__main__":
    unittest.main()
