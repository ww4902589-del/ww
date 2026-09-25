"""Task #12: local style-only recommendation UI and behavior."""

from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


class StyleRecommendationTests(unittest.TestCase):
    def test_style_only_controls_are_visible(self):
        html = (ROOT / "src" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="recommendStylesButton"', html)
        self.assertIn('id="styleRecommendationStatus"', html)
        self.assertIn('id="styleRecommendations"', html)
        self.assertIn('不包含 LoRA', html)

    @unittest.skipUnless(shutil.which("node"), "Node.js is optional")
    def test_real_page_functions_recommend_and_apply_without_lora_changes(self):
        script = Path(__file__).with_name("style_recommendation_behavior.js")
        result = subprocess.run(
            ["node", str(script)], cwd=ROOT, capture_output=True, text=True,
            timeout=15, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
