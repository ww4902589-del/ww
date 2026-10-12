"""Golden snapshot of the compiled graph for the real 极清 workflow.

This is the regression gate for the compiler rewrite. The values below were
verified by hand against the workflow file; they are what the old positional
cursor got wrong (it put the string ``"randomize"`` into ``sampler_name`` and
shifted every later parameter). Any future change that reintroduces positional
mapping will break these assertions immediately.
"""

from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from fakes import SOURCE_ROOT  # noqa: E402

from comfybatch_nodeschema import NodeSchemaRegistry, graph_seeds, set_active_registry  # noqa: E402
from comfybatch_v2_core import BatchConfig, Krea2WorkflowAdapter  # noqa: E402

WORKFLOW = "workflow_Krea2-极清生图流_SeedVR2-int8图像放大.json"

#: Node 3 is the UltimateSDUpscale node whose misalignment caused the incident.
GOLDEN_ULTIMATE_SD_UPSCALE = {
    "upscale_by": 2.0,
    "steps": 4,
    "cfg": 1.0,
    "sampler_name": "euler",
    "scheduler": "simple",
    "denoise": 0.15,
    "mode_type": "Linear",
    "tile_width": 1024,
    "tile_height": 1024,
    "mask_blur": 16,
    "tile_padding": 64,
    "seam_fix_mode": "None",
    "seam_fix_denoise": 1.0,
    "seam_fix_width": 64,
    "seam_fix_mask_blur": 8,
    "seam_fix_padding": 16,
    "force_uniform_tiles": True,
    "tiled_decode": False,
}

GOLDEN_ADVANCED_SAMPLER = {
    "add_noise": "enable",
    "steps": 8,
    "cfg": 1.0,
    "sampler_name": "euler",
    "scheduler": "simple",
    "start_at_step": 0,
    "end_at_step": 8,
    "return_with_leftover_noise": "disable",
}

#: Values the old cursor produced instead, for the record. Keeping them here
#: documents exactly what the defect looked like.
BROKEN_VALUES_FROM_POSITIONAL_CURSOR = {"sampler_name": "randomize", "scheduler": 4}


def config(**overrides) -> BatchConfig:
    values = {
        "workflow_path": str(SOURCE_ROOT.parent / "tests" / "fixtures" / WORKFLOW),
        "model": "redcraftHybridH3A2A_30Krea2.safetensors",
        "workflow_variant": "save:1",
        "aspect_ratio": "16:9 (Widescreen)",
        "megapixels": 0.9,
    }
    values.update(overrides)
    return BatchConfig(**values)


class CompiledGraphGoldenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = NodeSchemaRegistry.from_path(SOURCE_ROOT.parent / "tests" / "fixtures" / "object_info.json")
        set_active_registry(cls.registry)

    def build(self, **overrides):
        cfg = config(**overrides)
        adapter = Krea2WorkflowAdapter.from_path(cfg.workflow_path, self.registry)
        graph = adapter.build("服装展示提示词", cfg, "ComfyBatch-V2/golden/001")
        return cfg, adapter, graph

    def test_ultimate_sd_upscale_matches_the_golden_parameters(self):
        _cfg, _adapter, graph = self.build()
        node = graph["3"]
        self.assertEqual("UltimateSDUpscale", node["class_type"])
        for name, expected in GOLDEN_ULTIMATE_SD_UPSCALE.items():
            self.assertEqual(expected, node["inputs"].get(name), f"节点 3 的 {name} 与黄金值不符")

    def test_the_broken_positional_values_are_not_present(self):
        """The exact symptom of the defect must be gone."""
        _cfg, _adapter, graph = self.build()
        inputs = graph["3"]["inputs"]
        for name, broken in BROKEN_VALUES_FROM_POSITIONAL_CURSOR.items():
            self.assertNotEqual(broken, inputs.get(name), f"节点 3 的 {name} 又出现了位置漂移产生的值 {broken!r}")
        # A control_after_generate token must never be submitted as a parameter.
        for _node_id, node in graph.items():
            for value in node["inputs"].values():
                self.assertNotIn(value, ("randomize", "increment", "decrement"),
                                 "提交图里出现了 control_after_generate 的取值")

    def test_advanced_sampler_matches_the_golden_parameters(self):
        _cfg, _adapter, graph = self.build()
        node = graph["21"]
        self.assertEqual("KSamplerAdvanced", node["class_type"])
        for name, expected in GOLDEN_ADVANCED_SAMPLER.items():
            self.assertEqual(expected, node["inputs"].get(name), f"节点 21 的 {name} 与黄金值不符")

    def test_linked_inputs_are_preserved_as_links(self):
        _cfg, _adapter, graph = self.build()
        inputs = graph["3"]["inputs"]
        # These four were promoted to links in the workflow; the cursor used to
        # skip them for input binding while widgets_values still counted them.
        for name in ("upscale_model", "image", "model", "positive", "negative", "vae"):
            self.assertIsInstance(inputs[name], list, f"{name} 应该是连线")

    def test_no_dangling_links_in_the_compiled_graph(self):
        _cfg, _adapter, graph = self.build()
        for node_id, node in graph.items():
            for name, value in node["inputs"].items():
                if isinstance(value, list) and len(value) == 2:
                    self.assertIn(str(value[0]), graph, f"节点 {node_id}.{name} 悬空指向 {value[0]}")

    def test_prompt_and_negative_prompt_are_written(self):
        cfg = config(negative_prompt="三视图, 多人")
        adapter = Krea2WorkflowAdapter.from_path(cfg.workflow_path, self.registry)
        graph = adapter.build("正面提示词内容", cfg, "p", task_negative="真人")
        self.assertIn("正面提示词内容", json_dumps(graph))
        # The negative prompt must reach a CLIPTextEncode, including for
        # KSamplerAdvanced, which the old code silently skipped.
        self.assertIn("真人", json_dumps(graph))

    def test_upscale_model_can_be_swapped_and_the_audit_then_passes(self):
        cfg, adapter, _graph = self.build()
        # Without a replacement the missing OmniSR model is a blocking finding.
        blocking = adapter.audit(cfg, branch="save:1")["blocking"]
        self.assertEqual(
            {"fixture-resource-001.safetensors"},
            {problem["received_value"] for problem in blocking if problem["input_name"] == "model_name"},
        )

        swapped, adapter_swapped, graph = self.build(params={"upscale_model": "RealESRGAN_x4plus_anime_6B.pth"})
        self.assertEqual("RealESRGAN_x4plus_anime_6B.pth", graph["11"]["inputs"]["model_name"])
        self.assertEqual([], adapter_swapped.audit(swapped, branch="save:1")["blocking"])

    def test_audit_traces_provenance_for_every_input(self):
        cfg, adapter, graph = self.build()
        audit = adapter.audit(cfg, branch="save:1")
        self.assertEqual(sum(len(node["inputs"]) for node in graph.values()), audit["input_count"])
        counts = audit["source_counts"]
        # Every input in the graph is explained by exactly one source.
        self.assertEqual(audit["input_count"], sum(counts.values()))
        self.assertGreater(counts.get("linked", 0), 0)
        self.assertGreater(counts.get("override", 0), 0)
        for node_id, entry in audit["nodes"].items():
            for name, trace in entry["inputs"].items():
                self.assertIn(trace["source"], {"linked", "widget", "override", "injected", "default"})

    def test_variant_selection_reaches_the_right_branch(self):
        """The three branches the plan requires are all discoverable."""
        adapter = Krea2WorkflowAdapter.from_path(config().workflow_path, self.registry)
        names = {variant["name"] for variant in adapter.workflow_variants()}
        self.assertIn("②Krea2-高清重绘2", names)
        self.assertTrue(any("SeedVR2" in name for name in names))
        self.assertTrue(any("二采" in name for name in names))


class SeedThreadingTests(unittest.TestCase):
    """``config.seed`` must reach every sampler/noise seed in the compiled graph.

    The V2.19 seed-mode control only works if the pinned seed survives the whole
    compilation pipeline. ``apply_seed`` offsets multi-node seeds by one so two
    samplers in one workflow do not correlate their passes.
    """

    @classmethod
    def setUpClass(cls):
        cls.registry = NodeSchemaRegistry.from_path(SOURCE_ROOT.parent / "tests" / "fixtures" / "object_info.json")
        set_active_registry(cls.registry)

    def build(self, **overrides):
        cfg = config(**overrides)
        adapter = Krea2WorkflowAdapter.from_path(cfg.workflow_path, self.registry)
        graph = adapter.build("种子贯通测试", cfg, "ComfyBatch-V2/seed/001")
        return cfg, graph

    def test_a_fixed_seed_pins_every_sampler_seed_with_node_offsets(self):
        _cfg, graph = self.build(seed=777)
        seeds = graph_seeds(graph)
        self.assertTrue(seeds, "固定种子时编译图里必须能取到具体的种子")
        # apply_seed keeps the graph_seeds order and offsets each node by one.
        self.assertEqual([777 + offset for offset in range(len(seeds))], list(seeds.values()))

    def test_without_a_seed_the_seeds_are_concrete_random_integers(self):
        """No seed pin must never leak a placeholder value into the submit graph."""
        _cfg, first = self.build()
        _cfg, second = self.build()
        first_seeds = graph_seeds(first)
        second_seeds = graph_seeds(second)
        for seeds in (first_seeds, second_seeds):
            self.assertTrue(seeds, "不指定种子时也必须写入具体种子，不能留给前端")
            for value in seeds.values():
                self.assertIsInstance(value, int)
                self.assertFalse(isinstance(value, bool))
                self.assertGreaterEqual(value, 0)
        self.assertNotEqual(first_seeds, second_seeds, "两次独立编译应当得到不同的随机种子")


def json_dumps(value) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()
