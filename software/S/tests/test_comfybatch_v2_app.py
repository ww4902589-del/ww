import json
import io
import pathlib
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from fakes import ConnectedClient, page_source, ui_workflow, write_workflow

from PIL import Image

from comfybatch_v2_app import Application, choose_launch_port, parse_prompt_indexes
from comfybatch_v2_core import BatchConfig, PromptBundleParser


class ApplicationPreflightTests(unittest.TestCase):
    def test_imports_multiple_images_as_batch_tasks_and_stages_safe_copies(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            comfy = root / "ComfyUI"
            (comfy / "input").mkdir(parents=True)
            image_buffer = io.BytesIO()
            Image.new("RGB", (640, 360), "navy").save(image_buffer, format="PNG")
            app = Application(settings_path=root / "settings.json")
            app.comfy_root = comfy

            bundle = app.import_images([
                {"filename": "01:横图.png", "raw": image_buffer.getvalue()},
                {"filename": "02?.png", "raw": image_buffer.getvalue()},
            ])

            self.assertEqual(2, len(bundle.items))
            self.assertEqual("images", bundle.source_format)
            self.assertEqual([640, 360], bundle.items[0].metadata["source_dimensions"])
            self.assertTrue((comfy / "input" / bundle.items[0].metadata["source_image"]).is_file())
            self.assertNotIn(":", pathlib.Path(bundle.items[0].metadata["source_image"]).name)
            task_ids = [item.metadata.get("task_id") for item in bundle.items]
            self.assertTrue(all(task_ids), "每个图片任务导入时必须获得稳定身份")
            self.assertEqual(len(task_ids), len(set(task_ids)), "同批图片任务 ID 必须唯一")

    def test_bundle_update_cannot_replace_server_owned_source_image(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            comfy = root / "ComfyUI"
            (comfy / "input").mkdir(parents=True)
            image_buffer = io.BytesIO()
            Image.new("RGB", (8, 8), "navy").save(image_buffer, format="PNG")
            app = Application(settings_path=root / "settings.json")
            app.comfy_root = comfy
            bundle = app.import_images([{"filename": "safe.png", "raw": image_buffer.getvalue()}])
            trusted = bundle.items[0].metadata["source_image"]
            (comfy / "input" / "other.png").write_bytes(image_buffer.getvalue())

            updated = app.update_bundle([{
                "title": "edited", "prompt": "keep", "negative_prompt": "",
                "metadata": {**bundle.items[0].metadata, "source_image": "other.png"},
            }])

            self.assertEqual(trusted, updated.items[0].metadata["source_image"])

    def test_bundle_update_preserves_source_when_an_image_task_is_duplicated(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            comfy = root / "ComfyUI"
            (comfy / "input").mkdir(parents=True)
            first_buffer = io.BytesIO()
            second_buffer = io.BytesIO()
            Image.new("RGB", (8, 8), "navy").save(first_buffer, format="PNG")
            Image.new("RGB", (8, 8), "gold").save(second_buffer, format="PNG")
            app = Application(settings_path=root / "settings.json")
            app.comfy_root = comfy
            bundle = app.import_images([
                {"filename": "first.png", "raw": first_buffer.getvalue()},
                {"filename": "second.png", "raw": second_buffer.getvalue()},
            ])
            first = bundle.items[0].to_dict()
            duplicate = bundle.items[0].to_dict()
            duplicate["metadata"] = {**duplicate["metadata"], "task_id": "browser-copy"}
            second = bundle.items[1].to_dict()

            updated = app.update_bundle([first, duplicate, second])

            self.assertEqual(
                [first["metadata"]["source_image"], first["metadata"]["source_image"], second["metadata"]["source_image"]],
                [item.metadata["source_image"] for item in updated.items],
            )
            self.assertEqual(
                [first["metadata"]["task_id"], "browser-copy", second["metadata"]["task_id"]],
                [item.metadata["task_id"] for item in updated.items],
            )

    def test_interrogation_rejects_a_stale_task_identity_before_submission(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            comfy = root / "ComfyUI"
            (comfy / "input").mkdir(parents=True)
            image_buffer = io.BytesIO()
            Image.new("RGB", (8, 8), "navy").save(image_buffer, format="PNG")
            app = Application(settings_path=root / "settings.json")
            app.comfy_root = comfy
            bundle = app.import_images([{"filename": "safe.png", "raw": image_buffer.getvalue()}])
            source = bundle.items[0].metadata["source_image"]

            with self.assertRaisesRegex(ValueError, "任务身份已变化"):
                app.interrogate_task(1, source, "stale-task-id")

    def test_interrogation_rejects_result_if_bundle_changes_while_waiting(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            comfy = root / "ComfyUI"
            (comfy / "input").mkdir(parents=True)
            image_buffer = io.BytesIO()
            Image.new("RGB", (8, 8), "navy").save(image_buffer, format="PNG")
            raw = image_buffer.getvalue()
            app = Application(settings_path=root / "settings.json")
            app.comfy_root = comfy
            bundle = app.import_images([{"filename": "first.png", "raw": raw}])
            source = bundle.items[0].metadata["source_image"]
            app.schema = SimpleNamespace(class_types={"LoadImage", "H3ShowText", "BLIPCaption"})
            app.refresh_schema = lambda force=False: app.schema

            class ReplacingInterrogator:
                def run(self, *_args):
                    app.import_images([{"filename": "second.png", "raw": raw}])
                    return {"prompt": "stale", "backend": "BLIPCaption", "prompt_id": "p", "local_only": True}

            app.image_interrogator = ReplacingInterrogator()
            with self.assertRaisesRegex(ValueError, "任务合集已变化"):
                app.interrogate_task(1, source)
            self.assertNotEqual("stale", app.bundle.items[0].prompt)

    def test_resolves_selected_style_text_for_node_independent_compilation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            comfy = root / "ComfyUI"
            styles = comfy / "custom_nodes" / "easy" / "styles"
            styles.mkdir(parents=True)
            (styles / "anime.json").write_text(json.dumps([{
                "name": "Cel NZ",
                "prompt": "{prompt}, clean 2D cel shading",
                "negative_prompt": "photorealistic",
            }]), encoding="utf-8")
            app = Application()
            app.comfy_root = comfy
            app.workflow_roots = []
            app.runner.client = ConnectedClient()
            config = app.prepare_config(BatchConfig("flow.json", "model.safetensors", "anime", "Cel NZ"))
            self.assertIn("clean 2D cel shading", config.styles[0]["prompt"])
            self.assertEqual("photorealistic", config.styles[0]["negative_prompt"])

    def test_remembers_lora_trigger_words_across_restarts(self):
        with tempfile.TemporaryDirectory() as temp:
            settings = pathlib.Path(temp) / "settings.json"
            first = Application(settings_path=settings)
            first.save_lora_profile({
                "name": "artist.safetensors",
                "trigger_words": ["yoneyama mai", "vivid line art"],
                "use_triggers": True,
            })

            restarted = Application(settings_path=settings)
            self.assertEqual(
                ["yoneyama mai", "vivid line art"],
                restarted.lora_profiles()["artist.safetensors"]["trigger_words"],
            )

    def test_remembers_chinese_lora_name_and_recovers_from_backup(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            primary = root / "local" / "settings.json"
            backup = root / "portable" / "settings.json"
            first = Application(settings_path=primary, backup_settings_path=backup)
            first.save_lora_profile({
                "name": "new-style.safetensors",
                "display_name": "厚涂插画风格",
                "trigger_words": ["htl7q9vx"],
                "use_triggers": True,
            })
            primary.write_text("broken json", encoding="utf-8")

            restarted = Application(settings_path=primary, backup_settings_path=backup)
            profile = restarted.lora_profiles()["new-style.safetensors"]
            self.assertEqual("厚涂插画风格", profile["display_name"])
            self.assertEqual(["htl7q9vx"], profile["trigger_words"])

    def test_reuses_healthy_instance_or_falls_back_from_occupied_port(self):
        reused = choose_launch_port(
            "127.0.0.1", 8790,
            probe=lambda port: port == 8790,
            available=lambda port: False,
        )
        self.assertEqual(("reuse", 8790), reused)

        fallback = choose_launch_port(
            "127.0.0.1", 8790,
            probe=lambda port: False,
            available=lambda port: port == 8791,
        )
        self.assertEqual(("start", 8791), fallback)

    def test_persists_updates_and_deletes_style_lora_presets(self):
        with tempfile.TemporaryDirectory() as temp:
            settings = pathlib.Path(temp) / "settings.json"
            app = Application(settings_path=settings)
            app.bundle = PromptBundleParser.parse("prompts.txt", "第一条\n\n第二条".encode("utf-8"))
            preset = app.save_style_lora_preset({
                "name": "水彩与米山舞",
                "styles": [{"catalog": "anime", "name": "Watercolor"}],
                "loras": [{"name": "artist.safetensors", "strength": 0.7}],
            })
            app.assign_style_lora_preset(preset["id"], [1])
            app.save_style_lora_preset({**preset, "name": "水彩与米山舞·新版"})

            restarted = Application(settings_path=settings)
            self.assertEqual("水彩与米山舞·新版", restarted.style_lora_presets()[preset["id"]]["name"])
            app.delete_style_lora_preset(preset["id"])
            self.assertNotIn("style_lora_preset_id", app.bundle.items[0].metadata)

    def test_resolves_latest_preset_into_each_assigned_prompt(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            settings = root / "settings.json"
            comfy = root / "ComfyUI"
            styles = comfy / "custom_nodes" / "easy" / "styles"
            loras = comfy / "models" / "loras"
            styles.mkdir(parents=True)
            loras.mkdir(parents=True)
            (styles / "anime.json").write_text(json.dumps([{
                "name": "Watercolor", "prompt": "{prompt}, watercolor cel art"
            }]), encoding="utf-8")
            (loras / "artist.safetensors").write_bytes(b"")
            app = Application(settings_path=settings)
            app.comfy_root = comfy
            app.workflow_roots = []
            app.save_lora_profile({"name": "artist.safetensors", "display_name": "画师风格", "trigger_words": ["artist_style"]})
            app.bundle = PromptBundleParser.parse("prompts.txt", "第一条\n\n第二条".encode("utf-8"))
            preset = app.save_style_lora_preset({
                "name": "水彩组合",
                "styles": [{"catalog": "anime", "name": "Watercolor"}],
                "loras": [{"name": "artist.safetensors", "strength": 0.65}],
            })
            app.assign_style_lora_preset(preset["id"], [2])
            resolved = app.resolve_bundle_presets()
            generation = resolved.items[1].metadata["generation"]
            self.assertIn("watercolor cel art", generation["styles"][0]["prompt"])
            self.assertEqual(["artist_style"], generation["loras"][0]["trigger_words"])
            self.assertEqual("水彩组合", resolved.items[1].metadata["style_lora_preset_name"])

    def test_assigns_and_clears_batch_image_presets_without_overwriting_style_settings(self):
        app = Application()
        app.bundle = PromptBundleParser.parse("prompts.txt", "第一条\n\n第二条".encode("utf-8"))
        app.bundle.items[0].metadata["generation"] = {"styles": [{"name": "keep-style"}]}
        app.assign_image_preset("portrait-m", [1, 2])
        first = app.bundle.items[0].metadata
        # Must be the exact label ResolutionSelector declares. The original value
        # here was "9:16 (Portrait)", which that node does not accept, so
        # selecting this preset could only ever produce a 400. See
        # test_image_presets.py for the full schema check.
        self.assertEqual("9:16 (Portrait Widescreen)", first["generation"]["aspect_ratio"])
        self.assertEqual(0.9, first["generation"]["megapixels"])
        self.assertEqual("竖图·标准 720×1280", first["image_preset_name"])
        self.assertEqual([{"name": "keep-style"}], first["generation"]["styles"])
        app.assign_image_preset("", [1])
        self.assertNotIn("aspect_ratio", first["generation"])
        self.assertNotIn("megapixels", first["generation"])
        self.assertNotIn("image_preset_id", first)

    def test_resolves_original_node_local_style_thumbnail(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            comfy = root / "ComfyUI"
            styles = comfy / "custom_nodes" / "easy" / "styles"
            preview = comfy / "input" / "style_previews" / "krea2" / "sample.webp"
            styles.mkdir(parents=True)
            preview.parent.mkdir(parents=True)
            preview.write_bytes(b"preview")
            (styles / "anime.json").write_text(json.dumps([{
                "name": "Classic Cel", "name_cn": "经典赛璐璐",
                "prompt": "{prompt}",
                "thumbnail": "/view?filename=sample.webp&subfolder=style_previews%2Fkrea2&type=input",
            }]), encoding="utf-8")
            app = Application(settings_path=root / "settings.json")
            app.comfy_root = comfy
            app.workflow_roots = []
            app.inventory()
            kind, value = app.style_thumbnail("anime", "Classic Cel")
            self.assertEqual("file", kind)
            self.assertEqual(preview.resolve(), value)

    def test_parses_prompt_number_ranges(self):
        self.assertEqual([1, 3, 4, 5, 8], parse_prompt_indexes("1, 3-5，8", 8))
        with self.assertRaisesRegex(ValueError, "范围"):
            parse_prompt_indexes("2-9", 5)

    def test_blocks_model_from_wrong_family_before_queueing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            comfy = root / "ComfyUI"
            models = comfy / "models" / "diffusion_models"
            workflows = root / "workflows"
            models.mkdir(parents=True)
            workflows.mkdir()
            (models / "Krea2-red.safetensors").write_bytes(b"")
            (models / "minimax-video.safetensors").write_bytes(b"")
            # A connected workflow, so it clears the four-layer preflight and the
            # test really does isolate the model-family check.
            workflow = workflows / "krea.json"
            write_workflow(workflow, ui_workflow(model="Krea2-red.safetensors"))
            app = Application()
            app.comfy_root = comfy
            app.workflow_roots = [workflows]
            app.runner.client = ConnectedClient()
            good = BatchConfig(str(workflow), "Krea2-red.safetensors")
            app.validate_start(good)
            bad = BatchConfig(str(workflow), "minimax-video.safetensors")
            with self.assertRaisesRegex(ValueError, "不兼容"):
                app.validate_start(bad)

    def test_accepts_a_model_that_lives_in_a_loader_subfolder(self):
        """A subfoldered model must start whichever way the workflow names it.

        The inventory reports a model relative to its model root
        ("Krea2/krea2_turbo.safetensors") while a loader node may name the same
        file bare. The plain ``==`` this replaced compared those strings
        directly, so a model the user had installed was refused as
        "与所选工作流不兼容" -- the "调整麻烦" the user reported.
        """
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            comfy = root / "ComfyUI"
            models = comfy / "models" / "diffusion_models" / "Krea2"
            workflows = root / "workflows"
            models.mkdir(parents=True)
            workflows.mkdir()
            (models / "krea2_turbo_int8_convrot.safetensors").write_bytes(b"")
            # The workflow names the model bare, without the folder.
            workflow = workflows / "krea.json"
            write_workflow(workflow, ui_workflow(model="krea2_turbo_int8_convrot.safetensors"))
            app = Application()
            app.comfy_root = comfy
            app.workflow_roots = [workflows]
            app.runner.client = ConnectedClient()
            # Both the shape the inventory reports and the shape the user picks
            # must be accepted: they are the same installed file.
            for chosen in ("krea2_turbo_int8_convrot.safetensors",
                           "Krea2/krea2_turbo_int8_convrot.safetensors"):
                app.validate_start(BatchConfig(str(workflow), chosen))

    def test_updates_reviewed_bundle_without_requiring_json(self):
        app = Application()
        app.bundle = PromptBundleParser.parse("notes.txt", "原始提示词".encode())
        updated = app.update_bundle([{"title": "修改后", "prompt": "临时修改的提示词", "negative_prompt": "真人, 多人", "repeat": 2}])
        self.assertEqual(2, len(updated.items))
        self.assertEqual("临时修改的提示词", updated.items[1].prompt)
        self.assertEqual("真人, 多人", updated.items[1].negative_prompt)

    def test_page_exposes_global_and_per_task_negative_prompt_inputs(self):
        html = page_source()
        self.assertIn('id="globalNegative"', html)
        self.assertIn("item.negative_prompt", html)
        self.assertIn("negative_prompt:item.negative_prompt", html)

    def test_page_exposes_independent_excel_mapping_window(self):
        html = page_source()
        for marker in ('id="mappingDialog"', 'id="mappingTitle"', 'id="mappingPositive"', 'id="mappingNegative"', "/api/remap-import"):
            self.assertIn(marker, html)

    def test_selecting_saved_preset_immediately_makes_it_effective(self):
        html = page_source()
        self.assertIn('id="presetPicker" onchange="loadPreset(true)"', html)
        self.assertIn("let activePresetId=''", html)
        self.assertIn("任务级设定", html)
        self.assertIn("taskPresetSummaries()", html)


class InspectPayloadTests(unittest.TestCase):
    """The rail must not report a rejected configuration as clean.

    The first three preflight layers reject a workflow *before* anything is
    compiled, so there is no audit to read. The rail counted blocking problems
    from the audit alone and therefore showed "没有阻断问题（未检查）" for a
    configuration the check had just declared 不能生成 -- two panels on the same
    screen contradicting each other.
    """

    def test_a_rejected_preflight_counts_as_blocking(self):
        app = Application()
        app.last_preflight = {
            "ready": False,
            "errors": [{
                "severity": "blocking",
                "title": "所选风格没有可编译的模板文字",
                "detail": "工作流不满足原生风格注入的锚点契约（采样器正面口未直连 CLIPTextEncode）",
            }],
        }
        payload = app.inspect_payload()
        # A check ran, so the UI knows to stop saying 尚未检查...
        self.assertTrue(payload["inspected"])
        # ...but it did not pass, so the blocking count must not read as clean.
        self.assertFalse(payload["ready"])
        self.assertEqual(1, payload["blocking_count"])
        self.assertEqual(1, len(payload["preflight_errors"]))
        # The reason text reaches the drawer, which renders Problem-shaped dicts.
        self.assertIn("CLIPTextEncode", payload["preflight_errors"][0]["detail"])

    def test_no_check_yet_is_not_reported_as_inspected(self):
        app = Application()
        app.last_preflight = {}
        payload = app.inspect_payload()
        self.assertFalse(payload["inspected"])
        self.assertFalse(payload["ready"])
        self.assertEqual(0, payload["blocking_count"])

    def test_a_failure_before_the_preflight_records_anything_still_reports(self):
        """``prepare_config`` can fail first, leaving no problems to show."""
        app = Application()
        app.last_preflight = {}
        payload = app.blocked_preflight_payload('模型""与所选工作流不兼容')
        self.assertTrue(payload["inspected"])
        self.assertFalse(payload["ready"])
        self.assertEqual(1, payload["blocking_count"])
        self.assertIn("不兼容", payload["preflight_errors"][0]["detail"])

    def test_the_real_problems_are_kept_when_the_preflight_recorded_them(self):
        app = Application()
        app.last_preflight = {
            "ready": False,
            "errors": [{"severity": "blocking", "title": "参数越界", "detail": "steps 超出范围"}],
        }
        payload = app.blocked_preflight_payload("预检未通过")
        self.assertEqual(1, len(payload["preflight_errors"]))
        self.assertEqual("参数越界", payload["preflight_errors"][0]["title"])


if __name__ == "__main__":
    unittest.main()
