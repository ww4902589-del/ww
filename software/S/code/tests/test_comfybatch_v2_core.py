import json
import io
import pathlib
import struct
import tempfile
import time
import unittest
import zipfile

from PIL import Image

from comfybatch_v2_core import (
    BatchConfig,
    BatchRunner,
    ImageQualityInspector,
    Krea2WorkflowAdapter,
    PromptCompiler,
    PromptBundleParser,
    ResourceInventory,
    _safe_name,
)
from comfybatch_nodeschema import SOURCE_INJECTED, structural_fingerprint


class PromptBundleInterfaceTests(unittest.TestCase):
    def test_imports_catalog_and_plain_text_through_one_interface(self):
        catalog = {
            "items": [
                {
                    "title": "银色风琴褶裤",
                    "brief": "突出裤装结构",
                    "layers": {"base": "针织内搭", "silhouette": "宽腿裤"},
                    "scene": "白色展厅",
                }
            ]
        }
        parsed = PromptBundleParser.parse("catalog.json", json.dumps(catalog, ensure_ascii=False).encode("utf-8"))
        self.assertEqual(1, len(parsed.items))
        self.assertEqual("银色风琴褶裤", parsed.items[0].title)
        self.assertIn("宽腿裤", parsed.items[0].prompt)

        lines = PromptBundleParser.parse("prompts.txt", "红色风衣\n\n蓝色礼服\n".encode("utf-8"))
        self.assertEqual(["红色风衣", "蓝色礼服"], [item.prompt for item in lines.items])

    def test_finds_prompts_in_common_json_shapes_and_nested_fields(self):
        cases = [
            {"prompts": [{"title": "第一套", "prompt": "红色短外套"}]},
            {"tasks": [{"name": "第二套", "content": {"positive_prompt": "蓝色长裙"}}]},
            {"data": {"records": [{"title": "第三套", "description": "绿色礼服"}]}},
        ]
        expected = ["红色短外套", "蓝色长裙", "绿色礼服"]
        for index, payload in enumerate(cases):
            with self.subTest(index=index):
                bundle = PromptBundleParser.parse(
                    f"case-{index}.json",
                    json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                )
                self.assertEqual(1, len(bundle.items))
                self.assertEqual(expected[index], bundle.items[0].prompt)

    def test_groups_long_text_by_numbered_prompt_instead_of_every_line(self):
        text = """1. 雨夜礼服
黑色鱼尾长裙，人物站在剧院门口。
低机位全身镜头。

2. 夏日水手服
白色水手上衣与蓝色百褶裙。
海边横版构图。
"""
        bundle = PromptBundleParser.parse("long.txt", text.encode("utf-8"))
        self.assertEqual(2, len(bundle.items))
        self.assertEqual("雨夜礼服", bundle.items[0].title)
        self.assertIn("低机位全身镜头", bundle.items[0].prompt)

    def test_reads_docx_paragraphs_as_prompt_tasks(self):
        with tempfile.TemporaryDirectory() as temp:
            path = pathlib.Path(temp) / "prompts.docx"
            xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
