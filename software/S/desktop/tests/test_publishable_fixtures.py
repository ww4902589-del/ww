"""Publication guardrails for recorded ComfyUI workflow fixtures."""

from __future__ import annotations

import json
import pathlib
import re
import unittest
from typing import Any


FIXTURES = pathlib.Path(__file__).parent / "fixtures"
RESOURCE = re.compile(r"\.(?:bin|ckpt|gguf|onnx|patch|pt|pth|safetensors|png|jpe?g|webp)$", re.I)
PRIVATE_PATH = re.compile(r"(?:(?<![A-Za-z])[A-Za-z]:[\\/]|/Users/|/home/|AppData[\\/])", re.I)


def strings(value: Any):
    if isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)
    elif isinstance(value, str):
        yield value


class PublishableFixtureTests(unittest.TestCase):
    def test_workflow_widgets_only_name_public_or_synthetic_resources(self):
        for path in FIXTURES.glob("workflow_*.json"):
            payload = json.loads(path.read_text(encoding="utf-8"))
            public = {
                str(model["name"])
                for node in payload.get("nodes", []) if isinstance(node, dict)
                for model in node.get("properties", {}).get("models", [])
                if isinstance(model, dict)
                and str(model.get("url") or "").startswith("https://huggingface.co/")
                and model.get("name")
            }
            for node in payload.get("nodes", []):
                if not isinstance(node, dict):
                    continue
                widget_text = list(strings(node.get("widgets_values", [])))
                widget_text += list(strings(node.get("widgets_values_named", {})))
                for value in widget_text:
                    clean = value.replace("\\", "/").split("?", 1)[0]
                    if RESOURCE.search(clean):
                        self.assertTrue(
                            value.startswith("fixture-") or value in public,
                            f"private resource-like widget in {path.name}: {value}",
                        )

    def test_workflows_have_no_private_paths_or_original_author_notes(self):
        forbidden = ("pasted/", "ChatGPT Image ", "微信图片_", "▶▶特调大模型方式")
        for path in FIXTURES.glob("workflow_*.json"):
            text = path.read_text(encoding="utf-8")
            self.assertIsNone(PRIVATE_PATH.search(text), path.name)
            for token in forbidden:
                self.assertNotIn(token, text, path.name)


if __name__ == "__main__":
    unittest.main()
