"""使用目的与工作流预检：六种管线用途，以及"这个工作流能不能干这件事"。

做这件事的动机是一个很安静的失败：用户拿一个**没有放大分支**的工作流去跑"放大超分"，
批次会正常完成、正常出图、正常报成功——只是每张图都不是他想要的。页面上没有任何一处
说过这个工作流干不了他要的事。

所以这里的核心不是"多一个下拉框"，而是**把用途声明变成可校验的条件**：选了用途，
预检就必须回答"工作流满足吗"，不满足就阻断并说清缺哪一环，而不是安静地跑出一批
不对的图。

六种用途按**生成管线**划分，因为管线的每一段都能从编译图里验证：

| 用途 | 判定依据 |
|---|---|
| 文生图 | 采样器的潜空间来自空潜空间节点 |
| 图生图／参考图 | 采样器的上游闭包里有图像输入 |
| 局部重绘 | 采样器的上游闭包里有遮罩／内补节点 |
| 高清重绘（二采） | 存在一个采样器，其潜空间来自另一个采样器 |
| 放大超分 | 存在图像空间的放大器，位于采样之后、保存之前 |
| 批量变体 | 图里有可直接写入的具体种子 |

"不指定用途"是一种合法状态：不做任何断言，也就什么都不阻断。这样 V2.20 的行为
完全不变，用途检查是用户主动打开的一道闸门。
"""

from __future__ import annotations

import copy
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from fakes import SOURCE_ROOT  # noqa: E402

from comfybatch_errors import ErrorCategory, SEVERITY_BLOCKING, WORKFLOW_LEVEL  # noqa: E402
from comfybatch_nodeschema import (  # noqa: E402
    CompiledGraphAudit,
    NodeSchemaRegistry,
    PURPOSE_IDS,
    PURPOSES,
    purpose_catalog,
    purpose_facts,
    purpose_plan,
    purpose_problems,
    set_active_registry,
)
from comfybatch_v2_core import BatchConfig, Krea2WorkflowAdapter  # noqa: E402

FIXTURES = SOURCE_ROOT.parent / "tests" / "fixtures"
JI_QING = "workflow_Krea2-极清生图流_SeedVR2-int8图像放大.json"
GAO_QING = "workflow_Krea2-高清生图优化流_整合.json"
FENG_GE = "workflow_Krea2-风格扩展流_397种.json"

#: Compiling the same fixture repeatedly is wasteful, so the graphs the real
#: workflows produce are built once and shared. They are read-only in tests.
_GRAPH_CACHE: dict[tuple[str, str], dict] = {}


def real_graph(name: str, variant: str = "") -> dict:
    key = (name, variant)
    if key not in _GRAPH_CACHE:
        registry = NodeSchemaRegistry.from_path(FIXTURES / "object_info.json")
        set_active_registry(registry)
        config = BatchConfig(
            workflow_path=str(FIXTURES / name),
            model="redcraftHybridH3A2A_30Krea2.safetensors",
            aspect_ratio="16:9 (Widescreen)",
            megapixels=0.5,
            workflow_variant=variant,
        )
        adapter = Krea2WorkflowAdapter.from_path(config.workflow_path, registry)
        report = adapter.capabilities(config)
        if not report["ready"]:
            raise AssertionError(f"{name} {variant} 预检未通过：{report['errors']}")
        adapter.build("用途测试", config, "ComfyBatch-V2/purpose-test")
        _GRAPH_CACHE[key] = adapter.last_graph
    return _GRAPH_CACHE[key]


def graph_of(*nodes: tuple[str, str, dict]) -> dict:
    """A small API graph: ``(node_id, class_type, inputs)`` triples."""
    return {node_id: {"class_type": ctype, "inputs": inputs} for node_id, ctype, inputs in nodes}


def text_to_image_graph() -> dict:
    """The shape every real Krea2 fixture has: text -> empty latent -> sample."""
    return graph_of(
        ("8", "UNETLoader", {"unet_name": "m.safetensors", "weight_dtype": "default"}),
        ("9", "VAELoader", {"vae_name": "v.safetensors"}),
        ("25", "CLIPTextEncode", {"text": "a", "clip": ["8", 1]}),
        ("16", "EmptyLatentImage", {"width": 1024, "height": 576, "batch_size": 1}),
        ("13", "KSampler", {"model": ["8", 0], "seed": 1, "steps": 8, "latent_image": ["16", 0]}),
        ("18", "VAEDecode", {"samples": ["13", 0], "vae": ["9", 0]}),
        ("6", "SaveImage", {"images": ["18", 0], "filename_prefix": "x"}),
    )


