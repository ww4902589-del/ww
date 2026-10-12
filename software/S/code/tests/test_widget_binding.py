"""Regression tests for the schema-driven widget binder.

The defect these pin down: ``widgets_values`` keeps an entry for every widget,
including widgets whose value was promoted to a *link*, plus hidden frontend
widgets that have no API input. The old positional cursor only advanced for
unlinked widgets, so the offset drifted within a single node and values landed
on the wrong parameters -- which is what produced ``HTTP Error 400`` for all 45
items of the new 极清 workflow.

The sharpest test here is
``test_real_ultimate_sd_upscale_binds_every_parameter_by_name``: it compiles the
actual node from the actual workflow against the actual recorded schema.
"""

from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from fakes import SOURCE_ROOT, load_fixture_json  # noqa: E402

from comfybatch_nodeschema import (  # noqa: E402
    SOURCE_LINKED,
    SOURCE_OVERRIDE,
    SOURCE_WIDGET,
    NodeSchemaRegistry,
    WidgetBinder,
    convert_node,
)

SCHEMA_FIXTURE = "object_info.json"
WORKFLOW_FIXTURE = "workflow_Krea2-极清生图流_SeedVR2-int8图像放大.json"

_REGISTRY: NodeSchemaRegistry | None = None


def real_registry() -> NodeSchemaRegistry:
    """The recorded live schema (2848 node types), loaded once per process."""
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = NodeSchemaRegistry.from_path(SOURCE_ROOT.parent / "tests" / "fixtures" / SCHEMA_FIXTURE)
    return _REGISTRY


def real_workflow() -> dict:
    return load_fixture_json(WORKFLOW_FIXTURE)


def real_node(node_type: str) -> dict:
    for node in real_workflow().get("nodes", []):
        if node.get("type") == node_type:
            return node
    raise AssertionError(f"工作流中找不到 {node_type} 节点")


