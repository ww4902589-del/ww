"""Every built-in image preset must be a value ComfyUI actually accepts.

Motivation: five of the nine original presets used aspect-ratio labels that do
not exist in the live ``ResolutionSelector`` candidate list, so choosing one
could only ever produce ``HTTP Error 400 value_not_in_list``. This test pins all
of them against the recorded real schema so the bug cannot come back.
"""

from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from fakes import SOURCE_ROOT  # noqa: E402

from comfybatch_nodeschema import NodeSchemaRegistry  # noqa: E402
from comfybatch_v2_app import IMAGE_PRESETS  # noqa: E402
from comfybatch_v2_core import resolve_image_dimensions  # noqa: E402


def real_registry() -> NodeSchemaRegistry:
    return NodeSchemaRegistry.from_path(SOURCE_ROOT.parent / "tests" / "fixtures" / "object_info.json")


class ImagePresetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = real_registry()
        cls.candidates = cls.registry.combo_candidates("ResolutionSelector", "aspect_ratio")

    def test_schema_exposes_the_real_candidate_list(self):
        self.assertEqual(
            [
                "1:1 (Square)", "2:3 (Portrait Photo)", "3:2 (Photo)", "3:4 (Portrait Standard)",
                "4:3 (Standard)", "9:16 (Portrait Widescreen)", "16:9 (Widescreen)", "21:9 (Ultrawide)",
            ],
            self.candidates,
        )

    def test_every_preset_label_is_a_declared_candidate(self):
        for preset in IMAGE_PRESETS:
            with self.subTest(preset=preset["id"]):
                self.assertIn(preset["aspect_ratio"], self.candidates,
                              f"预设 {preset['name']} 的比例标签 ComfyUI 不接受")

    def test_every_preset_passes_schema_validation(self):
        for preset in IMAGE_PRESETS:
            with self.subTest(preset=preset["id"]):
                ok, reason, _candidates, _expected = self.registry.validate(
                    "ResolutionSelector", "aspect_ratio", preset["aspect_ratio"]
                )
                self.assertTrue(ok, f"{preset['id']}: {reason}")

    def test_megapixels_is_within_the_node_range(self):
        spec = self.registry.input_spec("ResolutionSelector", "megapixels")
        low = spec["opts"].get("min")
        high = spec["opts"].get("max")
        self.assertEqual(0.1, low)
        self.assertEqual(16.0, high)
        for preset in IMAGE_PRESETS:
            with self.subTest(preset=preset["id"]):
                self.assertGreaterEqual(preset["megapixels"], low)
                self.assertLessEqual(preset["megapixels"], high)

    def test_each_preset_resolves_to_its_advertised_pixel_size(self):
        """The name states a size, so the resolver must produce exactly that."""
        expected = {
            "wide-s": (1024, 576), "wide-m": (1280, 720), "wide-l": (1536, 864),
            "landscape-m": (1200, 800), "classic-m": (1152, 864), "square-m": (1024, 1024),
            "portrait-s": (576, 1024), "portrait-m": (720, 1280), "portrait-l": (864, 1536),
        }
        for preset in IMAGE_PRESETS:
            with self.subTest(preset=preset["id"]):
                width, height = resolve_image_dimensions(preset["aspect_ratio"], preset["megapixels"])
                self.assertEqual(expected[preset["id"]], (width, height))

    def test_preset_ids_and_names_are_unique(self):
        ids = [preset["id"] for preset in IMAGE_PRESETS]
        names = [preset["name"] for preset in IMAGE_PRESETS]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(names), len(set(names)))

    def test_the_former_invalid_labels_are_gone(self):
        """The exact strings that used to be shipped and could never work."""
        used = {preset["aspect_ratio"] for preset in IMAGE_PRESETS}
        for broken in ("3:2 (Landscape)", "4:3 (Landscape)", "9:16 (Portrait)"):
            self.assertNotIn(broken, used)
            self.assertNotIn(broken, self.candidates)


if __name__ == "__main__":
    unittest.main()
