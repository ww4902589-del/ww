"""Local-only image-to-prompt workflow selection and execution."""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Collection
from typing import Any


EASY_NODE = "easy imageInterrogator"
BLIP_NODE = "BLIPCaption"
OUTPUT_NODE = "H3ShowText"
LOAD_NODE = "LoadImage"
REQUIRED_COMMON = (LOAD_NODE, OUTPUT_NODE)


class InterrogationUnavailable(ValueError):
    """Raised with an actionable local dependency message."""


def available_backends(class_types: Collection[str]) -> list[str]:
    """Return usable caption nodes in preference order."""
    if any(name not in class_types for name in REQUIRED_COMMON):
        return []
    return [name for name in (EASY_NODE, BLIP_NODE) if name in class_types]


def capability_payload(class_types: Collection[str]) -> dict[str, Any]:
    backends = available_backends(class_types)
    missing = [name for name in REQUIRED_COMMON if name not in class_types]
    if not any(name in class_types for name in (EASY_NODE, BLIP_NODE)):
        missing.append(f"{EASY_NODE} 或 {BLIP_NODE}")
    return {
        "available": bool(backends), "preferred": backends[0] if backends else "",
        "backends": backends, "missing": missing, "local_only": True,
    }


def build_graph(source_image: str, backend: str) -> dict[str, Any]:
    """Build an API-format graph from a ComfyUI-input-relative image path."""
    if backend not in {EASY_NODE, BLIP_NODE}:
        raise ValueError(f"不支持的反推节点：{backend}")
    caption_inputs: dict[str, Any] = {"image": ["1", 0]}
    if backend == EASY_NODE:
        # ``best`` loads a much larger ranking set and can make the first click
        # look frozen for many minutes. ``fast`` keeps the one-click workflow
        # responsive while still using the preferred local EasyUse backend.
        caption_inputs.update({"mode": "fast", "use_lowvram": True})
    else:
        caption_inputs.update({"min_length": 24, "max_length": 80, "device_mode": "AUTO", "enabled": True})
    return {
        "1": {"class_type": LOAD_NODE, "inputs": {"image": source_image}},
        "2": {"class_type": backend, "inputs": caption_inputs},
        "3": {"class_type": OUTPUT_NODE, "inputs": {"text": ["2", 0]}},
    }


def text_from_entry(entry: dict[str, Any]) -> str:
    """Extract H3's UI text from a normalised ComfyUI history entry."""
    output = ((entry.get("outputs") or {}).get("3") or {}) if isinstance(entry, dict) else {}
    values = output.get("text") or output.get("string") or output.get("STRING") or []
    if isinstance(values, str):
        values = [values]
    if isinstance(values, list):
        return "\n".join(str(value).strip() for value in values if str(value).strip()).strip()
    return ""


class ImageInterrogator:
    """Serialised executor so several clicks do not load caption models at once."""

    def __init__(self, client: Any, *, timeout: float = 600.0, poll_interval: float = 0.5):
        self.client = client
        self.timeout = timeout
        self.poll_interval = poll_interval
        self._lock = threading.Lock()

    def run(self, source_image: str, class_types: Collection[str]) -> dict[str, Any]:
        candidates = available_backends(class_types)
        if not candidates:
            missing = "、".join(capability_payload(class_types)["missing"])
            raise InterrogationUnavailable(
                "本机 ComfyUI 暂不能反推提示词。请安装或启用 " + missing
                + "，重启 ComfyUI 后点击“重新扫描本地资源”。"
            )
        errors: list[str] = []
        deadline = time.monotonic() + self.timeout
        if not self._lock.acquire(timeout=max(0.0, deadline - time.monotonic())):
            raise TimeoutError("等待本机提示词反推队列超时")
        try:
            for backend in candidates:
                try:
                    return self._run_backend(source_image, backend, deadline)
                except Exception as exc:  # noqa: BLE001 - fallback is required
                    errors.append(f"{backend}: {exc}")
                if time.monotonic() >= deadline:
                    break
        finally:
            self._lock.release()
        raise ValueError("本机提示词反推失败；已尝试可用节点。" + "；".join(errors))

    def _run_backend(self, source_image: str, backend: str, deadline: float) -> dict[str, Any]:
        prompt_id = self.client.submit(build_graph(source_image, backend), "caption-" + uuid.uuid4().hex)
        while time.monotonic() < deadline:
            result = self.client.poll(prompt_id)
            state = str(result.get("state") or "pending")
            if state == "error":
                problems = result.get("problems") or []
                detail = "；".join(str(getattr(problem, "detail", problem)) for problem in problems)
                raise RuntimeError(detail or "ComfyUI 节点执行失败")
            if state == "done":
                text = text_from_entry(result.get("entry") or {})
                if not text:
                    raise RuntimeError("H3ShowText 未在 ComfyUI 历史中返回文本")
                return {"prompt": text, "backend": backend, "prompt_id": prompt_id, "local_only": True}
            time.sleep(self.poll_interval)
        raise TimeoutError(f"等待 {backend} 返回结果超时")