class WidgetBinderUnitTests(unittest.TestCase):
    """Binder behaviour on hand-built nodes, independent of ComfyUI versions."""

    def test_binds_unlinked_widgets_by_name_not_position(self):
        node = {
            "id": 1,
            "type": "KSampler",
            "inputs": [
                {"name": "seed", "widget": {"name": "seed"}, "link": None},
                {"name": "steps", "widget": {"name": "steps"}, "link": None},
                {"name": "cfg", "widget": {"name": "cfg"}, "link": None},
            ],
            "widgets_values": [42, "fixed", 20, 7.5],
        }
        result = WidgetBinder.bind(node)
        self.assertEqual([], result.problems)
        # The hidden control_after_generate slot must be consumed, so steps is 20
        # rather than the string "fixed".
        self.assertEqual(42, result.widgets["seed"])
        self.assertEqual(20, result.widgets["steps"])
        self.assertEqual(7.5, result.widgets["cfg"])

    def test_linked_widget_still_occupies_its_slot(self):
        """A widget promoted to a link must not shift the following values."""
        node = {
            "id": 7,
            "type": "UltimateSDUpscale",
            "inputs": [
                {"name": "upscale_by", "widget": {"name": "upscale_by"}, "link": 8},
                {"name": "seed", "widget": {"name": "seed"}, "link": 9},
                {"name": "steps", "widget": {"name": "steps"}, "link": None},
                {"name": "sampler_name", "widget": {"name": "sampler_name"}, "link": None},
            ],
            "widgets_values": [2.0, 999, "fixed", 4, "euler"],
        }
        result = WidgetBinder.bind(node)
        self.assertEqual([], result.problems)
        self.assertEqual(2.0, result.widgets["upscale_by"])
        self.assertEqual(999, result.widgets["seed"])
        self.assertEqual(4, result.widgets["steps"])
        self.assertEqual("euler", result.widgets["sampler_name"])

    def test_reports_misalignment_instead_of_guessing(self):
        node = {
            "id": 3,
            "type": "SomeCustomNode",
            "inputs": [
                {"name": "alpha", "widget": {"name": "alpha"}, "link": None},
                {"name": "beta", "widget": {"name": "beta"}, "link": None},
            ],
            "widgets_values": [1, 2, 3, 4, 5],
        }
        result = WidgetBinder.bind(node)
        self.assertEqual(1, len(result.problems))
        problem = result.problems[0]
        self.assertEqual("parameter_misaligned", problem.category)
        self.assertIn("alpha", problem.detail)
        self.assertIn("5", problem.detail)
        # Values that are provably aligned are still bound, in the right names.
        self.assertEqual({"alpha": 1, "beta": 2}, result.widgets)

    def test_reports_when_no_widget_order_can_be_determined(self):
        node = {"id": 4, "type": "MysteryNode", "inputs": [], "widgets_values": [1, 2]}
        result = WidgetBinder.bind(node, NodeSchemaRegistry.empty())
        self.assertEqual(1, len(result.problems))
        self.assertEqual("parameter_misaligned", result.problems[0].category)

    def test_builtin_order_covers_core_nodes_when_the_node_declares_nothing(self):
        """A graph with no ``widget`` declaration and no ComfyUI still binds.

        This is the silent-degradation case: without an order the text simply
        never reaches ``inputs.text``, and a negative-prompt encoder quietly
        runs empty. The built-in table has to mirror the *declared* order, with
        no ``control_after_generate`` entry -- that slot is re-inserted
        positionally, so listing it here would shift every value by one.
        """
        registry = NodeSchemaRegistry.empty()

        encoder = {"id": 4, "type": "CLIPTextEncode", "inputs": [], "widgets_values": ["作者原有的负面词"]}
        result = WidgetBinder.bind(encoder, registry)
        self.assertEqual([], result.problems)
        self.assertEqual("作者原有的负面词", result.widgets["text"])

        sampler = {
            "id": 6, "type": "KSampler", "inputs": [],
            "widgets_values": [959948902156062, "fixed", 1, 1, "euler", "simple", 1],
        }
        result = WidgetBinder.bind(sampler, registry)
        self.assertEqual([], result.problems)
        self.assertEqual(1, result.widgets["steps"])
        self.assertEqual(1.0, result.widgets["cfg"])
        self.assertEqual("euler", result.widgets["sampler_name"])
        self.assertEqual("simple", result.widgets["scheduler"])
        self.assertEqual(1.0, result.widgets["denoise"])

    def test_the_schema_still_wins_over_the_builtin_table(self):
        """The fallback must never override a live ComfyUI definition."""
        registry = NodeSchemaRegistry.from_object_info({
            "CLIPTextEncode": {
                "input": {"required": {"clip": ["CLIP"], "text": ["STRING", {"multiline": True}]}},
                "output": [], "name": "CLIPTextEncode",
            },
        })
        self.assertEqual(["text"], registry.widget_order("CLIPTextEncode"))
        # An unknown type reports nothing rather than borrowing another node's order.
        self.assertEqual([], registry.widget_order("MysteryNode"))

    def test_trailing_frontend_state_is_a_warning_not_a_blocker(self):
        """Real case: rgthree's Seed node declares one widget but stores four values.

        Treating this as blocking would make the software refuse to run a
        perfectly good workflow, so trailing extras must stay non-blocking while
        still being reported.
        """
        registry = real_registry()
        node = real_node("Seed (rgthree)")
        result = WidgetBinder.bind(node, registry)
        self.assertEqual(1, len(result.problems))
        problem = result.problems[0]
        self.assertEqual("warning", problem.severity)
        self.assertFalse(problem.workflow_level)
        # The value that does line up is still bound to the right name.
        self.assertEqual(-1, result.widgets["seed"])

    def test_display_only_node_with_no_widget_inputs_reports_nothing(self):
        """Real case: "Image Comparer (rgthree)" stores temp preview URLs.

        It has no widget inputs at all, so there is nothing to map and nothing to
        warn about.
        """
        registry = real_registry()
        node = next((n for n in real_workflow()["nodes"] if n.get("type") == "Image Comparer (rgthree)"), None)
        self.assertIsNotNone(node, "固件缺少 Image Comparer (rgthree)")
        self.assertEqual([], registry.widget_order("Image Comparer (rgthree)"))
        result = WidgetBinder.bind(node, registry)
        self.assertEqual([], result.problems)
        self.assertEqual({}, result.widgets)
        self.assertEqual("no-widgets", result.strategy)

    def test_shortfall_is_a_warning_and_the_audit_is_the_gate(self):
        """The binding layer diagnoses; the graph audit decides.

        A binding shortfall cannot by itself say whether a converter or ComfyUI
        will supply the missing value, so it must not block. The audit sees the
        final graph, so *it* is what reports a genuinely missing required input.
        """
        from comfybatch_nodeschema import CompiledGraphAudit

        registry = NodeSchemaRegistry.from_object_info({
            "SomeNode": {"input": {"required": {
                "alpha": ["INT", {"default": 1, "min": 0, "max": 10}],
                "beta": ["INT", {"default": 2, "min": 0, "max": 10}],
                "gamma": ["INT", {"default": 3, "min": 0, "max": 10}],
            }}},
        })
        node = {
            "id": 9,
            "type": "SomeNode",
            "inputs": [
                {"name": "alpha", "widget": {"name": "alpha"}, "link": None},
                {"name": "beta", "widget": {"name": "beta"}, "link": None},
                {"name": "gamma", "widget": {"name": "gamma"}, "link": None},
            ],
            "widgets_values": [1],
        }
        result = WidgetBinder.bind(node, registry)
        self.assertEqual(1, len(result.problems))
        self.assertEqual("warning", result.problems[0].severity)
        self.assertEqual({"alpha": 1}, result.widgets)

        # The audit on the resulting graph reports the two unset required inputs
        # as blocking, which is what actually stops a bad run.
        audit = CompiledGraphAudit.run(
            {"9": {"class_type": "SomeNode", "inputs": {"alpha": 1}}}, registry=registry
        )
        self.assertEqual(2, len(audit["blocking"]))
        missing = {problem["input_name"] for problem in audit["blocking"]}
        self.assertEqual({"beta", "gamma"}, missing)
        self.assertTrue(all(problem["workflow_level"] for problem in audit["blocking"]))

    def test_audit_accepts_a_graph_whose_required_inputs_are_all_present(self):
        from comfybatch_nodeschema import CompiledGraphAudit

        registry = NodeSchemaRegistry.from_object_info({
            "SomeNode": {"input": {"required": {"alpha": ["INT", {"default": 1, "min": 0, "max": 10}]}}},
        })
        audit = CompiledGraphAudit.run(
            {"9": {"class_type": "SomeNode", "inputs": {"alpha": 1}}}, registry=registry
        )
        self.assertEqual([], audit["blocking"])


