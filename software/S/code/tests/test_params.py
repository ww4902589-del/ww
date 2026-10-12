"""The parameter workbench: one registry drives UI, validation, and the graph.

Design point under test: a parameter is declared once, and that single
declaration is what the page renders, what validation checks against the live
schema, and what writes the value onto the submitted graph. If those could drift
apart, the page would show values that never reach ComfyUI.
"""

from __future__ import annotations

import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from fakes import SOURCE_ROOT  # noqa: E402

from comfybatch_nodeschema import NodeSchemaRegistry, set_active_registry  # noqa: E402
from comfybatch_params import (  # noqa: E402
    GROUP_ADVANCED,
    GROUP_COMMON,
    PARAMS,
    apply_params,
    registry_payload,
    resolve,
    sampler_stages,
)
from comfybatch_v2_core import BatchConfig, Krea2WorkflowAdapter  # noqa: E402

WORKFLOW = "workflow_Krea2-极清生图流_SeedVR2-int8图像放大.json"


def real_registry() -> NodeSchemaRegistry:
    return NodeSchemaRegistry.from_path(SOURCE_ROOT.parent / "tests" / "fixtures" / "object_info.json")


def real_workflow() -> dict:
    return json.loads((SOURCE_ROOT.parent / "tests" / "fixtures" / WORKFLOW).read_text(encoding="utf-8"))


class RegistryTests(unittest.TestCase):
    def test_common_and_advanced_are_both_populated(self):
        groups = {group["id"]: group for group in registry_payload(real_registry())["groups"]}
        self.assertIn(GROUP_COMMON, groups)
        self.assertIn(GROUP_ADVANCED, groups)
        self.assertGreaterEqual(len(groups[GROUP_COMMON]["params"]), 8)
        self.assertGreaterEqual(len(groups[GROUP_ADVANCED]["params"]), 10)

    def test_every_spec_declares_what_it_writes_to(self):
        for key, spec in PARAMS.items():
            with self.subTest(param=key):
                self.assertTrue(spec.targets, f"{key} 没有声明目标节点")
                for target in spec.targets:
                    self.assertTrue(target.class_type and target.input_name)

    def test_enum_specs_name_a_candidate_source(self):
        for key, spec in PARAMS.items():
            if spec.kind == "enum":
                with self.subTest(param=key):
                    self.assertIsNotNone(spec.candidate_from, f"{key} 是枚举但没说候选来自哪里")

    def test_enum_candidates_come_from_the_live_schema(self):
        payload = registry_payload(real_registry())
        specs = {p["key"]: p for group in payload["groups"] for p in group["params"]}
        # Non-empty for sampler_name, which every ComfyUI install has.
        self.assertTrue(specs["sampler_name"].get("options"))
        self.assertIn("euler", specs["sampler_name"]["options"])

    def test_bounds_are_declared_for_numeric_params(self):
        for key, spec in PARAMS.items():
            if spec.kind in {"int", "float"}:
                with self.subTest(param=key):
                    self.assertIsNotNone(spec.minimum, f"{key} 缺少下限")
                    self.assertIsNotNone(spec.maximum, f"{key} 缺少上限")


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.registry = real_registry()

    def test_valid_values_resolve(self):
        values, problems = resolve({"steps": 30, "cfg": "7.5", "denoise": 0.4}, self.registry)
        self.assertEqual({"steps": 30, "cfg": 7.5, "denoise": 0.4}, values)
        self.assertEqual([], problems)

    def test_out_of_range_is_rejected_with_bounds(self):
        values, problems = resolve({"steps": 99999}, self.registry)
        self.assertEqual({}, values)
        self.assertEqual(1, len(problems))
        self.assertTrue(problems[0].workflow_level)
        self.assertIn("200", problems[0].detail)

    def test_unknown_parameter_is_reported_not_ignored(self):
        values, problems = resolve({"definitely_not_a_param": 1}, self.registry)
        self.assertEqual({}, values)
        self.assertEqual("warning", problems[0].severity)
        self.assertIn("unknown", problems[0].title.replace("未知", "unknown"))

    def test_non_numeric_text_is_rejected(self):
        values, problems = resolve({"cfg": "abc"}, self.registry)
        self.assertEqual({}, values)
        self.assertTrue(problems)

    def test_enum_value_must_be_a_real_candidate(self):
        values, problems = resolve({"sampler_name": "definitely_not_a_sampler"}, self.registry)
        self.assertEqual({}, values)
        self.assertTrue(problems[0].candidates, "拒绝枚举值时必须给出合法候选")
        self.assertIn("euler", problems[0].candidates)
        self.assertTrue(any("euler" in fix for fix in problems[0].fixes))

    def test_blank_values_mean_use_the_workflow_value(self):
        values, problems = resolve({"steps": "", "cfg": None}, self.registry)
        self.assertEqual({}, values)
        self.assertEqual([], problems)

    def test_booleans_accept_chinese_and_english(self):
        self.assertEqual({"force_uniform_tiles": True}, resolve({"force_uniform_tiles": "是"}, self.registry)[0])
        self.assertEqual({"force_uniform_tiles": False}, resolve({"force_uniform_tiles": "false"}, self.registry)[0])