<w:p><w:r><w:t>1. 第一套</w:t></w:r></w:p>
<w:p><w:r><w:t>白色方领上衣，红色长裙。</w:t></w:r></w:p>
<w:p><w:r><w:t>2. 第二套</w:t></w:r></w:p>
<w:p><w:r><w:t>蓝色水手服，海边场景。</w:t></w:r></w:p>
</w:body></w:document>"""
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("word/document.xml", xml)
            bundle = PromptBundleParser.parse(path.name, path.read_bytes())
            self.assertEqual(2, len(bundle.items))
            self.assertEqual("第一套", bundle.items[0].title)

    def test_docx_aspect_ratio_prefix_is_not_mistaken_for_numbered_heading(self):
        with tempfile.TemporaryDirectory() as temp:
            path = pathlib.Path(temp) / "aspect-prompts.docx"
            xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
<w:p><w:r><w:t>1</w:t></w:r></w:p>
<w:p><w:r><w:t>16:9横向2D插画，第一套蓝色水手服。</w:t></w:r></w:p>
<w:p><w:r><w:t>16:9横向2D插画，第二套红色洛丽塔裙。</w:t></w:r></w:p>
</w:body></w:document>"""
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("word/document.xml", xml)
            bundle = PromptBundleParser.parse(path.name, path.read_bytes())
            self.assertEqual(2, len(bundle.items))
            self.assertEqual("提示词 001", bundle.items[0].title)
            self.assertTrue(bundle.items[0].prompt.startswith("16:9横向"))
            self.assertIn("第二套红色洛丽塔裙", bundle.items[1].prompt)

    def test_xlsx_single_column_prompt_header_is_not_imported_as_a_task(self):
        with tempfile.TemporaryDirectory() as temp:
            path = pathlib.Path(temp) / "prompts.xlsx"
            shared = """<?xml version="1.0" encoding="UTF-8"?>
<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="3" uniqueCount="3">
<si><t>完整提示词</t></si><si><t>第一套动漫服饰提示词</t></si><si><t>第二套手游服饰提示词</t></si>
</sst>"""
            sheet = """<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>
<row r="1"><c r="A1" t="s"><v>0</v></c></row>
<row r="2"><c r="A2" t="s"><v>1</v></c></row>
<row r="3"><c r="A3" t="s"><v>2</v></c></row>
</sheetData></worksheet>"""
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("xl/sharedStrings.xml", shared)
                archive.writestr("xl/worksheets/sheet1.xml", sheet)
            bundle = PromptBundleParser.parse(path.name, path.read_bytes())
            self.assertEqual(2, len(bundle.items))
            self.assertEqual("第一套动漫服饰提示词", bundle.items[0].prompt)

    def test_imports_positive_and_negative_prompts_from_json_text_and_xlsx(self):
        payload = {"items": [{"title": "礼服", "positive_prompt": "蓝色礼服", "negative_prompt": "真人, 多人"}]}
        json_bundle = PromptBundleParser.parse("prompts.json", json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        self.assertEqual("真人, 多人", json_bundle.items[0].negative_prompt)

        text = "1. 水手服\n正面提示词：蓝白水手服\n负面提示词：真人，三视图\n"
        text_bundle = PromptBundleParser.parse("prompts.txt", text.encode("utf-8"))
        self.assertEqual("蓝白水手服", text_bundle.items[0].prompt)
        self.assertEqual("真人，三视图", text_bundle.items[0].negative_prompt)

        with tempfile.TemporaryDirectory() as temp:
            path = pathlib.Path(temp) / "prompts.xlsx"
            shared = """<?xml version="1.0" encoding="UTF-8"?>
<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="5" uniqueCount="5">
<si><t>标题</t></si><si><t>正向提示词</t></si><si><t>负面提示词</t></si><si><t>洛丽塔</t></si><si><t>真人, 多人</t></si>
</sst>"""
            sheet = """<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>
<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c><c r="C1" t="s"><v>2</v></c></row>
<row r="2"><c r="A2" t="s"><v>3</v></c><c r="B2" t="s"><v>3</v></c><c r="C2" t="s"><v>4</v></c></row>
</sheetData></worksheet>"""
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("xl/sharedStrings.xml", shared)
                archive.writestr("xl/worksheets/sheet1.xml", sheet)
            xlsx_bundle = PromptBundleParser.parse(path.name, path.read_bytes())
            self.assertEqual("洛丽塔", xlsx_bundle.items[0].title)
            self.assertEqual("洛丽塔", xlsx_bundle.items[0].prompt)
            self.assertEqual("真人, 多人", xlsx_bundle.items[0].negative_prompt)

    def test_xlsx_recognises_version_annotation_in_prompt_header(self):
        shared = """<?xml version="1.0" encoding="UTF-8"?>
<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="4" uniqueCount="4">
<si><t>完整正向Prompt（第一版结构）</t></si><si><t>Negative Prompt</t></si>
<si><t>High-finish 2D hanfu illustration</t></si><si><t>multiple people, photorealistic</t></si>
</sst>"""
        sheet = """<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>
<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>
<row r="2"><c r="A2" t="s"><v>2</v></c><c r="B2" t="s"><v>3</v></c></row>
</sheetData></worksheet>"""
        raw = io.BytesIO()
        with zipfile.ZipFile(raw, "w") as archive:
            archive.writestr("xl/sharedStrings.xml", shared)
            archive.writestr("xl/worksheets/sheet1.xml", sheet)

        mapping = PromptBundleParser.xlsx_mapping_info(raw.getvalue())
        bundle = PromptBundleParser.parse("hanfu.xlsx", raw.getvalue())

        self.assertEqual("完整正向Prompt（第一版结构）", mapping["detected"]["positive"])
        self.assertEqual("Negative Prompt", mapping["detected"]["negative"])
        self.assertEqual(1, len(bundle.items))
        self.assertEqual("High-finish 2D hanfu illustration", bundle.items[0].prompt)
        self.assertEqual("multiple people, photorealistic", bundle.items[0].negative_prompt)

    def test_xlsx_recognises_independent_prompt_aliases_and_preserves_metadata(self):
        shared_values = [
            "编号", "页面标题", "独立正向Prompt", "独立负向Prompt", "风险等级",
            "01", "月白清庭", "2D hanfu illustration", "photorealistic, multiple people", "S",
        ]
        shared = '<?xml version="1.0" encoding="UTF-8"?><sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">' + "".join(
            f"<si><t>{value}</t></si>" for value in shared_values
        ) + "</sst>"
        sheet = """<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>
<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c><c r="C1" t="s"><v>2</v></c><c r="D1" t="s"><v>3</v></c><c r="E1" t="s"><v>4</v></c></row>
<row r="2"><c r="A2" t="s"><v>5</v></c><c r="B2" t="s"><v>6</v></c><c r="C2" t="s"><v>7</v></c><c r="D2" t="s"><v>8</v></c><c r="E2" t="s"><v>9</v></c></row>
</sheetData></worksheet>"""
        raw = io.BytesIO()
        with zipfile.ZipFile(raw, "w") as archive:
            archive.writestr("xl/sharedStrings.xml", shared)
            archive.writestr("xl/worksheets/sheet1.xml", sheet)
        bundle = PromptBundleParser.parse("hanfu.xlsx", raw.getvalue())
        self.assertEqual(1, len(bundle.items))
        self.assertEqual("月白清庭", bundle.items[0].title)
        self.assertEqual("2D hanfu illustration", bundle.items[0].prompt)
        self.assertEqual("photorealistic, multiple people", bundle.items[0].negative_prompt)
        self.assertEqual("S", bundle.items[0].metadata["风险等级"])
        self.assertEqual("01", bundle.items[0].metadata["编号"])

        remapped = PromptBundleParser.parse(
            "hanfu.xlsx", raw.getvalue(),
            mapping={"title": "编号", "positive": "页面标题", "negative": "独立负向Prompt"},
        )
        self.assertEqual("01", remapped.items[0].title)
        self.assertEqual("月白清庭", remapped.items[0].prompt)

    def test_xlsx_mapping_info_lists_headers_and_detected_roles(self):
        configured = __import__("os").environ.get("COMFYBATCH_TEST_XLSX", "")
        path = pathlib.Path(configured) if configured else None
        if not path or not path.is_file():
            self.skipTest("未提供 COMFYBATCH_TEST_XLSX")
        info = PromptBundleParser.xlsx_mapping_info(path.read_bytes())
        self.assertIn("独立正向Prompt", info["headers"])
        self.assertEqual("页面标题", info["detected"]["title"])
        self.assertEqual("独立正向Prompt", info["detected"]["positive"])
        self.assertEqual("独立负向Prompt", info["detected"]["negative"])

    def test_safe_name_handles_windows_rules_and_length(self):
        self.assertEqual("16_9_人物_横版", _safe_name("16:9/人物\n横版. "))
        self.assertEqual("_CON", _safe_name("CON"))
        self.assertLessEqual(len(_safe_name("长" * 200)), 80)


class InventoryInterfaceTests(unittest.TestCase):
    def test_discovers_selectable_resources(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            comfy = root / "ComfyUI"
            workflows = root / "workflows"
            (comfy / "models" / "diffusion_models").mkdir(parents=True)
            (comfy / "models" / "loras").mkdir(parents=True)
            styles = comfy / "custom_nodes" / "easy" / "styles"
            styles.mkdir(parents=True)
            workflows.mkdir()
            (comfy / "models" / "diffusion_models" / "red.safetensors").write_bytes(b"")
            (comfy / "models" / "loras" / "dress.safetensors").write_bytes(b"")
            (styles / "krea2_anime_动漫_styles.json").write_text(
                '[{"name":"Anime Style","name_cn":"动漫风格","prompt":"{prompt}","thumbnail":"https://example.com/anime.webp"}]',
                encoding="utf-8",
            )
            (workflows / "flow.json").write_text(json.dumps({"nodes": [{"id": 1, "type": "UNETLoader"}]}), encoding="utf-8")

            result = ResourceInventory(comfy, [workflows]).snapshot()
            self.assertEqual("red.safetensors", result["models"][0]["value"])
            self.assertEqual("dress.safetensors", result["loras"][0]["value"])
            self.assertEqual("Anime Style", result["styles"][0]["name"])
            self.assertEqual("动漫风格", result["styles"][0]["name_cn"])
            self.assertEqual("动漫风格", result["styles"][0]["display_name"])
            self.assertEqual("动漫风格合集", result["styles"][0]["library_cn"])
            self.assertEqual("https://example.com/anime.webp", result["styles"][0]["thumbnail"])
            self.assertEqual("flow.json", result["workflows"][0]["name"])

    def test_reports_workflow_model_compatibility_and_lora_triggers(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            comfy = root / "ComfyUI"
            workflows = root / "workflows"
            unets = comfy / "models" / "diffusion_models"
            checkpoints = comfy / "models" / "checkpoints"
            loras = comfy / "models" / "loras"
            for path in (unets, checkpoints, loras, workflows):
                path.mkdir(parents=True, exist_ok=True)
            (unets / "Krea2-red_fp8.safetensors").write_bytes(b"")
            (unets / "minimax-video.safetensors").write_bytes(b"")
            (checkpoints / "painting-xl.safetensors").write_bytes(b"")
            metadata = json.dumps({"__metadata__": {"ss_output_name": "dress", "modelspec.tags": "sailor_uniform, blue_skirt"}}).encode()
            (loras / "dress.safetensors").write_bytes(struct.pack("<Q", len(metadata)) + metadata)
            (workflows / "krea.json").write_text(json.dumps({"nodes": [
                {"id": 1, "type": "UNETLoader", "widgets_values": ["Krea2-red_fp8.safetensors"]},
                {"id": 2, "type": "KSampler"}, {"id": 3, "type": "SaveImage"},
                {"id": 4, "type": "CLIPTextEncode"},
            ]}), encoding="utf-8")
            inventory = ResourceInventory(comfy, [workflows])
            result = inventory.snapshot()
            workflow = result["workflows"][0]
            self.assertEqual("UNETLoader", workflow["model_loader"])
            self.assertEqual("krea2", workflow["model_family"])
            compatible = inventory.compatible_models(workflow["value"])
            self.assertEqual(["Krea2-red_fp8.safetensors"], [item["value"] for item in compatible])
            self.assertEqual(["sailor_uniform", "blue_skirt"], result["loras"][0]["trigger_words"])
            self.assertEqual("confirmed", result["loras"][0]["trigger_status"])


class WorkflowModelNameMatchingTests(unittest.TestCase):
    """A model living in a subfolder is named inconsistently across workflows.

    ``_models()`` reports ``value`` as the path relative to the model root
    (``Krea2/krea2_turbo.safetensors``), while a workflow's loader node may name
    the very same file by its bare filename. Exact string equality then reports
    a compatible model as incompatible, which reads to the user as "this
    workflow is broken" when nothing is.
    """

    def _inventory(self, root: pathlib.Path, workflow_nodes: list[dict]):
        comfy = root / "ComfyUI"
        workflows = root / "workflows"
        # The model sits inside a loader subfolder, which is how a real install
        # is laid out and the case the flat-file test above never covered.
        unets = comfy / "models" / "diffusion_models" / "Krea2"
        for path in (unets, workflows):
            path.mkdir(parents=True, exist_ok=True)
        (unets / "krea2_turbo_int8_convrot.safetensors").write_bytes(b"")
        (workflows / "flow.json").write_text(
            json.dumps({"nodes": workflow_nodes}), encoding="utf-8")
        return ResourceInventory(comfy, [workflows])

    @staticmethod
    def _node(model_name: str) -> list[dict]:
        return [
            {"id": 1, "type": "UNETLoader", "widgets_values": [model_name]},
            {"id": 2, "type": "KSampler"}, {"id": 3, "type": "SaveImage"},
            {"id": 4, "type": "CLIPTextEncode"},
        ]

    def test_a_bare_filename_matches_a_model_inside_a_subfolder(self):
        with tempfile.TemporaryDirectory() as temp:
            inventory = self._inventory(pathlib.Path(temp), self._node("krea2_turbo_int8_convrot.safetensors"))
            workflow = inventory.snapshot()["workflows"][0]
            compatible = inventory.compatible_models(workflow["value"])
            self.assertEqual(
                ["krea2_turbo_int8_convrot.safetensors"],
                [item["name"] for item in compatible],
                "a bare filename should resolve to the model in the subfolder",
            )

    def test_a_subfolder_prefixed_name_also_matches(self):
        with tempfile.TemporaryDirectory() as temp:
            inventory = self._inventory(pathlib.Path(temp), self._node(r"Krea2\krea2_turbo_int8_convrot.safetensors"))
            workflow = inventory.snapshot()["workflows"][0]
            compatible = inventory.compatible_models(workflow["value"])
            self.assertEqual(
                ["krea2_turbo_int8_convrot.safetensors"],
                [item["name"] for item in compatible],
                "a prefixed name should resolve to the same model",
            )

    def test_the_workflow_reports_the_bare_model_name(self):
        """``current_model`` is what the UI shows and what /api/start validates.

        Reporting it bare keeps it consistent across workflows: some loader
        nodes carry the folder, some do not, and a bare name cannot be
        mistaken for a distinct model.
        """
        with tempfile.TemporaryDirectory() as temp:
            inventory = self._inventory(pathlib.Path(temp), self._node(r"Krea2\krea2_turbo_int8_convrot.safetensors"))
            workflow = inventory.snapshot()["workflows"][0]
            self.assertEqual("krea2_turbo_int8_convrot.safetensors", workflow["current_model"])

    def test_a_model_that_is_not_installed_is_never_in_the_candidate_list(self):
        """The candidate list is a menu of installed files, never a wildcard.

        A workflow whose loader names something absent must not make that name
        selectable -- otherwise the page would offer a model that cannot run.
        """
        with tempfile.TemporaryDirectory() as temp:
            inventory = self._inventory(pathlib.Path(temp), self._node("not_installed_at_all.safetensors"))
            workflow = inventory.snapshot()["workflows"][0]
            names = {item["name"] for item in inventory.compatible_models(workflow["value"])}
            self.assertNotIn("not_installed_at_all.safetensors", names)
            self.assertIn("krea2_turbo_int8_convrot.safetensors", names)

    def test_an_unknown_family_falls_back_to_every_installed_model(self):
        """An unrecognized family must not silently empty the menu.

        ``_model_family`` returns "unknown" for anything it cannot classify.
        Filtering on that string leaves no candidates at all, which is why the
        fallback to the loader's full list matters.
        """
        with tempfile.TemporaryDirectory() as temp:
            inventory = self._inventory(pathlib.Path(temp), self._node("mystery-weights-v9.safetensors"))
            workflow = inventory.snapshot()["workflows"][0]
            self.assertEqual("unknown", workflow["model_family"])
            names = {item["name"] for item in inventory.compatible_models(workflow["value"])}
            self.assertIn("krea2_turbo_int8_convrot.safetensors", names)

    def test_every_krea2_model_is_offered_even_from_a_subfolder(self):
        """``compatible_models`` is the menu of valid choices for this workflow.

        Both Krea2 models are legitimate picks, so both must be listed -- and
        listing them by name is what lets the page show a readable label
        instead of a full relative path.
        """
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            comfy = root / "ComfyUI"
            workflows = root / "workflows"
            unets = comfy / "models" / "diffusion_models" / "Krea2"
            for path in (unets, workflows):
                path.mkdir(parents=True, exist_ok=True)
            (unets / "krea2_turbo_int8_convrot.safetensors").write_bytes(b"")
            (unets / "krea2_other_int8_convrot.safetensors").write_bytes(b"")
            (workflows / "flow.json").write_text(
                json.dumps({"nodes": self._node("krea2_turbo_int8_convrot.safetensors")}), encoding="utf-8")
            inventory = ResourceInventory(comfy, [workflows])
            workflow = inventory.snapshot()["workflows"][0]
            compatible = inventory.compatible_models(workflow["value"])
            self.assertEqual(
                ["krea2_other_int8_convrot.safetensors", "krea2_turbo_int8_convrot.safetensors"],
                sorted(item["name"] for item in compatible),
            )

    def test_a_name_that_resolves_to_an_installed_model_is_accepted(self):
        """The check ``/api/start`` performs, on the shapes that broke it.

        Before the fix the comparison was exact string equality, so a workflow
        naming ``krea2_turbo_int8_convrot.safetensors`` never matched the
        inventory row ``Krea2/krea2_turbo_int8_convrot.safetensors`` and the
        batch was refused with "模型与所选工作流不兼容" for a model the user has.
        """
        with tempfile.TemporaryDirectory() as temp:
            for written in ("krea2_turbo_int8_convrot.safetensors",
                            r"Krea2\krea2_turbo_int8_convrot.safetensors",
                            "Krea2/krea2_turbo_int8_convrot.safetensors"):
                inventory = self._inventory(pathlib.Path(temp), self._node(written))
                workflow = inventory.snapshot()["workflows"][0]
                compatible = inventory.compatible_models(workflow["value"])
                keys = {ResourceInventory.model_key(item["value"]) for item in compatible}
                self.assertIn(
                    ResourceInventory.model_key(written), keys,
                    f"{written!r} should resolve to an installed model",
                )


class WorkflowAdapterInterfaceTests(unittest.TestCase):
    def test_injects_negative_encoder_when_sampler_uses_zero_conditioning(self):
        workflow = {
            "1": {"class_type": "CLIPLoader", "inputs": {"clip_name": "clip.safetensors", "type": "krea2", "device": "default"}},
            "2": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 0], "text": "positive"}},
            "3": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["2", 0]}},
            "4": {"class_type": "UNETLoader", "inputs": {"unet_name": "model.safetensors"}},
            "5": {"class_type": "KSampler", "inputs": {"model": ["4", 0], "positive": ["2", 0], "negative": ["3", 0]}},
            "6": {"class_type": "SaveImage", "inputs": {"filename_prefix": "old"}},
        }
        config = BatchConfig("flow.json", "model.safetensors", negative_prompt="真人")
        graph = Krea2WorkflowAdapter(workflow).build("动漫角色", config, "Batch/item", task_negative="三视图")
        negative_link = graph["5"]["inputs"]["negative"]
        negative_node = graph[str(negative_link[0])]
        self.assertEqual("CLIPTextEncode", negative_node["class_type"])
        self.assertEqual(["1", 0], negative_node["inputs"]["clip"])
        self.assertEqual("三视图, 真人", negative_node["inputs"]["text"])

    def test_warns_when_negative_prompt_replaces_the_workflows_own_conditioning(self):
        """Replacing a ``ConditioningZeroOut`` negative is a semantic change.

        The author's zeroed-out negative means "no negative guidance at all"; a
        real negative prompt means the opposite. The substitution is still made
        (it is the only way to honour the request), but it must be announced --
        silently swapping it changes the picture with no visible cause.
        """
        workflow = {
            "1": {"class_type": "CLIPLoader", "inputs": {"clip_name": "clip.safetensors", "type": "krea2", "device": "default"}},
            "2": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 0], "text": "positive"}},
            "3": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["2", 0]}},
            "4": {"class_type": "UNETLoader", "inputs": {"unet_name": "model.safetensors"}},
            "5": {"class_type": "KSampler", "inputs": {"model": ["4", 0], "positive": ["2", 0], "negative": ["3", 0]}},
            "6": {"class_type": "SaveImage", "inputs": {"filename_prefix": "old"}},
        }
        report = Krea2WorkflowAdapter(workflow).capabilities(
            BatchConfig("flow.json", "model.safetensors", negative_prompt="真人")
        )

        self.assertTrue(report["ready"])
        self.assertEqual(["ConditioningZeroOut"], report["negative_prompt"]["replaces"])
        self.assertTrue(
            any("ConditioningZeroOut" in item for item in report["warnings"]),
            f"the displaced node type must be named in the warnings: {report['warnings']}",
        )

    def test_a_real_negative_encoder_is_not_reported_as_replaced(self):
        """A workflow that already owns a negative encoder is untouched."""
        workflow = {
            "1": {"class_type": "CLIPLoader", "inputs": {"clip_name": "clip.safetensors", "type": "krea2", "device": "default"}},
            "2": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 0], "text": "positive"}},
            "3": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 0], "text": "old negative"}},
            "4": {"class_type": "UNETLoader", "inputs": {"unet_name": "model.safetensors"}},
            "5": {"class_type": "KSampler", "inputs": {"model": ["4", 0], "positive": ["2", 0], "negative": ["3", 0]}},
            "6": {"class_type": "SaveImage", "inputs": {"filename_prefix": "old"}},
        }
        report = Krea2WorkflowAdapter(workflow).capabilities(
            BatchConfig("flow.json", "model.safetensors", negative_prompt="真人")
        )

        self.assertTrue(report["ready"])
        self.assertEqual([], report["negative_prompt"]["replaces"])
        self.assertEqual([], report["warnings"])

    def test_negative_replacement_is_reported_for_ui_graphs_too(self):
        """UI graphs keep inputs as a list and links in a separate table."""
        workflow = {
            "nodes": [
                {"id": 1, "type": "UNETLoader", "mode": 0, "widgets_values": ["model.safetensors", "default"]},
                {"id": 2, "type": "CLIPLoader", "mode": 0, "widgets_values": ["clip.safetensors", "krea2", "default"]},
                {"id": 3, "type": "CLIPTextEncode", "mode": 0, "widgets_values": ["positive"],
                 "inputs": [{"name": "clip", "link": 1, "widget": {"name": "text"}}]},
                {"id": 4, "type": "ConditioningZeroOut", "mode": 0, "inputs": [{"name": "conditioning", "link": 2}]},
                {"id": 5, "type": "KSampler", "mode": 0, "widgets_values": [1, "randomize", 8, 1.0, "euler", "simple", 1.0],
                 "inputs": [{"name": "model", "link": 3}, {"name": "positive", "link": 2},
                            {"name": "negative", "link": 4}]},
                {"id": 6, "type": "SaveImage", "mode": 0, "widgets_values": ["old"],
                 "inputs": [{"name": "images", "link": 5}]},
            ],
            "links": [
                [1, 2, 0, 3, 0, "CLIP"],
                [2, 3, 0, 5, 1, "CONDITIONING"],
                [3, 1, 0, 5, 0, "MODEL"],
                [4, 4, 0, 5, 2, "CONDITIONING"],
                [5, 5, 0, 6, 0, "LATENT"],
            ],
        }
        report = Krea2WorkflowAdapter(workflow).capabilities(
            BatchConfig("flow.json", "model.safetensors", negative_prompt="真人")
        )

        self.assertTrue(report["ready"])
        self.assertEqual(["ConditioningZeroOut"], report["negative_prompt"]["replaces"])
        self.assertTrue(any("ConditioningZeroOut" in item for item in report["warnings"]))

    def test_an_empty_negative_is_announced_when_no_clip_source_exists(self):
        """方案C(docs/15 §6.1): no CLIP source anywhere no longer blocks.

        The run keeps the author's own wiring (an empty negative -- exactly
        what ConditioningZeroOut means), and the preflight says plainly that
        the filled text will not apply, instead of refusing to start.
        """
        workflow = {
            "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "model.safetensors"}},
            "2": {"class_type": "PrimitiveStringMultiline", "inputs": {"value": "positive"}},
            "3": {"class_type": "ConditioningZeroOut", "inputs": {}},
            "4": {"class_type": "KSampler", "inputs": {"model": ["1", 0], "positive": ["2", 0], "negative": ["3", 0]}},
            "5": {"class_type": "SaveImage", "inputs": {"filename_prefix": "old"}},
        }
        config = BatchConfig("flow.json", "model.safetensors", negative_prompt="真人")
        report = Krea2WorkflowAdapter(workflow).capabilities(config)
        self.assertTrue(report["ready"])
        self.assertTrue(
            any("负面词不会生效" in warning for warning in report["warnings"]),
            f"the empty-negative notice must be in the warnings: {report['warnings']}",
        )
        self.assertFalse(
            any("将改为直接负面引导" in warning for warning in report["warnings"]),
            "the surrogate warning must not claim a bypass that cannot happen",
        )
        self.assertEqual("空负面（填了但不生效）", report["negative_prompt"]["mode"])
        self.assertTrue(report["negative_prompt"]["empty_negative"])

    def test_the_negative_encoder_borrows_clip_from_any_graph_source(self):
        """方案A(docs/15 §6.1): the CLIP search is graph-wide, and accounted.

        The positive side is not a real encoder, so the old first-hop borrow
        failed and the whole batch was refused. Now the rebuilt encoder takes
        the checkpoint loader's CLIP output -- and the audit records which
        fallback source was used.
        """
        workflow = {
            "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "painting.safetensors"}},
            "2": {"class_type": "PrimitiveStringMultiline", "inputs": {"value": "old"}},
            "3": {"class_type": "ConditioningZeroOut", "inputs": {}},
            "4": {"class_type": "KSampler", "inputs": {"model": ["1", 0], "positive": ["2", 0], "negative": ["3", 0]}},
            "5": {"class_type": "SaveImage", "inputs": {"filename_prefix": "old"}},
        }
        adapter = Krea2WorkflowAdapter(workflow)
        graph = adapter.build(
            "one subject", BatchConfig("flow.json", "painting.safetensors", negative_prompt="真人"), "Batch/item"
        )
        negative_link = graph["4"]["inputs"]["negative"]
        negative_node = graph[str(negative_link[0])]
        self.assertEqual("CLIPTextEncode", negative_node["class_type"])
        self.assertEqual(["1", 1], negative_node["inputs"]["clip"])
        fallback = adapter.audit()["negative_clip_fallback"]
        self.assertEqual(["4"], list(fallback), "only the borrowing sampler is listed")
        self.assertIn("CheckpointLoaderSimple 1", fallback["4"])

    def test_a_negative_with_no_clip_source_keeps_the_authors_wiring(self):
        """方案C(docs/15 §6.1): the author's empty negative stays connected.

        Nothing is rebuilt or displaced; the sampler keeps its zeroed-out
        negative and the audit says the filled text did not apply.
        """
        workflow = {
            "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "model.safetensors"}},
            "2": {"class_type": "PrimitiveStringMultiline", "inputs": {"value": "old"}},
            "3": {"class_type": "ConditioningZeroOut", "inputs": {}},
            "4": {"class_type": "KSampler", "inputs": {"model": ["1", 0], "positive": ["2", 0], "negative": ["3", 0]}},
            "5": {"class_type": "SaveImage", "inputs": {"filename_prefix": "old"}},
        }
        adapter = Krea2WorkflowAdapter(workflow)
        graph = adapter.build(
            "one subject", BatchConfig("flow.json", "model.safetensors", negative_prompt="真人"), "Batch/item"
        )
        self.assertEqual(["3", 0], graph["4"]["inputs"]["negative"], "the author's wiring must stay")
        audit = adapter.audit()
        self.assertEqual({"4": "ConditioningZeroOut"}, audit["negative_skipped"])
        self.assertEqual({}, audit["negative_clip_fallback"])
        self.assertEqual([], audit["orphaned_conditioning"], "nothing was displaced")

    def test_applies_selected_aspect_ratio_and_size_to_resolution_selector(self):
        workflow = {
            "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "old.safetensors"}},
            "2": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": "old"}},
            "3": {"class_type": "KSampler", "inputs": {"model": ["1", 0], "positive": ["2", 0]}},
            "4": {"class_type": "ResolutionSelector", "inputs": {"aspect_ratio": "1:1 (Square)", "megapixels": 1.2}},
            "5": {"class_type": "SaveImage", "inputs": {"filename_prefix": "old"}},
        }
        config = BatchConfig("flow.json", "painting.safetensors", aspect_ratio="9:16 (Portrait)", megapixels=0.9)
        graph = Krea2WorkflowAdapter(workflow).build("one subject", config, "Batch/item")
        self.assertEqual("9:16 (Portrait)", graph["4"]["inputs"]["aspect_ratio"])
        self.assertEqual(0.9, graph["4"]["inputs"]["megapixels"])

    def test_a_single_negative_encoder_is_shared_by_every_sampler(self):
        """Two samplers needing a negative encoder must not get two copies.

        Each pass used to be handed its own identical node, which made the graph
        harder to read and left no way to tell which pass a later edit hit.
        """
        workflow = {
            "1": {"class_type": "CLIPLoader", "inputs": {"clip_name": "clip.safetensors", "type": "krea2", "device": "default"}},
            "2": {"class_type": "UNETLoader", "inputs": {"unet_name": "model.safetensors"}},
            "3": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 0], "text": "positive"}},
            "4": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["3", 0]}},
            "5": {"class_type": "KSampler", "inputs": {"model": ["2", 0], "positive": ["3", 0], "negative": ["4", 0]}},
            "6": {"class_type": "KSamplerAdvanced", "inputs": {"model": ["2", 0], "positive": ["3", 0], "negative": ["4", 0]}},
            "7": {"class_type": "SaveImage", "inputs": {"filename_prefix": "old"}},
        }
        graph = Krea2WorkflowAdapter(workflow).build(
            "one subject", BatchConfig("flow.json", "model.safetensors", negative_prompt="真人"), "Batch/item"
        )

        injected = [
            node_id for node_id, node in graph.items()
            if node.get("class_type") == "CLIPTextEncode" and node_id not in {"3"}
        ]
        self.assertEqual(1, len(injected), f"expected one shared negative node, got {injected}")
        shared = injected[0]
        self.assertEqual([shared, 0], graph["5"]["inputs"]["negative"])
        self.assertEqual([shared, 0], graph["6"]["inputs"]["negative"])

    def test_a_displaced_conditioning_node_is_reported_but_kept(self):
        """The replaced node stays in the graph, and the audit says so.

        Deleting it is not safe -- a rgthree "Fast Groups Bypasser" can
        re-activate that branch on a later pass -- but a workflow author editing
        the file afterwards deserves to know it no longer does anything.
        """
        workflow = {
            "1": {"class_type": "CLIPLoader", "inputs": {"clip_name": "clip.safetensors", "type": "krea2", "device": "default"}},
            "2": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 0], "text": "positive"}},
            "3": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["2", 0]}},
            "4": {"class_type": "UNETLoader", "inputs": {"unet_name": "model.safetensors"}},
            "5": {"class_type": "KSampler", "inputs": {"model": ["4", 0], "positive": ["2", 0], "negative": ["3", 0]}},
            "6": {"class_type": "SaveImage", "inputs": {"filename_prefix": "old"}},
        }
        adapter = Krea2WorkflowAdapter(workflow)
        graph = adapter.build(
            "one subject", BatchConfig("flow.json", "model.safetensors", negative_prompt="真人"), "Batch/item"
        )

        self.assertIn("3", graph, "the displaced node must not be deleted")
        self.assertEqual("ConditioningZeroOut", graph["3"]["class_type"])
        self.assertEqual(
            [{"node_id": "3", "node_type": "ConditioningZeroOut"}],
            adapter.audit()["orphaned_conditioning"],
        )

    def test_nothing_is_reported_when_the_negative_encoder_is_the_workflows_own(self):
        workflow = {
            "1": {"class_type": "CLIPLoader", "inputs": {"clip_name": "clip.safetensors", "type": "krea2", "device": "default"}},
            "2": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 0], "text": "positive"}},
            "3": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 0], "text": "old negative"}},
            "4": {"class_type": "UNETLoader", "inputs": {"unet_name": "model.safetensors"}},
            "5": {"class_type": "KSampler", "inputs": {"model": ["4", 0], "positive": ["2", 0], "negative": ["3", 0]}},
            "6": {"class_type": "SaveImage", "inputs": {"filename_prefix": "old"}},
        }
        adapter = Krea2WorkflowAdapter(workflow)
        adapter.build(
            "one subject", BatchConfig("flow.json", "model.safetensors", negative_prompt="真人"), "Batch/item"
        )
        self.assertEqual([], adapter.audit()["orphaned_conditioning"])

    def test_applies_landscape_size_to_active_empty_latent_image(self):
        workflow = {
            "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "old.safetensors"}},
            "2": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": "positive"}},
            "3": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": "negative"}},
            "4": {"class_type": "EmptyLatentImage", "inputs": {"width": 768, "height": 1280, "batch_size": 1}},
            "5": {"class_type": "KSampler", "inputs": {"model": ["1", 0], "positive": ["2", 0], "negative": ["3", 0], "latent_image": ["4", 0]}},
            "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
            "7": {"class_type": "SaveImage", "inputs": {"images": ["6", 0], "filename_prefix": "old"}},
        }
        config = BatchConfig(
            "flow.json",
            "painting.safetensors",
            aspect_ratio="16:9 (Widescreen)",
            megapixels=0.9,
        )

        graph = Krea2WorkflowAdapter(workflow).build("one subject", config, "Batch/item")
        report = Krea2WorkflowAdapter(workflow).capabilities(config)

        self.assertEqual(1280, graph["4"]["inputs"]["width"])
        self.assertEqual(720, graph["4"]["inputs"]["height"])
        self.assertEqual({"width": 1280, "height": 720}, report["dimensions"])

    def test_preflight_identifies_actual_workflow_and_key_nodes(self):
        workflow = {
            "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "old.safetensors"}},
            "2": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": "positive"}},
            "3": {"class_type": "EmptyLatentImage", "inputs": {"width": 1024, "height": 1024, "batch_size": 1}},
            "4": {"class_type": "KSampler", "inputs": {"model": ["1", 0], "positive": ["2", 0], "latent_image": ["3", 0]}},
            "5": {"class_type": "SaveImage", "inputs": {"filename_prefix": "old"}},
        }
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "高清工作流.json"
            path.write_text(json.dumps(workflow), encoding="utf-8")
            config = BatchConfig(str(path), "painting.safetensors")

            report = Krea2WorkflowAdapter.from_path(path).capabilities(config)

        self.assertEqual("高清工作流.json", report["workflow"]["name"])
        self.assertEqual(str(path.resolve()), report["workflow"]["path"])
        # The published fingerprint is the structural one -- the same value the
        # audit and the saved replacement rules use. Publishing the file hash
        # under this name made the page show two different 工作流指纹 values for
        # one workflow on one screen.
        self.assertEqual(structural_fingerprint(workflow), report["workflow"]["fingerprint"])
        self.assertEqual(12, len(report["workflow"]["file_hash"]))
        self.assertNotEqual(report["workflow"]["fingerprint"], report["workflow"]["file_hash"])
        self.assertEqual(["2"], report["nodes"]["prompt"])
        self.assertEqual(["3"], report["nodes"]["dimensions"])
        self.assertEqual(["5"], report["nodes"]["save"])

    def test_writes_batch_source_image_into_load_image_node(self):
        workflow = {
            "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "old.safetensors"}},
            "2": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": "positive"}},
            "3": {"class_type": "LoadImage", "inputs": {"image": "old.png", "upload": "image"}},
            "4": {"class_type": "VAEEncode", "inputs": {"pixels": ["3", 0], "vae": ["1", 2]}},
            "5": {"class_type": "KSampler", "inputs": {"model": ["1", 0], "positive": ["2", 0], "latent_image": ["4", 0]}},
            "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
            "7": {"class_type": "SaveImage", "inputs": {"images": ["6", 0], "filename_prefix": "old"}},
        }
        config = BatchConfig("image-flow.json", "painting.safetensors")

        graph = Krea2WorkflowAdapter(workflow).build(
            "preserve the source image",
            config,
            "Batch/image-001",
            source_image="ComfyBatch-V2/imports/run/source.png",
        )
        report = Krea2WorkflowAdapter(workflow).capabilities(config, source_image="source.png")

        self.assertEqual("ComfyBatch-V2/imports/run/source.png", graph["3"]["inputs"]["image"])
        self.assertTrue(report["input_image"]["supported"])
        self.assertEqual(["3"], report["nodes"]["image_input"])

    def test_applies_style_locally_when_workflow_has_no_style_node(self):
        workflow = {
            "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "old.safetensors"}},
            "2": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": "old positive"}},
            "3": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": "old negative"}},
            "4": {"class_type": "EmptyLatentImage", "inputs": {"width": 1024, "height": 1024, "batch_size": 1}},
            "5": {"class_type": "KSampler", "inputs": {"model": ["1", 0], "positive": ["2", 0], "negative": ["3", 0], "latent_image": ["4", 0]}},
            "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
            "7": {"class_type": "SaveImage", "inputs": {"images": ["6", 0], "filename_prefix": "old"}},
        }
        config = BatchConfig(
            workflow_path="flow.json",
            model="painting.safetensors",
            styles=[
                {"catalog": "anime", "name": "Cel", "prompt": "{prompt}, crisp cel shading", "negative_prompt": "photorealistic"},
                {"catalog": "light", "name": "Glow", "prompt": "prismatic rim light", "negative_prompt": "flat lighting"},
            ],
        )
        compiled = PromptCompiler.compile("one subject", config)
        graph = Krea2WorkflowAdapter(workflow).build(compiled, config, "Batch/item")
        self.assertIn("one subject", graph["2"]["inputs"]["text"])
        self.assertIn("crisp cel shading", graph["2"]["inputs"]["text"])
        self.assertIn("prismatic rim light", graph["2"]["inputs"]["text"])
        self.assertIn("photorealistic", graph["3"]["inputs"]["text"])
        self.assertIn("flat lighting", graph["3"]["inputs"]["text"])
        report = Krea2WorkflowAdapter(workflow).capabilities(config)
        self.assertTrue(report["ready"])
        self.assertEqual("本地提示词编译", report["style"]["mode"])

    def test_injects_full_lora_chain_into_checkpoint_workflow(self):
        workflow = {
            "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "old.safetensors"}},
            "2": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": "positive"}},
            "3": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": "negative"}},
            "4": {"class_type": "EmptyLatentImage", "inputs": {"width": 1024, "height": 1024, "batch_size": 1}},
            "5": {"class_type": "KSampler", "inputs": {"model": ["1", 0], "positive": ["2", 0], "negative": ["3", 0], "latent_image": ["4", 0]}},
            "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
            "7": {"class_type": "SaveImage", "inputs": {"images": ["6", 0], "filename_prefix": "old"}},
        }
        config = BatchConfig("flow.json", "new.safetensors", loras=[{"name": "dress.safetensors", "strength": 0.65}])
        graph = Krea2WorkflowAdapter(workflow).build("one subject", config, "Batch/item")
        self.assertEqual("new.safetensors", graph["1"]["inputs"]["ckpt_name"])
        self.assertEqual("LoraLoader", graph["1000"]["class_type"])
        self.assertEqual(["1000", 0], graph["5"]["inputs"]["model"])
        self.assertEqual(["1000", 1], graph["2"]["inputs"]["clip"])
        self.assertEqual(["1000", 1], graph["3"]["inputs"]["clip"])
        self.assertEqual(0.65, graph["1000"]["inputs"]["strength_clip"])

    def test_reports_missing_workflow_capabilities_before_build(self):
        workflow = {"1": {"class_type": "SaveImage", "inputs": {"filename_prefix": "old"}}}
        config = BatchConfig("flow.json", "model.safetensors", loras=[{"name": "x.safetensors"}])
        report = Krea2WorkflowAdapter(workflow).capabilities(config)
        self.assertFalse(report["ready"])
        self.assertTrue(any("模型加载器" in error for error in report["errors"]))
        with self.assertRaisesRegex(ValueError, "模型加载器"):
            Krea2WorkflowAdapter(workflow).build("prompt", config, "Batch/item")

    def test_builds_prompt_graph_with_selected_model_style_and_lora(self):
        workflow = {
            "nodes": [
                {"id": 27, "type": "SaveImage", "inputs": [{"name": "images", "link": 8}], "widgets_values": ["old"]},
                {"id": 28, "type": "KSampler", "inputs": [
                    {"name": "model", "link": 1}, {"name": "positive", "link": 2}, {"name": "negative", "link": 3}, {"name": "latent_image", "link": 4}
                ], "widgets_values": [1, "fixed", 8, 1, "euler", "simple", 1]},
                {"id": 29, "type": "EmptyLatentImage", "inputs": [{"name": "width", "link": 11}, {"name": "height", "link": 12}], "widgets_values": [768, 1280, 1]},
                {"id": 30, "type": "VAEDecode", "inputs": [{"name": "samples", "link": 7}, {"name": "vae", "link": 6}]},
                {"id": 31, "type": "ConditioningZeroOut", "inputs": [{"name": "conditioning", "link": 9}]},
                {"id": 34, "type": "VAELoader", "widgets_values": ["qwen_image_vae.safetensors"]},
                {"id": 36, "type": "CLIPLoader", "widgets_values": ["qwen3vl_4b_fp8_scaled.safetensors", "krea2", "defaultdefault"]},
                {"id": 37, "type": "UNETLoader", "widgets_values": ["old.safetensors", "defaultdefault"]},
                {"id": 50, "type": "PrimitiveStringMultiline", "widgets_values": ["old prompt"]},
                {"id": 52, "type": "easy stylesSelector", "inputs": [{"name": "positive", "link": 10}], "widgets_values": ["old_styles", "Old"]},
                {"id": 53, "type": "CLIPTextEncode", "inputs": [{"name": "text", "link": 13}, {"name": "clip", "link": 5}], "widgets_values": [""]},
                {"id": 55, "type": "ResolutionSelector", "widgets_values": ["9:16 (Portrait)", 1.2, 32]},
            ],
            "links": [
                [1, 37, 0, 28, 0, "MODEL"], [2, 53, 0, 28, 1, "CONDITIONING"], [3, 31, 0, 28, 2, "CONDITIONING"],
                [4, 29, 0, 28, 3, "LATENT"], [5, 36, 0, 53, 1, "CLIP"], [6, 34, 0, 30, 1, "VAE"],
                [7, 28, 0, 30, 0, "LATENT"], [8, 30, 0, 27, 0, "IMAGE"], [9, 53, 0, 31, 0, "CONDITIONING"],
                [10, 50, 0, 52, 0, "STRING"], [11, 55, 0, 29, 0, "INT"], [12, 55, 1, 29, 1, "INT"], [13, 52, 0, 53, 0, "STRING"]
            ]
        }
        config = BatchConfig(
            workflow_path="flow.json",
            model="new.safetensors",
            style_library="anime",
            style_name="Anime Style",
            loras=[{"name": "dress.safetensors", "strength": 0.7}],
            aspect_ratio="16:9 (Widescreen)",
            output_dir="D:/out",
        )
        graph = Krea2WorkflowAdapter(workflow).build("one prompt", config, "Batch/item-001")
        self.assertEqual("new.safetensors", graph["37"]["inputs"]["unet_name"])
        self.assertEqual("anime", graph["52"]["inputs"]["styles"])
        self.assertEqual("Anime Style", graph["52"]["inputs"]["select_styles"])
        self.assertEqual("LoraLoaderModelOnly", graph["1000"]["class_type"])
        self.assertEqual(["1000", 0], graph["28"]["inputs"]["model"])
        self.assertEqual("16:9 (Widescreen)", graph["55"]["inputs"]["aspect_ratio"])

    def test_ui_conversion_prunes_muted_branches_and_reconnects_bypassed_model_loader(self):
        workflow = {
            "nodes": [
                {"id": 1, "type": "UNETLoader", "mode": 0, "widgets_values": ["old.safetensors", "default"]},
                {"id": 2, "type": "LoraLoaderModelOnly", "mode": 4, "inputs": [{"name": "model", "link": 1}]},
                {"id": 3, "type": "CLIPLoader", "mode": 0, "widgets_values": ["clip.safetensors", "krea2", "default"]},
                {"id": 4, "type": "CLIPTextEncode", "mode": 0, "inputs": [{"name": "clip", "link": 2}]},
                {"id": 5, "type": "ConditioningZeroOut", "mode": 0, "inputs": [{"name": "conditioning", "link": 3}]},
                {"id": 6, "type": "EmptyLatentImage", "mode": 0, "widgets_values": [768, 1280, 1]},
                {"id": 7, "type": "KSampler", "mode": 0, "inputs": [
                    {"name": "model", "link": 4}, {"name": "positive", "link": 5},
                    {"name": "negative", "link": 6}, {"name": "latent_image", "link": 7},
                ], "widgets_values": [1, "fixed", 8, 1, "euler", "simple", 1]},
                {"id": 8, "type": "VAELoader", "mode": 0, "widgets_values": ["vae.safetensors"]},
                {"id": 9, "type": "VAEDecode", "mode": 0, "inputs": [{"name": "samples", "link": 8}, {"name": "vae", "link": 9}]},
                {"id": 10, "type": "SaveImage", "mode": 0, "inputs": [{"name": "images", "link": 10}]},
                {"id": 20, "type": "KSamplerAdvanced", "mode": 4},
                {"id": 21, "type": "VAEDecode", "mode": 4, "inputs": [{"name": "samples", "link": 20}, {"name": "vae", "link": 9}]},
                {"id": 22, "type": "SaveImage", "mode": 4, "inputs": [{"name": "images", "link": 21}]},
                {"id": 23, "type": "ColorMatchV2", "mode": 4},
                {"id": 24, "type": "SaveImage", "mode": 4, "inputs": [{"name": "images", "link": 23}]},
            ],
            "links": [
                [1, 1, 0, 2, 0, "MODEL"], [2, 3, 0, 4, 0, "CLIP"],
                [3, 4, 0, 5, 0, "CONDITIONING"], [4, 2, 0, 7, 0, "MODEL"],
                [5, 4, 0, 7, 1, "CONDITIONING"], [6, 5, 0, 7, 2, "CONDITIONING"],
                [7, 6, 0, 7, 3, "LATENT"], [8, 7, 0, 9, 0, "LATENT"],
                [9, 8, 0, 9, 1, "VAE"], [10, 9, 0, 10, 0, "IMAGE"],
                [20, 20, 0, 21, 0, "LATENT"], [21, 21, 0, 22, 0, "IMAGE"],
                [23, 23, 0, 24, 0, "IMAGE"],
            ],
        }
        config = BatchConfig(
            "flow.json",
            "new.safetensors",
            loras=[{"name": "detail.safetensors", "strength": 0.5}],
            aspect_ratio="16:9 (Widescreen)",
            megapixels=0.9,
        )
        graph = Krea2WorkflowAdapter(workflow).build("one subject", config, "Batch/item")
        dangling = [
            (node_id, name, value[0])
            for node_id, node in graph.items()
            for name, value in node.get("inputs", {}).items()
            if isinstance(value, list) and len(value) == 2 and str(value[0]) not in graph
        ]
        self.assertEqual([], dangling)
        self.assertEqual({"10"}, {node_id for node_id, node in graph.items() if node["class_type"] == "SaveImage"})
        self.assertEqual(1280, graph["6"]["inputs"]["width"])
        self.assertEqual(720, graph["6"]["inputs"]["height"])
        self.assertEqual(["1000", 0], graph["7"]["inputs"]["model"])
        self.assertEqual(["1", 0], graph["1000"]["inputs"]["model"])

    def test_builds_ordered_cross_catalog_style_combination(self):
        workflow = {
            "1": {"class_type": "PrimitiveStringMultiline", "inputs": {"value": "old"}},
            "2": {"class_type": "easy stylesSelector", "inputs": {"styles": "old", "select_styles": "Old", "positive": ["1", 0]}},
            "3": {"class_type": "UNETLoader", "inputs": {"unet_name": "Krea2-old.safetensors"}},
            "4": {"class_type": "KSampler", "inputs": {"model": ["3", 0]}},
            "5": {"class_type": "ResolutionSelector", "inputs": {"aspect_ratio": "1:1"}},
            "6": {"class_type": "SaveImage", "inputs": {"filename_prefix": "old"}},
        }
        config = BatchConfig(
            workflow_path="flow.json",
            model="Krea2-red.safetensors",
            style_library="",
            style_name="",
            styles=[
                {"catalog": "anime", "name": "Classic Cel", "prompt": "{prompt}, classic cel animation"},
                {"catalog": "lighting", "name": "Prismatic Glow", "prompt": "prismatic glow"},
            ],
        )
        compiled = PromptCompiler.compile("one subject", config)
        graph = Krea2WorkflowAdapter(workflow).build(compiled, config, "Batch/item")
        self.assertIn("classic cel animation", graph["1"]["inputs"]["value"])
        self.assertIn("prismatic glow", graph["1"]["inputs"]["value"])

    def test_selects_two_pass_output_branch_instead_of_saved_active_branch(self):
        workflow = {
            "groups": [{"title": "二采出图", "bounding": [900, 0, 500, 500]}],
            "nodes": [
                {"id": 1, "type": "UNETLoader", "mode": 0, "pos": [0, 0], "widgets_values": ["old.safetensors", "default"]},
                {"id": 2, "type": "CLIPLoader", "mode": 0, "pos": [0, 50], "widgets_values": ["clip.safetensors", "krea2", "default"]},
                {"id": 3, "type": "CLIPTextEncode", "mode": 0, "pos": [100, 50], "inputs": [{"name": "clip", "link": 1}], "widgets_values": ["old"]},
                {"id": 4, "type": "EmptyLatentImage", "mode": 0, "pos": [100, 100], "widgets_values": [768, 1280, 1]},
                {"id": 5, "type": "VAELoader", "mode": 0, "pos": [0, 100], "widgets_values": ["vae.safetensors"]},
                {"id": 6, "type": "KSampler", "mode": 0, "pos": [300, 100], "inputs": [{"name": "model", "link": 2}, {"name": "positive", "link": 3}, {"name": "negative", "link": 3}, {"name": "latent_image", "link": 4}], "widgets_values": [1, "fixed", 8, 1, "euler", "simple", 1]},
                {"id": 7, "type": "VAEDecode", "mode": 0, "pos": [500, 100], "inputs": [{"name": "samples", "link": 5}, {"name": "vae", "link": 6}]},
                {"id": 8, "type": "SaveImage", "mode": 0, "pos": [700, 100], "inputs": [{"name": "images", "link": 7}], "widgets_values": ["initial"]},
                {"id": 20, "type": "KSamplerAdvanced", "mode": 4, "pos": [950, 100], "inputs": [{"name": "model", "link": 2}, {"name": "positive", "link": 3}, {"name": "negative", "link": 3}, {"name": "latent_image", "link": 4}]},
                {"id": 21, "type": "LatentUpscaleBy", "mode": 4, "pos": [1050, 100], "inputs": [{"name": "samples", "link": 20}]},
                {"id": 22, "type": "KSamplerAdvanced", "mode": 4, "pos": [1150, 100], "inputs": [{"name": "model", "link": 2}, {"name": "positive", "link": 3}, {"name": "negative", "link": 3}, {"name": "latent_image", "link": 21}]},
                {"id": 23, "type": "VAEDecode", "mode": 4, "pos": [1200, 200], "inputs": [{"name": "samples", "link": 22}, {"name": "vae", "link": 6}]},
                {"id": 24, "type": "SaveImage", "mode": 4, "pos": [1300, 300], "inputs": [{"name": "images", "link": 23}], "widgets_values": ["second"]},
            ],
            "links": [
                [1, 2, 0, 3, 0, "CLIP"], [2, 1, 0, 6, 0, "MODEL"], [3, 3, 0, 6, 1, "CONDITIONING"], [4, 4, 0, 6, 3, "LATENT"],
                [5, 6, 0, 7, 0, "LATENT"], [6, 5, 0, 7, 1, "VAE"], [7, 7, 0, 8, 0, "IMAGE"],
                [20, 20, 0, 21, 0, "LATENT"], [21, 21, 0, 22, 3, "LATENT"], [22, 22, 0, 23, 0, "LATENT"], [23, 23, 0, 24, 0, "IMAGE"],
            ],
        }
        adapter = Krea2WorkflowAdapter(workflow)
        second = next(item for item in adapter.workflow_variants() if item["save_node_id"] == "24")
        self.assertEqual(2, second["sampler_count"])
        self.assertEqual(1, second["upscale_count"])
        config = BatchConfig("flow.json", "new.safetensors", workflow_variant=second["id"])
        graph = adapter.build("one subject", config, "Batch/second")
        self.assertEqual({"24"}, {node_id for node_id, node in graph.items() if node["class_type"] == "SaveImage"})
        self.assertEqual(2, sum(node["class_type"] == "KSamplerAdvanced" for node in graph.values()))
        self.assertIn("LatentUpscaleBy", {node["class_type"] for node in graph.values()})

    def test_native_style_combiner_injects_one_workflow_node_per_selected_style(self):
        workflow = {
            "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "old.safetensors"}},
            "2": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": "old positive"}},
            "3": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": "old negative"}},
            "4": {"class_type": "EmptyLatentImage", "inputs": {"width": 1024, "height": 1024, "batch_size": 1}},
            "5": {"class_type": "KSampler", "inputs": {"model": ["1", 0], "positive": ["2", 0], "negative": ["3", 0], "latent_image": ["4", 0]}},
            "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
            "7": {"class_type": "SaveImage", "inputs": {"images": ["6", 0], "filename_prefix": "old"}},
        }
        config = BatchConfig(
            "flow.json", "model.safetensors", style_application="native",
            styles=[{"catalog": "anime", "name": "Anime"}, {"catalog": "ink", "name": "Manga Ink"}],
        )
        compiled = PromptCompiler.compile("one subject", config)
        self.assertEqual("one subject", compiled)
        graph = Krea2WorkflowAdapter(workflow).build(compiled, config, "Batch/native")
        style_nodes = [(node_id, node) for node_id, node in graph.items() if node["class_type"] == "easy stylesSelector"]
        self.assertEqual(2, len(style_nodes))
        last_id = style_nodes[-1][0]
        self.assertEqual([last_id, 0], graph["2"]["inputs"]["text"])
        self.assertEqual("Manga Ink", style_nodes[-1][1]["inputs"]["select_styles"])

    def test_two_pass_ui_conversion_maps_advanced_sampler_widgets_and_keeps_optional_lora_bypassed(self):
        sampler_inputs = [
            {"name": "model", "link": 2}, {"name": "positive", "link": 3},
            {"name": "negative", "link": 3}, {"name": "latent_image", "link": 4},
            {"name": "add_noise", "link": None, "widget": {"name": "add_noise"}},
            {"name": "noise_seed", "link": 8, "widget": {"name": "noise_seed"}},
            {"name": "steps", "link": None, "widget": {"name": "steps"}},
            {"name": "cfg", "link": None, "widget": {"name": "cfg"}},
            {"name": "sampler_name", "link": None, "widget": {"name": "sampler_name"}},
            {"name": "scheduler", "link": None, "widget": {"name": "scheduler"}},
            {"name": "start_at_step", "link": None, "widget": {"name": "start_at_step"}},
            {"name": "end_at_step", "link": None, "widget": {"name": "end_at_step"}},
            {"name": "return_with_leftover_noise", "link": None, "widget": {"name": "return_with_leftover_noise"}},
        ]
        workflow = {
            "nodes": [
                {"id": 1, "type": "UNETLoader", "mode": 0, "widgets_values": ["old.safetensors", "default"]},
                {"id": 9, "type": "LoraLoaderModelOnly", "mode": 4, "inputs": [{"name": "model", "link": 1}, {"name": "lora_name", "link": None, "widget": {"name": "lora_name"}}, {"name": "strength_model", "link": None, "widget": {"name": "strength_model"}}], "widgets_values": ["missing.safetensors", 0.8]},
                {"id": 2, "type": "CLIPLoader", "mode": 0, "widgets_values": ["clip.safetensors", "krea2", "default"]},
                {"id": 3, "type": "CLIPTextEncode", "mode": 0, "inputs": [{"name": "clip", "link": 10}], "widgets_values": ["old"]},
                {"id": 4, "type": "EmptyLatentImage", "mode": 0, "widgets_values": [768, 1280, 1]},
                {"id": 8, "type": "Seed (rgthree)", "mode": 0, "widgets_values": [999, "", "", ""]},
                {"id": 5, "type": "KSamplerAdvanced", "mode": 4, "inputs": sampler_inputs, "widgets_values": ["enable", 123, "randomize", 10, 1, "euler", "simple", 4, 999, "disable"]},
                {"id": 6, "type": "VAEDecode", "mode": 4, "inputs": [{"name": "samples", "link": 5}, {"name": "vae", "link": 11}]},
                {"id": 7, "type": "SaveImage", "mode": 4, "inputs": [{"name": "images", "link": 6}], "widgets_values": ["out"]},
            ],
            "links": [
                [1, 1, 0, 9, 0, "MODEL"], [2, 9, 0, 5, 0, "MODEL"], [3, 3, 0, 5, 1, "CONDITIONING"],
                [4, 4, 0, 5, 3, "LATENT"], [5, 5, 0, 6, 0, "LATENT"], [6, 6, 0, 7, 0, "IMAGE"],
                [8, 8, 0, 5, 5, "INT"], [10, 2, 0, 3, 0, "CLIP"], [11, 2, 0, 6, 1, "VAE"],
            ],
        }
        adapter = Krea2WorkflowAdapter(workflow)
        variant = next(item for item in adapter.workflow_variants() if item["save_node_id"] == "7")
        graph = adapter.build("one subject", BatchConfig("flow.json", "new.safetensors", workflow_variant=variant["id"]), "Batch/two-pass")
        self.assertNotIn("9", graph)
        self.assertEqual(["1", 0], graph["5"]["inputs"]["model"])
        self.assertEqual(10, graph["5"]["inputs"]["steps"])
        self.assertEqual(1.0, graph["5"]["inputs"]["cfg"])
        self.assertEqual("euler", graph["5"]["inputs"]["sampler_name"])
        self.assertEqual("simple", graph["5"]["inputs"]["scheduler"])
        self.assertEqual(4, graph["5"]["inputs"]["start_at_step"])
        self.assertEqual(999, graph["5"]["inputs"]["end_at_step"])
        self.assertEqual("disable", graph["5"]["inputs"]["return_with_leftover_noise"])
        self.assertLessEqual(graph["8"]["inputs"]["seed"], 1125899906842624)


