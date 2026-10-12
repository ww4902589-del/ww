"""Remove local resource names and personal notes from committed workflows.

The workflow topology is the regression fixture. Installed model filenames,
temporary previews and author notes are not, so published fixtures replace them
with stable synthetic values while preserving node ids, links and widget shape.
"""

from __future__ import annotations

import json
import pathlib
import re
from typing import Any


ROOT = pathlib.Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
RESOURCE = re.compile(r"\.(?:bin|ckpt|gguf|onnx|patch|pt|pth|safetensors|png|jpe?g|webp)$", re.I)
TEMP_PREVIEW = re.compile(r"^/api/view\?filename=[^&]+(?:&.*)?$", re.I)
GENERIC_NOTE = "Fixture note: private workflow guidance was removed before publication."


def suffix(value: str) -> str:
    return pathlib.PurePosixPath(value.replace("\\", "/")).suffix.lower()


class Sanitizer:
    def __init__(self, public_resources: set[str]) -> None:
        self.resources: dict[str, str] = {}
        self.public_resources = public_resources

    def resource(self, value: str) -> str:
        if value in self.public_resources:
            return value
        if value not in self.resources:
            ext = suffix(value) or ".safetensors"
            self.resources[value] = f"fixture-resource-{len(self.resources) + 1:03d}{ext}"
        return self.resources[value]

    def widgets(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {key: self.widgets(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.widgets(item) for item in value]
        if isinstance(value, str) and RESOURCE.search(value.replace("\\", "/")):
            return self.resource(value)
        return value

    def declared_models(self, value: Any, names: list[str]) -> Any:
        """Restore public model metadata into widget slots before anonymizing."""
        if isinstance(value, dict):
            return {key: self.declared_models(item, names) for key, item in value.items()}
        if isinstance(value, list):
            return [self.declared_models(item, names) for item in value]
        if isinstance(value, str) and RESOURCE.search(value.replace("\\", "/")) and names:
            return names.pop(0)
        return value

    def workflow(self, payload: dict[str, Any]) -> dict[str, Any]:
        for node in payload.get("nodes", []):
            if not isinstance(node, dict):
                continue
            models = node.get("properties", {}).get("models", [])
            declared = [
                str(model["name"])
                for model in models if isinstance(models, list) and isinstance(model, dict)
                and str(model.get("url") or "").startswith("https://huggingface.co/") and model.get("name")
            ]
            if "widgets_values" in node:
                if declared:
                    node["widgets_values"] = self.declared_models(node["widgets_values"], list(declared))
                node["widgets_values"] = self.widgets(node["widgets_values"])
            if "widgets_values_named" in node:
                if declared:
                    node["widgets_values_named"] = self.declared_models(
                        node["widgets_values_named"], list(declared)
                    )
                node["widgets_values_named"] = self.widgets(node["widgets_values_named"])
            node_type = str(node.get("type") or "").lower()
            if "label" in node_type or "note" in node_type:
                values = node.get("widgets_values")
                if isinstance(values, list):
                    node["widgets_values"] = [
                        GENERIC_NOTE if isinstance(item, str) and len(item) > 120 else item
                        for item in values
                    ]
            self._previews(node)
        return payload

    def _previews(self, value: Any) -> None:
        if isinstance(value, dict):
            for key, item in list(value.items()):
                if isinstance(item, str) and TEMP_PREVIEW.match(item):
                    value[key] = "/api/view?filename=fixture-preview.png&type=temp"
                else:
                    self._previews(item)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                if isinstance(item, str) and TEMP_PREVIEW.match(item):
                    value[index] = "/api/view?filename=fixture-preview.png&type=temp"
                else:
                    self._previews(item)


def main() -> int:
    paths = sorted(FIXTURES.glob("workflow_*.json"))
    payloads = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    public_resources: set[str] = set()
    for payload in payloads:
        for node in payload.get("nodes", []):
            models = node.get("properties", {}).get("models", []) if isinstance(node, dict) else []
            for model in models if isinstance(models, list) else []:
                if (
                    isinstance(model, dict)
                    and str(model.get("url") or "").startswith("https://huggingface.co/")
                    and model.get("name")
                ):
                    public_resources.add(str(model["name"]))
    sanitizer = Sanitizer(public_resources)
    for path, payload in zip(paths, payloads):
        sanitizer.workflow(payload)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"sanitized {len(paths)} workflows and {len(sanitizer.resources)} resource names")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