def with_refine(graph: dict) -> dict:
    """Add a second sampler fed by a latent upscale of the first."""
    out = copy.deepcopy(graph)
    out.update(graph_of(
        ("22", "LatentUpscaleBy", {"samples": ["13", 0], "scale_by": 2.0}),
        ("12", "KSampler", {"model": ["8", 0], "seed": 2, "steps": 8, "latent_image": ["22", 0]}),
        ("19", "VAEDecode", {"samples": ["12", 0], "vae": ["9", 0]}),
    ))
    out["6"]["inputs"]["images"] = ["19", 0]
    return out


def with_image_input(graph: dict) -> dict:
    """Replace the empty latent with an encoded input image (img2img)."""
    out = copy.deepcopy(graph)
    out.update(graph_of(
        ("1", "LoadImage", {"image": "ref.png", "upload": "image"}),
        ("2", "VAEEncode", {"pixels": ["1", 0], "vae": ["9", 0]}),
    ))
    out["13"]["inputs"]["latent_image"] = ["2", 0]
    del out["16"]
    return out


def with_inpaint(graph: dict) -> dict:
    """An input image plus a mask, encoded for inpainting."""
    out = with_image_input(graph)
    out.update(graph_of(
        ("40", "LoadImageMask", {"image": "mask.png", "channel": "red"}),
        ("41", "InpaintModelConditioning", {"positive": ["25", 0], "mask": ["40", 0]}),
    ))
    out["13"]["inputs"]["latent_image"] = ["41", 1]
    return out


def with_upscale(graph: dict) -> dict:
    """An image-space upscaler between the decode and the save."""
    out = copy.deepcopy(graph)
    out.update(graph_of(
        ("11", "UpscaleModelLoader", {"model_name": "up.pth"}),
        ("3", "ImageUpscaleWithModel", {"upscale_model": ["11", 0], "image": ["18", 0]}),
    ))
    out["6"]["inputs"]["images"] = ["3", 0]
    return out


class PurposeCatalogTests(unittest.TestCase):
    """The six purposes themselves."""

    def test_exactly_six_purposes_are_declared(self):
        self.assertEqual(6, len(PURPOSES))
        self.assertEqual(6, len(PURPOSE_IDS))

    def test_every_purpose_has_a_stable_id_a_name_and_a_summary(self):
        for item in PURPOSES:
            self.assertTrue(item["id"], item)
            self.assertTrue(item["name"], item)
            self.assertTrue(item["summary"], item)
            self.assertRegex(item["id"], r"^[a-z][a-z0-9_]*$")

    def test_purpose_ids_are_unique_and_in_the_declared_order(self):
        self.assertEqual(len(set(PURPOSE_IDS)), len(PURPOSE_IDS))
        self.assertEqual([item["id"] for item in PURPOSES], list(PURPOSE_IDS))

    def test_the_six_pipeline_stages_are_all_covered(self):
        # Naming them in a test makes an accidental rename or drop visible.
        self.assertEqual(
            ["txt2img", "img2img", "inpaint", "refine", "upscale", "variants"],
            list(PURPOSE_IDS),
        )

    def test_the_catalog_is_serialisable_for_the_page(self):
        catalog = purpose_catalog()
        self.assertEqual(6, len(catalog))
        for item in catalog:
            self.assertEqual({"id", "name", "summary"}, set(item))