class NativeStyleInjectionTests(unittest.TestCase):
    """docs/15 §6.2 方案A：白名单式风格注入。

    锚点契约：采样器 positive 直连 CLIPTextEncode（组合器链输出汇回其 text 口）。
    锚点不满足时不再硬拒——回退为提示词编译并如实相告；绝不猜测插桩位置。
    """

    def test_every_whitelist_entry_declares_anchor_and_consumer(self):
        for class_type, entry in Krea2WorkflowAdapter.STYLE_INJECTION_WHITELIST.items():
            for key in ("host_types", "anchor_port", "anchor_node", "insert_port", "chain_input_port", "output_index"):
                self.assertIn(key, entry, f"{class_type} 缺少锚点契约字段 {key}")
            self.assertTrue(
                set(entry["host_types"]) <= {"KSampler", "KSamplerAdvanced"},
                "锚点宿主只能是采样器",
            )

    def test_native_styles_fall_back_to_prompt_compilation_when_the_anchor_is_missing(self):
        workflow = {
            "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "old.safetensors"}},
            "2": {"class_type": "PrimitiveStringMultiline", "inputs": {"value": "old"}},
            "3": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": ["2", 0]}},
            "4": {"class_type": "ConditioningCombine", "inputs": {"conditioning_1": ["3", 0], "conditioning_2": ["3", 0]}},
            "5": {"class_type": "KSampler", "inputs": {"model": ["1", 0], "positive": ["4", 0], "negative": ["3", 0]}},
            "6": {"class_type": "SaveImage", "inputs": {"filename_prefix": "old"}},
        }
        config = BatchConfig(
            "flow.json", "model.safetensors", style_application="native",
            styles=[{"catalog": "anime", "name": "Classic Cel", "prompt": "{prompt}, classic cel"}],
        )
        adapter = Krea2WorkflowAdapter(workflow)
        report = adapter.capabilities(config)
        self.assertTrue(report["ready"], "锚点不满足时应回退而非拒绝")
        self.assertEqual("本地提示词编译", report["style"]["mode"])
        self.assertTrue(report["style"]["native_fallback"])
        self.assertTrue(any("锚点" in warning and "编译进提示词" in warning for warning in report["warnings"]))
        compiled = PromptCompiler.compile("one subject", config)
        self.assertEqual("one subject", compiled, "原生模式下编译阶段不合并风格模板")
        graph = adapter.build(compiled, config, "Batch/item")
        self.assertEqual([], [node_id for node_id, node in graph.items() if node["class_type"] == "easy stylesSelector"])
        self.assertEqual("one subject, classic cel", graph["2"]["inputs"]["value"], "回退档把模板编译进提示词文本")
        self.assertEqual(["4", 0], graph["5"]["inputs"]["positive"], "作者的条件链保持原样")
        ledger = adapter.audit()["style_injection"]
        self.assertEqual("编译回退（原生锚点不满足）", ledger["mode"])
        self.assertFalse(ledger["injected"])
        self.assertEqual({"5": "ConditioningCombine"}, ledger["unanchored"])

    def test_native_styles_cover_only_the_samplers_that_meet_the_anchor(self):
        workflow = {
            "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "old.safetensors"}},
            "2": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": "old"}},
            "3": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["2", 0]}},
            "4": {"class_type": "KSampler", "inputs": {"model": ["1", 0], "positive": ["2", 0], "negative": ["3", 0]}},
            "5": {"class_type": "KSamplerAdvanced", "inputs": {"model": ["1", 0], "positive": ["3", 0], "negative": ["3", 0]}},
            "6": {"class_type": "SaveImage", "inputs": {"filename_prefix": "old"}},
        }
        config = BatchConfig(
            "flow.json", "model.safetensors", style_application="native",
            styles=[{"catalog": "anime", "name": "Anime"}],
        )
        compiled = PromptCompiler.compile("one subject", config)
        adapter = Krea2WorkflowAdapter(workflow)
        graph = adapter.build(compiled, config, "Batch/item")
        selectors = [node_id for node_id, node in graph.items() if node["class_type"] == "easy stylesSelector"]
        self.assertEqual(1, len(selectors), "锚点命中的采样器共享一条组合器链")
        self.assertEqual([selectors[-1], 0], graph["2"]["inputs"]["text"])
        self.assertEqual(["3", 0], graph["5"]["inputs"]["positive"], "未满足锚点的采样器保持作者原接线")
        ledger = adapter.audit()["style_injection"]
        self.assertTrue(ledger["injected"])
        self.assertEqual({"4": "2"}, ledger["hosts"])
        self.assertEqual({"5": "ConditioningZeroOut"}, ledger["unanchored"])

    def test_native_styles_without_anchor_or_template_text_are_refused_before_build(self):
        workflow = {
            "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "old.safetensors"}},
            "2": {"class_type": "PrimitiveStringMultiline", "inputs": {"value": "old"}},
            "3": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": ["2", 0]}},
            "4": {"class_type": "ConditioningCombine", "inputs": {"conditioning_1": ["3", 0], "conditioning_2": ["3", 0]}},
            "5": {"class_type": "KSampler", "inputs": {"model": ["1", 0], "positive": ["4", 0], "negative": ["3", 0]}},
            "6": {"class_type": "SaveImage", "inputs": {"filename_prefix": "old"}},
        }
        config = BatchConfig(
            "flow.json", "model.safetensors", style_application="native",
            styles=[{"catalog": "anime", "name": "Anime"}],
        )
        adapter = Krea2WorkflowAdapter(workflow)
        report = adapter.capabilities(config)
        self.assertFalse(report["ready"])
        self.assertTrue(any("锚点契约" in error for error in report["errors"]))
        with self.assertRaisesRegex(ValueError, "锚点契约"):
            adapter.build("one subject", config, "Batch/item")

    def test_the_displaced_text_source_is_recorded_when_injection_takes_over(self):
        """原生注入接管编码器 text 口时，被挤掉的连线来源必须记账且不得删除。"""
        workflow = {
            "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "old.safetensors"}},
            "2": {"class_type": "PrimitiveStringMultiline", "inputs": {"value": "old"}},
            "3": {"class_type": "easy stylesSelector", "inputs": {"positive": ["2", 0], "styles": "author", "select_styles": "Author"}},
            "4": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": ["3", 0]}},
            "5": {"class_type": "KSampler", "inputs": {"model": ["1", 0], "positive": ["4", 0], "negative": ["4", 0]}},
            "6": {"class_type": "SaveImage", "inputs": {"filename_prefix": "old"}},
        }
        config = BatchConfig(
            "flow.json", "model.safetensors", style_application="native",
            styles=[{"catalog": "anime", "name": "Anime"}],
        )
        compiled = PromptCompiler.compile("one subject", config)
        adapter = Krea2WorkflowAdapter(workflow)
        graph = adapter.build(compiled, config, "Batch/item")
        ledger = adapter.audit()["style_injection"]
        self.assertTrue(ledger["injected"])
        self.assertEqual({"3": "easy stylesSelector"}, ledger["displaced"])
        self.assertEqual([ledger["injected_nodes"][-1], 0], graph["4"]["inputs"]["text"], "注入链接管 text 口")
        self.assertIn("3", graph, "被挤掉的节点保留在图中")
        self.assertEqual("easy stylesSelector", graph["3"]["class_type"])
        for node_id in ledger["injected_nodes"]:
            self.assertTrue(
                adapter.last_sources[node_id]
                and all(value == SOURCE_INJECTED for value in adapter.last_sources[node_id].values()),
                "注入节点必须标记为软件注入",
            )