class RealSchemaBindingTests(unittest.TestCase):
    """Binder behaviour against the actual recorded ComfyUI schema."""

    @classmethod
    def setUpClass(cls):
        cls.registry = real_registry()
        cls.workflow = real_workflow()

    def test_registry_loads_real_schema(self):
        # The publishable fixture is deliberately minimized to node types used
        # by the committed workflows; it must still cover the full workflows.
        self.assertGreaterEqual(len(self.registry), 38)
        for name in ("UltimateSDUpscale", "KSamplerAdvanced", "easy stylesSelector", "ResolutionSelector"):
            self.assertTrue(self.registry.has(name), f"schema 缺少 {name}")

    def test_real_ultimate_sd_upscale_binds_every_parameter_by_name(self):
        node = real_node("UltimateSDUpscale")
        result = WidgetBinder.bind(node, self.registry)
        self.assertEqual([], result.problems)
        self.assertEqual("declared", result.strategy)

        # 21 values = 20 declared widgets + 1 hidden control_after_generate.
        self.assertEqual(20, len(result.declared))
        self.assertEqual(21, len(node["widgets_values"]))

        expected = {
            "upscale_by": 2.0, "seed": 999, "steps": 4, "cfg": 1.0,
            "sampler_name": "euler", "scheduler": "simple", "denoise": 0.15,
            "mode_type": "Linear", "tile_width": 1024, "tile_height": 1024,
            "mask_blur": 16, "tile_padding": 64, "seam_fix_mode": "None",
            "seam_fix_denoise": 1.0, "seam_fix_width": 64,
            "seam_fix_mask_blur": 8, "seam_fix_padding": 16,
            "force_uniform_tiles": True, "tiled_decode": False,
        }
        for name, want in expected.items():
            self.assertEqual(want, result.widgets[name], f"{name} 映射错误")

    def test_every_bound_combo_value_is_a_legal_candidate(self):
        """The invariant the old cursor violated.

        Overflowing the offset moved a value such as "randomize" into a *different*
        parameter, creating a value that was never in the workflow at all. So the
        precise invariant is: any value the binder produces that is not a legal
        candidate must be a value the workflow itself already contained. A
        misalignment breaks this because it manufactures new mismatches.
        """
        checked = 0
        introduced = []
        for node in self.workflow.get("nodes", []):
            node_type = str(node.get("type"))
            if not self.registry.has(node_type):
                continue
            # widgets_values can contain nested lists, which are not hashable.
            raw_values = {value for value in (node.get("widgets_values") or []) if not isinstance(value, list)}
            result = WidgetBinder.bind(node, self.registry)
            blocking = [p for p in result.problems if p.severity == "blocking"]
            self.assertEqual([], blocking, f"{node_type} 绑定被阻断")
            for name, value in result.widgets.items():
                if isinstance(value, list):
                    continue
                candidates = self.registry.combo_candidates(node_type, name)
                if not candidates:
                    continue
                checked += 1
                if value not in candidates and value not in raw_values:
                    introduced.append(f"{node_type}.{name}={value!r}")

        self.assertGreater(checked, 10, "没有校验到足够的候选参数")
        self.assertEqual([], introduced, "绑定过程凭空制造了非法值（位置漂移的典型症状）")

    def test_the_known_missing_upscale_model_is_really_illegal(self):
        """Documents the real defect: the workflow asks for a model not installed."""
        registry = self.registry
        candidates = registry.combo_candidates("UpscaleModelLoader", "model_name")
        self.assertEqual(["RealESRGAN_x4plus_anime_6B.pth"], candidates)
        ok, reason, offered, _ = registry.validate("UpscaleModelLoader", "model_name", "OmniSR_X4_DIV2K.safetensors")
        self.assertFalse(ok)
        self.assertEqual(["RealESRGAN_x4plus_anime_6B.pth"], offered)
        self.assertIn("候选", reason)

    def test_convert_node_records_provenance_for_linked_and_widget_inputs(self):
        node = real_node("UltimateSDUpscale")
        linked = {"model": ["26", 0], "upscale_model": ["11", 0]}
        inputs, sources, problems = convert_node(
            str(node["id"]), node, linked,
            config=_config(), target_size=(1280, 720),
            output_prefix="ComfyBatch-V2/test", prompt_text="p",
            source_image="", registry=self.registry,
        )
        self.assertEqual([], problems)
        self.assertEqual(SOURCE_LINKED, sources["model"])
        self.assertEqual(SOURCE_LINKED, sources["upscale_model"])
        # mask_blur is a widget value that the UltimateSDUpscale converter then
        # normalises, so it is recorded as an override rather than a raw widget.
        self.assertEqual(SOURCE_OVERRIDE, sources["mask_blur"])
        # tile_width/tile_height are linked in the workflow, so the link wins.
        self.assertEqual(["11", 0], inputs["upscale_model"])

    def test_upscale_model_can_be_overridden_from_params(self):
        node = real_node("UltimateSDUpscale")
        config = _config(params={"upscale_model": "RealESRGAN_x4plus_anime_6B.pth", "upscale_by": 1.5})
        inputs, sources, _ = convert_node(
            str(node["id"]), node, {}, config=config, target_size=(1280, 720),
            output_prefix="p", prompt_text="p", source_image="", registry=self.registry,
        )
        # upscale_model is unlinked here, so the override is what lands.
        self.assertEqual(SOURCE_OVERRIDE, sources["upscale_by"])
        self.assertEqual(1.5, inputs["upscale_by"])