class PurposeFactsTests(unittest.TestCase):
    """What the compiled graph actually says."""

    def test_text_to_image_workflow_has_an_empty_latent_feeding_a_sampler(self):
        facts = purpose_facts(real_graph(JI_QING))
        self.assertIn("16", facts["latent_sources"])
        self.assertTrue(facts["text_to_image"], facts)
        self.assertEqual([], facts["image_branches"])
        self.assertEqual([], facts["inpaint_branches"])

    def test_the_upscale_branch_exists_only_in_the_variant_that_has_it(self):
        without = purpose_facts(real_graph(JI_QING, ""))
        with_up = purpose_facts(real_graph(JI_QING, "save:1"))
        self.assertEqual([], without["upscalers_before_save"])
        self.assertIn("3", with_up["upscalers_before_save"])

    def test_a_second_sampler_after_a_latent_upscale_is_detected(self):
        facts = purpose_facts(real_graph(JI_QING, ""))
        self.assertIn("13", facts["second_pass_samplers"])

    def test_a_workflow_without_a_second_pass_reports_none(self):
        # 风格扩展流 is a single KSampler straight into the save node.
        facts = purpose_facts(real_graph(FENG_GE, ""))
        self.assertEqual([], facts["second_pass_samplers"])
        self.assertEqual([], facts["upscalers_before_save"])

    def test_a_latent_upscaler_is_not_counted_as_an_image_upscaler(self):
        # LatentUpscaleBy sits between the two samplers; it is part of 高清重绘,
        # not of 放大超分 -- the project's own docs separate 二采 from SeedVR 放大.
        facts = purpose_facts(real_graph(JI_QING, ""))
        self.assertIn("22", facts["latent_upscalers"])
        self.assertEqual([], facts["upscalers_before_save"])

    def test_a_plain_resize_is_not_an_upscaler(self):
        """A resizer must not satisfy 放大超分.

        Matching the class name on ``scale`` alone also matches ``ImageScale``,
        ``ImageScaleBy``, ``ImageScaleToTotalPixels``, ``RescaleCFG`` and
        ``NumberScaler`` -- all present in this install. Counting one as an
        upscaler would let the batch through and return a picture that was only
        resized, which is the failure this layer exists to prevent.
        """
        graph = text_to_image_graph()
        graph.update(graph_of(
            ("3", "ImageScale", {"image": ["18", 0], "width": 2048, "height": 1152}),
        ))
        graph["6"]["inputs"]["images"] = ["3", 0]
        facts = purpose_facts(graph)
        self.assertEqual([], facts["upscalers"])
        self.assertEqual([], facts["upscalers_before_save"])
        self.assertFalse(purpose_plan(graph, "upscale")["ready"])

    def test_a_model_based_upscaler_is_an_upscaler(self):
        graph = with_upscale(text_to_image_graph())
        facts = purpose_facts(graph)
        self.assertIn("3", facts["upscalers"])
        self.assertIn("3", facts["upscalers_before_save"])
        self.assertTrue(purpose_plan(graph, "upscale")["ready"])

    def test_the_real_workflows_have_a_writable_seed(self):
        for name in (JI_QING, GAO_QING, FENG_GE):
            facts = purpose_facts(real_graph(name, ""))
            self.assertGreater(facts["seed_count"], 0, name)

    def test_an_image_input_is_found_only_when_it_feeds_the_sampler(self):
        self.assertEqual(["13"], purpose_facts(with_image_input(text_to_image_graph()))["image_branches"])
        # A LoadImage sitting next to the graph, wired to nothing, is not a branch.
        orphan = text_to_image_graph()
        orphan.update(graph_of(("1", "LoadImage", {"image": "ref.png"})))
        self.assertEqual([], purpose_facts(orphan)["image_branches"])

    def test_a_mask_node_outside_the_sampler_chain_is_not_an_inpaint_branch(self):
        orphan = text_to_image_graph()
        orphan.update(graph_of(("40", "LoadImageMask", {"image": "mask.png"})))
        self.assertEqual([], purpose_facts(orphan)["inpaint_branches"])

    def test_facts_on_an_empty_graph_do_not_raise(self):
        facts = purpose_facts({})
        self.assertEqual([], facts["samplers"])
        self.assertFalse(facts["text_to_image"])
        self.assertEqual(0, facts["seed_count"])