class PromptCompilerInterfaceTests(unittest.TestCase):
    def test_merges_task_global_and_style_negative_prompts_in_order_without_duplicates(self):
        config = BatchConfig(
            workflow_path="flow.json",
            model="model.safetensors",
            negative_prompt="多人, 真人",
            styles=[{"negative_prompt": "真人, 3d render"}, {"negative_prompt": "watermark"}],
        )
        self.assertEqual(
            "三视图, 多人, 真人, 3d render, watermark",
            PromptCompiler.compile_negative("三视图, 多人", config),
        )

    def test_adds_confirmed_lora_triggers_and_single_subject_guard(self):
        config = BatchConfig(
            workflow_path="flow.json",
            model="Krea2-red.safetensors",
            style_library="anime",
            style_name="Cel",
            loras=[{"name": "dress.safetensors", "strength": 0.7, "trigger_words": ["sailor_uniform"], "use_triggers": True}],
            single_subject_guard=True,
        )
        compiled = PromptCompiler.compile("海边的蓝色裙装", config)
        self.assertIn("sailor_uniform", compiled)
        self.assertIn("单幅连续画面", compiled)
        self.assertIn("仅一名人物", compiled)

    def test_subject_guard_does_not_prime_multi_view_concepts(self):
        config = BatchConfig(
            workflow_path="flow.json",
            model="Krea2-red.safetensors",
            single_subject_guard=True,
        )
        compiled = PromptCompiler.compile("一名女性站在庭院中", config)
        for risky_term in ("三视图", "角色设定表", "分镜", "拼贴", "并排"):
            self.assertNotIn(risky_term, compiled)

    def test_subject_guard_removes_visual_layout_negations_from_imported_prompt(self):
        config = BatchConfig(
            workflow_path="flow.json",
            model="Krea2-red.safetensors",
            single_subject_guard=True,
        )
        source = "单幅2D插画，不是角色设计板。禁止出现重复人物、前后视图、三视图、分栏、拼贴、小窗。服装为蓝色水手服。"
        compiled = PromptCompiler.compile(source, config)
        self.assertIn("蓝色水手服", compiled)
        self.assertIn("人物只出现一次", compiled)
        for risky_term in ("角色设计板", "前后视图", "三视图", "分栏", "拼贴", "小窗"):
            self.assertNotIn(risky_term, compiled)


