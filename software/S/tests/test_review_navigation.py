"""Runtime checks for task #11's review navigation."""

from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ReviewNavigationTests(unittest.TestCase):
    def test_controls_are_visible_and_named(self):
        html = (ROOT / "src" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="reviewJumpIndex"', html)
        self.assertIn('id="reviewJumpButton"', html)
        self.assertIn('id="reviewJumpStatus"', html)
        self.assertIn('aria-live="polite"', html)

    @unittest.skipUnless(shutil.which("node"), "Node.js is optional")
    def test_navigation_uses_real_page_functions(self):
        script = Path(__file__).with_name("review_navigation_behavior.js")
        result = subprocess.run(
            ["node", str(script)], cwd=ROOT, capture_output=True, text=True,
            timeout=15, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