class SamplerStageTests(unittest.TestCase):
    """一采/二采 must come from the latent chain, not from node ids."""

    def test_two_pass_chain_is_ordered_by_latent_dependency(self):
        graph = {
            "21": {"class_type": "KSamplerAdvanced", "inputs": {"latent_image": ["16", 0]}},
            "16": {"class_type": "EmptyLatentImage", "inputs": {"width": 64, "height": 64}},
            "22": {"class_type": "LatentUpscaleBy", "inputs": {"samples": ["21", 0]}},
            "13": {"class_type": "KSampler", "inputs": {"latent_image": ["22", 0]}},
        }
        stages = sampler_stages(graph)
        self.assertEqual(1, stages["21"], "从 EmptyLatent 直接取 latent 的是第一采")
        self.assertEqual(2, stages["13"], "经过放大后再采样的是第二采")

    def test_single_sampler_is_stage_one(self):
        graph = {"5": {"class_type": "KSampler", "inputs": {}}}
        self.assertEqual({"5": 1}, sampler_stages(graph))

    def test_no_samplers_yields_nothing(self):
        self.assertEqual({}, sampler_stages({"1": {"class_type": "SaveImage", "inputs": {}}}))


class ApplyToGraphTests(unittest.TestCase):
    """Values must land on the right nodes, and never on a linked input."""

    @classmethod
    def setUpClass(cls):
        cls.registry = real_registry()
        set_active_registry(cls.registry)
        cls.workflow_path = str(SOURCE_ROOT.parent / "tests" / "fixtures" / WORKFLOW)

    def build(self, **params):
        cfg = BatchConfig(self.workflow_path, "redcraftHybridH3A2A_30Krea2.safetensors", "", "",
                          workflow_variant="save:1", aspect_ratio="16:9 (Widescreen)", megapixels=0.6, params=params)
        adapter = Krea2WorkflowAdapter.from_path(self.workflow_path, self.registry)
        graph = adapter.build("提示词", cfg, "ComfyBatch-V2/params")
        return cfg, adapter, graph

    def test_uniform_steps_reach_every_sampler(self):
        _cfg, _adapter, graph = self.build(steps=25)
        self.assertEqual(25, graph["21"]["inputs"]["steps"])
        self.assertEqual(25, graph["13"]["inputs"]["steps"])

    def test_stage_specific_steps_reach_only_that_sampler(self):
        _cfg, _adapter, graph = self.build(steps_stage1=12, steps_stage2=30)
        self.assertEqual(12, graph["21"]["inputs"]["steps"])
        self.assertEqual(30, graph["13"]["inputs"]["steps"])

    def test_sampler_and_scheduler_reach_both_passes(self):
        _cfg, _adapter, graph = self.build(sampler_name="dpmpp_2m", scheduler="karras")
        for node_id in ("21", "13"):
            self.assertEqual("dpmpp_2m", graph[node_id]["inputs"]["sampler_name"])
            self.assertEqual("karras", graph[node_id]["inputs"]["scheduler"])

    def test_upscale_parameters_reach_the_upscale_nodes(self):
        _cfg, _adapter, graph = self.build(upscale_by=1.5, tile_size=2048, mode_type="Linear")
        self.assertEqual(1.5, graph["3"]["inputs"]["upscale_by"])
        self.assertEqual(2048, graph["3"]["inputs"]["tile_width"])
        self.assertEqual(2048, graph["3"]["inputs"]["tile_height"])
        self.assertEqual("Linear", graph["3"]["inputs"]["mode_type"])

    def test_batch_size_reaches_the_latent_node(self):
        _cfg, _adapter, graph = self.build(batch_size=2)
        self.assertEqual(2, graph["16"]["inputs"]["batch_size"])

    def test_a_linked_input_is_never_overwritten_and_says_so(self):
        """The workflow drives width/height from ResolutionSelector."""
        cfg, adapter, graph = self.build(base_width=1280, base_height=720)
        self.assertIsInstance(graph["16"]["inputs"]["width"], list, "连线不能被参数覆盖")
        audit = adapter.audit(cfg, branch="save:1")
        warnings = [p for p in audit["param_problems"] if p["input_name"] == "base_width"]
        self.assertEqual(1, len(warnings), "没生效的参数必须说明原因")
        self.assertEqual("warning", warnings[0]["severity"])
        self.assertIn("ResolutionSelector", warnings[0]["detail"])

    def test_a_parameter_with_no_matching_node_is_reported(self):
        cfg, adapter, graph = self.build(upscale_by=2.0)
        # The 风格扩展流 branch has no upscale node, so simulate by removing them.
        del graph["3"]
        _values, problems = resolve({"upscale_by": 2.0}, self.registry)
        self.assertEqual([], problems)

    def test_values_are_tagged_as_workbench_choices(self):
        cfg, adapter, graph = self.build(steps_stage2=30)
        audit = adapter.audit(cfg, branch="save:1")
        tagged = [o for o in audit["overrides"] if o["source"] == "param"]
        self.assertTrue(tagged, "参数应用必须出现在生效参数表里")
        entry = next(o for o in tagged if o["input"] == "steps")
        self.assertEqual(30, entry["value"])
        self.assertEqual("参数工作台", entry["source_cn"])

    def test_out_of_range_parameter_is_blocking_through_the_audit(self):
        cfg, adapter, graph = self.build(steps=99999)
        audit = adapter.audit(cfg, branch="save:1")
        self.assertFalse(audit["ready"])
        self.assertEqual(1, len(audit["param_blocking"]))
        # And it did not silently write the bad value.
        self.assertNotEqual(99999, graph["21"]["inputs"]["steps"])

    def test_unknown_parameter_does_not_corrupt_the_graph(self):
        cfg, adapter, graph = self.build(**{"not_a_param": 5})
        audit = adapter.audit(cfg, branch="save:1")
        self.assertEqual(1, len(audit["param_problems"]))
        self.assertFalse(audit["param_problems"][0]["workflow_level"], "未知参数只警告，不阻断")
        for node in graph.values():
            self.assertNotIn("not_a_param", node.get("inputs") or {})

    def test_upscale_model_parameter_clears_the_missing_resource(self):
        cfg, adapter, graph = self.build(upscale_model="RealESRGAN_x4plus_anime_6B.pth")
        self.assertEqual("RealESRGAN_x4plus_anime_6B.pth", graph["11"]["inputs"]["model_name"])
        self.assertEqual([], adapter.audit(cfg, branch="save:1")["blocking"])

    def test_task_level_params_override_batch_level(self):
        """Task scope wins, which is what "仅选中的任务" has to mean."""
        adapter_batch = Krea2WorkflowAdapter.from_path(self.workflow_path, self.registry)
        merged = {"steps": 20, "cfg": 5.0}
        merged.update({"cfg": 9.0})  # a task-level value for cfg
        cfg = BatchConfig(self.workflow_path, "redcraftHybridH3A2A_30Krea2.safetensors", "", "",
                          workflow_variant="save:1", aspect_ratio="16:9 (Widescreen)", megapixels=0.6, params=merged)
        graph = adapter_batch.build("提示词", cfg, "p")
        self.assertEqual(20, graph["21"]["inputs"]["steps"])
        self.assertEqual(9.0, graph["21"]["inputs"]["cfg"])


if __name__ == "__main__":
    unittest.main()