class PurposePlanTests(unittest.TestCase):
    """Six purposes against the same graph, so the differences are visible."""

    def test_text_to_image_is_satisfied_by_every_real_workflow(self):
        for name in (JI_QING, GAO_QING, FENG_GE):
            plan = purpose_plan(real_graph(name, ""), "txt2img")
            self.assertTrue(plan["ready"], (name, plan))
            self.assertTrue(plan["checked"])
            self.assertEqual([], [item for item in plan["requirements"] if not item["met"]])

    def test_upscale_is_refused_by_the_workflow_without_an_upscale_branch(self):
        plan = purpose_plan(real_graph(JI_QING, ""), "upscale")
        self.assertFalse(plan["ready"])
        self.assertEqual(["upscale_branch"], [item["key"] for item in plan["requirements"] if not item["met"]])
        self.assertIn("放大", plan["detail"])

    def test_upscale_is_accepted_once_the_upscale_variant_is_selected(self):
        plan = purpose_plan(real_graph(JI_QING, "save:1"), "upscale")
        self.assertTrue(plan["ready"], plan)

    def test_inpaint_is_refused_by_a_workflow_that_cannot_mask(self):
        plan = purpose_plan(real_graph(JI_QING, ""), "inpaint")
        self.assertFalse(plan["ready"])
        self.assertIn("局部重绘", plan["name"])

    def test_image_to_image_is_refused_by_a_text_only_workflow(self):
        plan = purpose_plan(real_graph(JI_QING, ""), "img2img")
        self.assertFalse(plan["ready"])
        self.assertIn("图生图", plan["name"])

    def test_each_purpose_is_accepted_by_the_graph_that_implements_it(self):
        base = text_to_image_graph()
        cases = {
            "txt2img": base,
            "img2img": with_image_input(base),
            "inpaint": with_inpaint(base),
            "refine": with_refine(base),
            "upscale": with_upscale(base),
            "variants": base,
        }
        for purpose, graph in cases.items():
            plan = purpose_plan(graph, purpose)
            self.assertTrue(plan["ready"], (purpose, plan))

    def test_each_purpose_is_refused_by_a_graph_that_lacks_its_stage(self):
        base = text_to_image_graph()
        cases = {
            "img2img": base,
            "inpaint": base,
            "refine": base,
            "upscale": base,
        }
        for purpose, graph in cases.items():
            plan = purpose_plan(graph, purpose)
            self.assertFalse(plan["ready"], (purpose, plan))
            self.assertTrue(plan["detail"], purpose)

    def test_no_purpose_asserts_nothing(self):
        plan = purpose_plan(real_graph(JI_QING, ""), "")
        self.assertFalse(plan["checked"])
        self.assertTrue(plan["ready"])
        self.assertEqual([], plan["requirements"])
        self.assertEqual("", plan["detail"])

    def test_an_unknown_purpose_is_reported_rather_than_ignored(self):
        plan = purpose_plan(real_graph(JI_QING, ""), "make-coffee")
        self.assertTrue(plan["unknown"])
        self.assertFalse(plan["ready"])
        self.assertIn("make-coffee", plan["detail"])

    def test_requirements_carry_evidence_when_they_are_met(self):
        plan = purpose_plan(real_graph(JI_QING, ""), "txt2img")
        met = [item for item in plan["requirements"] if item["met"]]
        self.assertTrue(met)
        for item in met:
            self.assertTrue(item["evidence"], item)

    def test_requirements_carry_a_fix_hint_when_they_are_not_met(self):
        plan = purpose_plan(real_graph(JI_QING, ""), "upscale")
        unmet = [item for item in plan["requirements"] if not item["met"]]
        self.assertTrue(unmet)
        for item in unmet:
            self.assertTrue(item["hint"], item)

    def test_the_plan_can_be_serialised_for_the_page(self):
        plan = purpose_plan(real_graph(JI_QING, "save:1"), "upscale")
        for key in ("id", "name", "summary", "checked", "ready", "requirements", "detail"):
            self.assertIn(key, plan)
        for item in plan["requirements"]:
            self.assertEqual({"key", "label", "met", "evidence", "hint"}, set(item))


class PurposeProblemTests(unittest.TestCase):
    """An unmet purpose must arrive as a blocking, explainable problem."""

    def test_a_met_purpose_produces_no_problem(self):
        self.assertEqual([], purpose_problems(purpose_plan(real_graph(JI_QING, ""), "txt2img")))

    def test_an_unmet_purpose_blocks_and_names_the_missing_stage(self):
        problems = purpose_problems(purpose_plan(real_graph(JI_QING, ""), "upscale"))
        self.assertEqual(1, len(problems))
        problem = problems[0]
        self.assertEqual(ErrorCategory.PURPOSE_UNSUPPORTED, problem.category)
        self.assertEqual(SEVERITY_BLOCKING, problem.severity)
        self.assertTrue(problem.workflow_level)
        self.assertIn("放大超分", problem.detail)
        self.assertIn("放大", problem.detail)
        self.assertTrue(problem.fixes)

    def test_an_unknown_purpose_blocks_with_its_own_wording(self):
        problems = purpose_problems(purpose_plan(real_graph(JI_QING, ""), "nope"))
        self.assertEqual(1, len(problems))
        self.assertIn("nope", problems[0].detail)
        self.assertTrue(problems[0].workflow_level)

    def test_no_purpose_produces_no_problem(self):
        self.assertEqual([], purpose_problems(purpose_plan(real_graph(JI_QING, ""), "")))

    def test_the_category_is_workflow_level_so_a_batch_stops_instead_of_repeating(self):
        self.assertIn(ErrorCategory.PURPOSE_UNSUPPORTED, WORKFLOW_LEVEL)


