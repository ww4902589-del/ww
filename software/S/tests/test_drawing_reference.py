"""A browser-exported PNG must reach the existing image task and LoadImage seam."""

from __future__ import annotations

import io
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

from PIL import Image

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from fakes import SOURCE_ROOT  # noqa: E402
from test_purpose_preflight import text_to_image_graph, with_image_input  # noqa: E402
from comfybatch_nodeschema import NodeSchemaRegistry, set_active_registry  # noqa: E402
from comfybatch_v2_app import Application  # noqa: E402
from comfybatch_v2_core import BatchConfig, Krea2WorkflowAdapter  # noqa: E402


class DrawingReferencePathTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is optional")
    def test_canvas_controls_import_through_the_real_page_script(self):
        script = pathlib.Path(__file__).with_name("drawing_reference_behavior.js")
        result = subprocess.run(
            ["node", str(script)], cwd=SOURCE_ROOT.parent,
            capture_output=True, text=True, timeout=15, check=False,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_png_import_becomes_a_task_and_reaches_load_image(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            comfy = root / "ComfyUI"
            (comfy / "input").mkdir(parents=True)
            output = io.BytesIO()
            Image.new("RGB", (640, 400), "white").save(output, format="PNG")
            app = Application(settings_path=root / "settings.json")
            app.comfy_root = comfy

            bundle = app.import_images([{"filename": "绘画参考.png", "raw": output.getvalue()}])
            item = bundle.items[0]
            source = item.metadata["source_image"]
            self.assertEqual("images", bundle.source_format)
            self.assertTrue((comfy / "input" / source).is_file())
            self.assertEqual("绘画参考.png", item.metadata["source_filename"])

            registry = NodeSchemaRegistry.from_path(SOURCE_ROOT.parent / "tests" / "fixtures" / "object_info.json")
            set_active_registry(registry)
            config = BatchConfig(workflow_path="", model="m.safetensors", purpose="img2img")
            compatible = Krea2WorkflowAdapter(with_image_input(text_to_image_graph()), registry)
            graph = compatible.build("绘画参考", config, "ComfyBatch-V2/reference-test", source_image=source)
            self.assertEqual(source, graph["1"]["inputs"]["image"])

            incompatible = Krea2WorkflowAdapter(text_to_image_graph(), registry)
            report = incompatible.capabilities(config, source_image=source)
            self.assertFalse(report["ready"])
            self.assertIn("LoadImage", "；".join(report["errors"]))


if __name__ == "__main__":
    unittest.main()
