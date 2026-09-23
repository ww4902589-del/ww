"""Create a publishable object_info fixture without local resource inventory.

The recorded ComfyUI registry is useful for binding regression tests, but it
also exposes every installed custom node plus local image/model filenames. This
tool keeps only node types used by committed workflow fixtures and reduces
file-backed combos to values referenced by committed workflows and tests.
"""

from __future__ import annotations

import json
import pathlib
import re
import sys
from typing import Any


ROOT = pathlib.Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "object_info.json"
REFERENCE_DIRS = (ROOT / "tests",)

IMAGE_EXTENSIONS = {".avif", ".bmp", ".gif", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}
RESOURCE_EXTENSIONS = IMAGE_EXTENSIONS | {
    ".bin", ".ckpt", ".onnx", ".patch", ".pt", ".pth", ".safetensors",
}
PRIVATE_PATH = re.compile(
    r"(?:(?<![A-Za-z])[A-Za-z]:[\\/]|/Users/|/home/|AppData[\\/])",
    re.IGNORECASE,
)


def suffix(value: str) -> str:
    clean = value.replace("\\", "/").split("?")[0]
    return pathlib.PurePosixPath(clean).suffix.lower()


def referenced_resources() -> set[str]:
    """Return file-like values deliberately present in source/test fixtures."""
    values: set[str] = {"RealESRGAN_x4plus_anime_6B.pth"}
    quoted = re.compile(r"[\"']([^\"'\r\n]+)[\"']")
    for base in REFERENCE_DIRS:
        for path in base.rglob("*"):
            if (
                path == FIXTURE
                or path.name.startswith("models_")
                or not path.is_file()
                or path.suffix.lower() not in {".json", ".py"}
            ):
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for match in quoted.finditer(text):
                value = match.group(1)
                if suffix(value) in RESOURCE_EXTENSIONS and not PRIVATE_PATH.search(value):
                    values.add(value)
                    values.add(value.replace("\\", "/"))
    return values


def required_node_names() -> set[str]:
    names: set[str] = set()
    for path in (ROOT / "tests" / "fixtures").glob("workflow_*.json"):
        workflow = json.loads(path.read_text(encoding="utf-8"))
        for node in workflow.get("nodes", []):
            if isinstance(node, dict) and node.get("type"):
                names.add(str(node["type"]))
    return names


def sanitized_combo(name: str, values: list[Any], options: dict[str, Any], allowed: set[str]) -> list[Any]:
    strings = [value for value in values if isinstance(value, str)]
    extensions = {suffix(value) for value in strings}
    image_backed = bool(options.get("image_upload")) or (
        bool(strings) and bool(extensions) and extensions <= IMAGE_EXTENSIONS
    )
    if image_backed:
        return ["fixture-input.png"]

    file_backed = any(ext in RESOURCE_EXTENSIONS for ext in extensions) or any(
        PRIVATE_PATH.search(value) for value in strings
    )
    if not file_backed:
        return values

    kept = [value for value in strings if value in allowed or value.replace("\\", "/") in allowed]
    if kept:
        return list(dict.fromkeys(kept))

    extension = next((ext for ext in extensions if ext in RESOURCE_EXTENSIONS), ".safetensors")
    return [f"fixture-{name.replace('_', '-')}{extension}"]


def sanitize(payload: dict[str, Any]) -> dict[str, Any]:
    required = required_node_names()
    payload = {name: node for name, node in payload.items() if name in required}
    allowed = referenced_resources()
    for node in payload.values():
        if not isinstance(node, dict):
            continue
        inputs = node.get("input")
        if not isinstance(inputs, dict):
            continue
        for group_name in ("required", "optional"):
            group = inputs.get(group_name)
            if not isinstance(group, dict):
                continue
            for input_name, spec in group.items():
                if not isinstance(spec, list) or not spec or not isinstance(spec[0], list):
                    continue
                options = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
                spec[0] = sanitized_combo(str(input_name), spec[0], options, allowed)
    return scrub_private_paths(payload)


def scrub_private_paths(value: Any) -> Any:
    """Replace local absolute paths that can also appear in defaults/tooltips."""
    if isinstance(value, dict):
        return {key: scrub_private_paths(item) for key, item in value.items()}
    if isinstance(value, list):
        return [scrub_private_paths(item) for item in value]
    if isinstance(value, str) and PRIVATE_PATH.search(value):
        return PRIVATE_PATH.sub("<local-path>/", value)
    return value


def assert_publishable(payload: dict[str, Any]) -> None:
    serialized = json.dumps(payload, ensure_ascii=False)
    if PRIVATE_PATH.search(serialized):
        raise ValueError("sanitized fixture still contains an absolute/private path")
    forbidden = ("ChatGPT Image ", "微信图片_", "codex-clipboard-")
    found = [token for token in forbidden if token in serialized]
    if found:
        raise ValueError(f"sanitized fixture still contains private image names: {found}")


def main() -> int:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload = sanitize(payload)
    assert_publishable(payload)
    FIXTURE.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"sanitized {len(payload)} node definitions: {FIXTURE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