class AuditPurposeTests(unittest.TestCase):
    """The fifth preflight layer, wired where preflight already reads."""

    def test_the_audit_reports_the_purpose_block(self):
        graph = real_graph(JI_QING, "")
        result = CompiledGraphAudit.run(graph, purpose="txt2img")
        self.assertTrue(result["purpose"]["ready"])
        self.assertEqual("txt2img", result["purpose"]["id"])

    def test_an_unmet_purpose_appears_in_the_audit_blocking_list(self):
        graph = real_graph(JI_QING, "")
        result = CompiledGraphAudit.run(graph, purpose="upscale")
        categories = [item["category"] for item in result["blocking"]]
        self.assertIn(ErrorCategory.PURPOSE_UNSUPPORTED, categories)

    def test_an_unmet_purpose_also_appears_in_the_full_problem_list(self):
        graph = real_graph(JI_QING, "")
        result = CompiledGraphAudit.run(graph, purpose="upscale")
        categories = [item["category"] for item in result["problems"]]
        self.assertIn(ErrorCategory.PURPOSE_UNSUPPORTED, categories)

    def test_no_purpose_leaves_the_audit_exactly_as_before(self):
        graph = real_graph(JI_QING, "")
        without = CompiledGraphAudit.run(graph)
        self.assertFalse(without.get("blocking"))
        self.assertFalse(without["purpose"]["checked"])
        self.assertEqual("", without["purpose"]["id"])

    def test_the_audit_still_runs_its_own_layers_when_a_purpose_is_set(self):
        graph = real_graph(JI_QING, "save:1")
        result = CompiledGraphAudit.run(graph, purpose="upscale")
        self.assertTrue(result["node_count"])
        self.assertTrue(result["input_count"])
        self.assertEqual([], result["blocking"])


class PurposeConfigTests(unittest.TestCase):
    """The purpose travels with the batch config, like every other knob."""

    def test_a_config_without_a_purpose_defaults_to_none_selected(self):
        self.assertEqual("", BatchConfig(workflow_path="w", model="m").purpose)

    def test_from_dict_carries_the_purpose(self):
        config = BatchConfig.from_dict({"workflow_path": "w", "model": "m", "purpose": "upscale"})
        self.assertEqual("upscale", config.purpose)

    def test_from_dict_keeps_an_unknown_purpose_so_preflight_can_refuse_it(self):
        # Silently downgrading it to "" would turn a typo into "no assertion",
        # which reads on the page as "everything is fine".
        config = BatchConfig.from_dict({"workflow_path": "w", "model": "m", "purpose": "upscale2"})
        self.assertEqual("upscale2", config.purpose)

    def test_a_missing_purpose_key_is_still_empty(self):
        config = BatchConfig.from_dict({"workflow_path": "w", "model": "m"})
        self.assertEqual("", config.purpose)


class PurposePageAssetTests(unittest.TestCase):
    """The page offers the six purposes and shows the verdict."""

    def test_the_page_offers_a_purpose_selector(self):
        from fakes import page_source  # noqa: PLC0415 - local to this assertion
        source = page_source()
        self.assertIn("purposeSelect", source)
        self.assertIn("使用目的", source)

    def test_the_page_states_the_current_purpose_in_the_truth_rail(self):
        from fakes import page_source  # noqa: PLC0415
        self.assertIn("railPurpose", page_source())

    def test_the_script_renders_a_verdict_for_the_selected_purpose(self):
        from fakes import js_source  # noqa: PLC0415
        source = js_source()
        self.assertIn("purposeFactText", source)
        self.assertIn("purposeOptions", source)

    def test_the_script_knows_all_six_ids(self):
        from fakes import js_source  # noqa: PLC0415
        source = js_source()
        for purpose in PURPOSE_IDS:
            self.assertIn(purpose, source, purpose)

    def test_the_script_prefers_the_catalog_the_server_sends(self):
        """The built-in list is a fallback, not a second source of truth."""
        from fakes import js_source  # noqa: PLC0415
        source = js_source()
        self.assertIn("inventory.purposes", source)
        self.assertIn("purposeFallback", source)

    def test_the_stylesheet_styles_the_unsupported_state(self):
        from fakes import css_source  # noqa: PLC0415
        self.assertIn("purpose-unsupported", css_source())


if __name__ == "__main__":
    unittest.main()