class ImageQualityInterfaceTests(unittest.TestCase):
    def test_detects_three_repeated_panels(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            panel = Image.new("RGB", (160, 240), "white")
            for y in range(40, 210):
                panel.putpixel((80, y), (0, 0, 0))
            triptych = Image.new("RGB", (480, 240))
            for x in (0, 160, 320):
                triptych.paste(panel, (x, 0))
            path = root / "triptych.png"
            triptych.save(path)
            result = ImageQualityInspector.inspect(path)
            self.assertTrue(result["repeated_panels"])
            self.assertGreater(result["panel_similarity"], 0.95)


class BatchRuntimeInterfaceTests(unittest.TestCase):
    def test_runs_bundle_copies_output_and_redacts_key(self):
        class FakeComfy:
            def __init__(self, output_file):
                self.output_file = output_file
                self.graph = None

            def submit(self, graph, client_id):
                self.graph = graph
                self.output_file.write_bytes(b"image")
                return "prompt-1"

            def output(self, prompt_id):
                return {"filename": self.output_file.name, "subfolder": "", "type": "output"}

            def interrupt(self):
                pass

        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            comfy = root / "ComfyUI"
            output = comfy / "output"
            target = root / "collection"
            output.mkdir(parents=True)
            workflow = root / "workflow.json"
            workflow.write_text(json.dumps({
                "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "old", "weight_dtype": "default"}},
                "2": {"class_type": "PrimitiveStringMultiline", "inputs": {"value": "old"}},
                "3": {"class_type": "easy stylesSelector", "inputs": {"styles": "old", "select_styles": "old", "positive": ["2", 0]}},
                "4": {"class_type": "ResolutionSelector", "inputs": {"aspect_ratio": "1:1", "megapixels": 1, "multiple": 32}},
                "5": {"class_type": "KSampler", "inputs": {"model": ["1", 0]}},
                "6": {"class_type": "SaveImage", "inputs": {"filename_prefix": "old"}},
            }), encoding="utf-8")
            fake = FakeComfy(output / "result.png")
            runner = BatchRunner(comfy, fake)
            bundle = PromptBundleParser.parse("one.json", json.dumps({"items": [{
                "title": "测试服装",
                "prompt": "服装提示词",
                "style_lora_preset_name": "水彩组合",
                "generation": {"style_library": "anime-2d", "style_name": "Classic Cel Animation"},
            }]}).encode())
            config = BatchConfig(str(workflow), "red.safetensors", "anime", "Cel", output_dir=str(target), llm={"api_key": "secret"})
            runner.start(bundle, config)
            deadline = time.time() + 3
            while runner.status()["status"] in {"starting", "running"} and time.time() < deadline:
                time.sleep(0.01)
            status = runner.status()
            self.assertEqual("completed", status["status"])
            self.assertTrue((target / "result.png").exists())
            report = json.loads(pathlib.Path(status["report"]).read_text(encoding="utf-8"))
            self.assertEqual("", report["config"]["llm"]["api_key"])
            self.assertTrue(report["effective"]["ready"])
            self.assertEqual("服装提示词", fake.graph["2"]["inputs"]["value"])
            self.assertEqual("anime-2d", fake.graph["3"]["inputs"]["styles"])
            self.assertEqual("Classic Cel Animation", fake.graph["3"]["inputs"]["select_styles"])
            self.assertIn("水彩组合", fake.graph["6"]["inputs"]["filename_prefix"])
            self.assertEqual("水彩组合", report["results"][0]["preset_name"])

    def test_retries_when_local_inspector_finds_repeated_panels(self):
        class FakeComfy:
            def __init__(self, paths):
                self.paths = paths
                self.calls = 0

            def submit(self, graph, client_id):
                self.calls += 1
                return f"prompt-{self.calls}"

            def output(self, prompt_id):
                path = self.paths[int(prompt_id.split("-")[-1]) - 1]
                return {"filename": path.name, "subfolder": "", "type": "output"}

            def interrupt(self):
                pass

        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            comfy = root / "ComfyUI"
            output = comfy / "output"
            target = root / "collection"
            output.mkdir(parents=True)
            panel = Image.new("RGB", (120, 200), "white")
            triptych = Image.new("RGB", (360, 200))
            for x in (0, 120, 240):
                triptych.paste(panel, (x, 0))
            triptych.save(output / "first.png")
            single = Image.new("RGB", (360, 200))
            for x in range(360):
                for y in range(200):
                    single.putpixel((x, y), (x % 256, y % 256, (x + y) % 256))
            single.save(output / "second.png")
            workflow = root / "workflow.json"
            workflow.write_text(json.dumps({
                "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "Krea2-old.safetensors"}},
                "2": {"class_type": "PrimitiveStringMultiline", "inputs": {"value": "old"}},
                "3": {"class_type": "easy stylesSelector", "inputs": {"styles": "old", "select_styles": "old", "positive": ["2", 0]}},
                "4": {"class_type": "ResolutionSelector", "inputs": {"aspect_ratio": "1:1"}},
                "5": {"class_type": "KSampler", "inputs": {"model": ["1", 0]}},
                "6": {"class_type": "SaveImage", "inputs": {"filename_prefix": "old"}},
            }), encoding="utf-8")
            fake = FakeComfy([output / "first.png", output / "second.png"])
            runner = BatchRunner(comfy, fake)
            bundle = PromptBundleParser.parse("one.txt", "单人礼服".encode())
            config = BatchConfig(str(workflow), "Krea2-red.safetensors", "anime", "Cel", output_dir=str(target), single_subject_guard=True, max_retries=1)
            runner.start(bundle, config)
            deadline = time.time() + 3
            while runner.status()["status"] in {"starting", "running"} and time.time() < deadline:
                time.sleep(0.01)
            status = runner.status()
            self.assertEqual(2, fake.calls)
            self.assertEqual(2, len(status["results"][0]["attempts"]))
            self.assertEqual("second.png", pathlib.Path(status["results"][0]["copied_to"]).name)


if __name__ == "__main__":
    unittest.main()