class KSamplerAdvancedTests(unittest.TestCase):
    """The node the previous patch handled by hand, now handled generically."""

    def test_advanced_sampler_widgets_map_by_name(self):
        node = {
            "id": 5,
            "type": "KSamplerAdvanced",
            "inputs": [
                {"name": "add_noise", "widget": {"name": "add_noise"}, "link": None},
                {"name": "noise_seed", "widget": {"name": "noise_seed"}, "link": 8},
                {"name": "steps", "widget": {"name": "steps"}, "link": None},
                {"name": "cfg", "widget": {"name": "cfg"}, "link": None},
                {"name": "sampler_name", "widget": {"name": "sampler_name"}, "link": None},
                {"name": "scheduler", "widget": {"name": "scheduler"}, "link": None},
                {"name": "start_at_step", "widget": {"name": "start_at_step"}, "link": None},
                {"name": "end_at_step", "widget": {"name": "end_at_step"}, "link": None},
                {"name": "return_with_leftover_noise", "widget": {"name": "return_with_leftover_noise"}, "link": None},
            ],
            "widgets_values": ["enable", 123, "randomize", 10, 1, "euler", "simple", 4, 999, "disable"],
        }
        inputs, _sources, problems = convert_node(
            "5", node, {"noise_seed": ["8", 0], "model": ["1", 0]},
            config=_config(), target_size=(1280, 720), output_prefix="p",
            prompt_text="p", source_image="", registry=real_registry(),
        )
        self.assertEqual([], problems)
        self.assertEqual("enable", inputs["add_noise"])
        self.assertEqual(10, inputs["steps"])
        self.assertEqual(1.0, inputs["cfg"])
        self.assertEqual("euler", inputs["sampler_name"])
        self.assertEqual("simple", inputs["scheduler"])
        self.assertEqual(4, inputs["start_at_step"])
        self.assertEqual(999, inputs["end_at_step"])
        self.assertEqual("disable", inputs["return_with_leftover_noise"])
        # A linked seed must be preserved, not overwritten with a random value.
        self.assertEqual(["8", 0], inputs["noise_seed"])


def _config(**overrides):
    from comfybatch_v2_core import BatchConfig

    values = {"workflow_path": "w.json", "model": "m.safetensors", "aspect_ratio": "1:1 (Square)", "megapixels": 1.0}
    values.update(overrides)
    return BatchConfig(**values)


if __name__ == "__main__":
    unittest.main()
