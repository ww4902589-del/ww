"""Settings buttons reflect persisted state after save and delete."""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from comfybatch_v2_app import Application

from fakes import html_source, js_source


class SavedLoraProfileTests(unittest.TestCase):
    def test_inventory_marks_only_a_saved_profile_and_updates_after_delete(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            loras = root / "ComfyUI" / "models" / "loras"
            loras.mkdir(parents=True)
            (loras / "artist.safetensors").write_bytes(b"")
            app = Application(settings_path=root / "settings.json")
            app.comfy_root = root / "ComfyUI"
            app.workflow_roots = []
            with mock.patch.object(app.runner.client, "json", return_value={}):
                def row():
                    return next(x for x in app.inventory()["loras"] if x["value"] == "artist.safetensors")

                self.assertFalse(row()["has_saved_profile"])
                app.save_lora_profile({"name": "artist.safetensors", "trigger_words": []})
                self.assertTrue(row()["has_saved_profile"], "an empty but saved profile is still deletable")
                app.delete_lora_profile("artist.safetensors")
                self.assertFalse(row()["has_saved_profile"])

            restarted = Application(settings_path=root / "settings.json")
            restarted.comfy_root = root / "ComfyUI"
            restarted.workflow_roots = []
            with mock.patch.object(restarted.runner.client, "json", return_value={}):
                self.assertFalse(next(x for x in restarted.inventory()["loras"]
                                      if x["value"] == "artist.safetensors")["has_saved_profile"])


class SettingsButtonContractTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is optional")
    def test_page_settings_actions_update_state_and_report_failures(self):
        tests = pathlib.Path(__file__).resolve().parent
        script = tests / "settings_buttons_behavior.js"
        app_js = tests.parent / "src" / "app.js"
        result = subprocess.run(["node", str(script), str(app_js)],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(0, result.returncode, result.stderr)

    def test_preset_actions_are_tied_to_the_selected_preset(self):
        html = html_source()
        js = js_source()
        for button in ("updatePresetButton", "reloadPresetButton", "deletePresetButton"):
            self.assertIn(f'id="{button}"', html)
            self.assertIn(f"'{button}'", js)
        self.assertIn("button.disabled=!selected", js)
        self.assertIn("deletePreset(this)", html)

    def test_lora_delete_button_uses_row_identity_and_saved_state(self):
        js = js_source()
        self.assertIn("x.has_saved_profile?", js)
        self.assertIn("deleteLoraProfile(${i},this)", js)
        self.assertIn("const name=selectedLoras[index]?.name", js)


if __name__ == "__main__":
    unittest.main()
