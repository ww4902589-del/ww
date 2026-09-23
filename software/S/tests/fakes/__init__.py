"""Reusable test doubles and fixtures for the S / ComfyBatch V2.14 suite.

These used to live as classes nested inside individual test methods, which made
them impossible to reuse. They are collected here so every test verifies the
same observable contract.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile
from typing import Any

PACKAGE_ROOT = pathlib.Path(__file__).resolve().parents[2]
SOURCE_ROOT = PACKAGE_ROOT / "src"
FIXTURE_ROOT = PACKAGE_ROOT / "tests" / "fixtures"

if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

# Keep every test hermetic. Without this, constructing ``Application`` would read
# (and on some paths write) the real ``%LOCALAPPDATA%/ComfyBatch-S`` store holding
# the user's 14 LoRA profiles and 18 presets.
_TEMP_DATA = pathlib.Path(tempfile.mkdtemp(prefix="comfybatch-tests-"))
os.environ.setdefault("COMFYBATCH_DATA_DIR", str(_TEMP_DATA))


def html_source() -> str:
    """The page structure (``index.html``).

    The UI was one minified file; it is now three (``index.html``, ``app.css``,
    ``app.js``) so that markup, style and behaviour can be edited without
    touching each other. Element ids live here.
    """
    return (SOURCE_ROOT / "index.html").read_text(encoding="utf-8")


def css_source() -> str:
    return (SOURCE_ROOT / "app.css").read_text(encoding="utf-8")


def js_source() -> str:
    """The page behaviour (``app.js``). Function names and endpoints live here."""
    return (SOURCE_ROOT / "app.js").read_text(encoding="utf-8")


def install_static_assets(module) -> None:
    """Populate a module's ``STATIC_ASSETS`` from the sources.

    The handler serves the page from that table, which main() fills at startup;
    an in-process test server has to fill it itself.
    """
    module.STATIC_ASSETS.update({
        "/": html_source().encode("utf-8"),
        "/app.css": css_source().encode("utf-8"),
        "/app.js": js_source().encode("utf-8"),
    })


def page_source() -> str:
    """Everything the browser receives, concatenated.

    Most marker assertions care about *what the page contains*, not which of the
    three files it sits in, so they read this and keep working across the split.
    """
    return html_source() + "\n" + css_source() + "\n" + js_source()


def app_source() -> str:
    return (SOURCE_ROOT / "comfybatch_v2_app.py").read_text(encoding="utf-8")


def fixture(name: str) -> pathlib.Path:
    return FIXTURE_ROOT / name


def load_fixture_json(name: str) -> Any:
    return json.loads(fixture(name).read_text(encoding="utf-8"))


def make_comfy_tree(root: pathlib.Path) -> pathlib.Path:
    """Build a minimal ComfyUI directory tree and return the ComfyUI root."""
    comfy = root / "ComfyUI"
    for relative in (
        "models/diffusion_models",
        "models/checkpoints",
        "models/loras",
        "models/upscale_models",
        "models/vae",
        "models/clip",
        "custom_nodes/easy/styles",
        "output",
        "input",
    ):
        (comfy / relative).mkdir(parents=True, exist_ok=True)
    return comfy


class ConnectedClient:
    """ComfyUI client stub that answers every request, for preflight-only tests."""

    def __init__(self, base_url: str = "http://127.0.0.1:8188"):
        self.base_url = base_url
        self.calls: list[tuple[str, str]] = []

    def json(self, path: str, method: str = "GET", payload: dict[str, Any] | None = None, timeout: int = 30) -> dict[str, Any]:
        self.calls.append((method, path))
        return {"system": "ok"}

    def submit(self, graph, client_id):
        return "prompt-1"

    def output(self, prompt_id):
        return None

    def interrupt(self) -> None:
        pass


class ScriptedComfy:
    """Deterministic stand-in for :class:`ComfyClient`.

    Records the last submitted graph so tests can assert on the finally compiled
    API graph, and hands back a pre-seeded output file. ``output`` returns
    ``None`` for ``fail_prompt_ids`` so failure paths are exercisable.
    """

    def __init__(self, output_file: pathlib.Path | None = None, *, payload: bytes = b"image"):
        self.output_file = output_file
        self.payload = payload
        self.graphs: list[dict[str, Any]] = []
        self.graph: dict[str, Any] | None = None
        self.calls = 0
        self.timed_out: set[str] = set()
        self.interrupted = False

    def submit(self, graph, client_id):
        self.calls += 1
        self.graphs.append(graph)
        self.graph = graph
        if self.output_file is not None:
            self.output_file.parent.mkdir(parents=True, exist_ok=True)
            self.output_file.write_bytes(self.payload)
        return f"prompt-{self.calls}"

    def output(self, prompt_id):
        if prompt_id in self.timed_out:
            return None
        if self.output_file is None:
            return None
        return {"filename": self.output_file.name, "subfolder": "", "type": "output"}

    def interrupt(self) -> None:
        self.interrupted = True


class PrefixAwareComfy:
    """Simulates ComfyUI closely enough for redo tests.

    ComfyUI names each output from the ``SaveImage`` node's ``filename_prefix``:
    the prefix's last path segment becomes the filename stem and the prefix's
    directory becomes the subfolder. Re-submitting the same prefix produces a
    versioned name (``_00002_``). Modelling this is what lets a test assert on
    the real output name instead of a hand-written stand-in.
    """

    def __init__(self, comfy_root: pathlib.Path, payload: bytes = b"image"):
        self.comfy_root = pathlib.Path(comfy_root)
        self.payload = payload
        self.graphs: list[dict[str, Any]] = []
        self.graph: dict[str, Any] | None = None
        self.calls = 0
        self._outputs: dict[str, dict[str, str]] = {}

    def _prefix_for(self, graph: dict[str, Any]) -> str:
        for node in graph.values():
            if node.get("class_type") in {"SaveImage", "PreviewImage"}:
                return str(node.get("inputs", {}).get("filename_prefix") or "image")
        return "image"

    def submit(self, graph, client_id):
        self.calls += 1
        self.graphs.append(graph)
        self.graph = graph
        prompt_id = f"prompt-{self.calls}"

        relative = pathlib.PurePosixPath(self._prefix_for(graph).replace("\\", "/"))
        subfolder = "" if str(relative.parent) == "." else str(relative.parent)
        directory = self.comfy_root / "output" / subfolder
        directory.mkdir(parents=True, exist_ok=True)

        stem = relative.name
        existing = list(directory.glob(f"{stem}_*.png"))
        filename = f"{stem}_{len(existing) + 1:05d}_.png"
        (directory / filename).write_bytes(self.payload + prompt_id.encode())

        self._outputs[prompt_id] = {"filename": filename, "subfolder": subfolder, "type": "output"}
        return prompt_id

    def poll(self, prompt_id):
        image = self._outputs.get(str(prompt_id))
        if image is None:
            return {"state": "pending", "images": [], "problems": []}
        return {"state": "done", "images": [dict(image)], "problems": []}

    def output(self, prompt_id):
        images = self.poll(prompt_id)["images"]
        return images[0] if images else None

    def interrupt(self) -> None:
        pass


class SequencedComfy(ScriptedComfy):
    """Hands back a different image for each successive submission.

    Used for the retry/quality loops, where attempt N must observe image N.
    """

    def __init__(self, paths: list[pathlib.Path]):
        super().__init__(None)
        self.paths = paths

    def submit(self, graph, client_id):
        self.calls += 1
        self.graphs.append(graph)
        self.graph = graph
        return f"prompt-{self.calls}"

    def output(self, prompt_id):
        index = int(str(prompt_id).split("-")[-1]) - 1
        if index >= len(self.paths):
            return None
        path = self.paths[index]
        return {"filename": path.name, "subfolder": "", "type": "output"}


def api_workflow(**overrides: Any) -> dict[str, Any]:
    """Canonical API-format graph: loader -> prompt -> sampler -> decode -> save."""
    graph: dict[str, Any] = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "Krea2-old.safetensors", "weight_dtype": "default"}},
        "2": {"class_type": "PrimitiveStringMultiline", "inputs": {"value": "old"}},
        "3": {"class_type": "easy stylesSelector", "inputs": {"styles": "old", "select_styles": "old", "positive": ["2", 0]}},
        "4": {"class_type": "ResolutionSelector", "inputs": {"aspect_ratio": "1:1", "megapixels": 1, "multiple": 32}},
        "5": {"class_type": "KSampler", "inputs": {"model": ["1", 0], "positive": ["3", 0], "negative": ["2", 0]}},
        "6": {"class_type": "SaveImage", "inputs": {"filename_prefix": "old", "images": ["5", 0]}},
    }
    graph.update(overrides)
    return graph


def write_workflow(path: pathlib.Path, graph: dict[str, Any]) -> pathlib.Path:
    path.write_text(json.dumps(graph, ensure_ascii=False), encoding="utf-8")
    return path


def ui_workflow(
    model: str = "Krea2-red.safetensors",
    *,
    vae: str = "qwen_image_vae.safetensors",
    clip: str = "qwen3vl_4b_fp8_scaled.safetensors",
    width: int = 768,
    height: int = 1280,
    prompt: str = "old prompt",
) -> dict[str, Any]:
    """A connected, runnable UI-format workflow.

    Shared by tests that need a workflow which actually passes the four-layer
    preflight, as opposed to a bare list of node types.
    """
    return {
        "nodes": [
            {"id": 27, "type": "SaveImage", "inputs": [
                {"name": "images", "link": 8},
                {"name": "filename_prefix", "widget": {"name": "filename_prefix"}, "link": None},
            ], "widgets_values": ["old"]},
            {"id": 28, "type": "KSampler", "inputs": [
                {"name": "model", "link": 1, "widget": None},
                {"name": "positive", "link": 2},
                {"name": "negative", "link": 3},
                {"name": "latent_image", "link": 4},
                {"name": "seed", "widget": {"name": "seed"}, "link": None},
                {"name": "steps", "widget": {"name": "steps"}, "link": None},
                {"name": "cfg", "widget": {"name": "cfg"}, "link": None},
                {"name": "sampler_name", "widget": {"name": "sampler_name"}, "link": None},
                {"name": "scheduler", "widget": {"name": "scheduler"}, "link": None},
                {"name": "denoise", "widget": {"name": "denoise"}, "link": None},
            ], "widgets_values": [1, "fixed", 8, 1, "euler", "simple", 1]},
            {"id": 29, "type": "EmptyLatentImage", "inputs": [
                {"name": "width", "link": 11},
                {"name": "height", "link": 12},
                {"name": "batch_size", "widget": {"name": "batch_size"}, "link": None},
            ], "widgets_values": [width, height, 1]},
            {"id": 30, "type": "VAEDecode", "inputs": [{"name": "samples", "link": 7}, {"name": "vae", "link": 6}]},
            {"id": 31, "type": "ConditioningZeroOut", "inputs": [{"name": "conditioning", "link": 9}]},
            {"id": 34, "type": "VAELoader", "inputs": [
                {"name": "vae_name", "widget": {"name": "vae_name"}, "link": None},
            ], "widgets_values": [vae]},
            {"id": 36, "type": "CLIPLoader", "inputs": [
                {"name": "clip_name", "widget": {"name": "clip_name"}, "link": None},
                {"name": "type", "widget": {"name": "type"}, "link": None},
                {"name": "device", "widget": {"name": "device"}, "link": None},
            ], "widgets_values": [clip, "krea2", "default"]},
            {"id": 37, "type": "UNETLoader", "inputs": [
                {"name": "unet_name", "widget": {"name": "unet_name"}, "link": None},
                {"name": "weight_dtype", "widget": {"name": "weight_dtype"}, "link": None},
            ], "widgets_values": [model, "default"]},
            {"id": 50, "type": "PrimitiveStringMultiline", "inputs": [
                {"name": "value", "widget": {"name": "value"}, "link": None},
            ], "widgets_values": [prompt]},
            {"id": 52, "type": "easy stylesSelector", "inputs": [
                {"name": "positive", "link": 10},
                {"name": "styles", "widget": {"name": "styles"}, "link": None},
                {"name": "select_styles", "widget": {"name": "select_styles"}, "link": None},
            ], "widgets_values": ["old_styles", "Old"]},
            {"id": 53, "type": "CLIPTextEncode", "inputs": [
                {"name": "text", "widget": {"name": "text"}, "link": 13},
                {"name": "clip", "link": 5},
            ], "widgets_values": [""]},
            {"id": 55, "type": "ResolutionSelector", "inputs": [
                {"name": "aspect_ratio", "widget": {"name": "aspect_ratio"}, "link": None},
                {"name": "megapixels", "widget": {"name": "megapixels"}, "link": None},
                {"name": "multiple", "widget": {"name": "multiple"}, "link": None},
            ], "widgets_values": ["16:9 (Widescreen)", 1.2, 32]},
        ],
        "links": [
            [1, 37, 0, 28, 0, "MODEL"], [2, 53, 0, 28, 1, "CONDITIONING"], [3, 31, 0, 28, 2, "CONDITIONING"],
            [4, 29, 0, 28, 3, "LATENT"], [5, 36, 0, 53, 1, "CLIP"], [6, 34, 0, 30, 1, "VAE"],
            [7, 28, 0, 30, 0, "LATENT"], [8, 30, 0, 27, 0, "IMAGE"], [9, 53, 0, 31, 0, "CONDITIONING"],
            [10, 50, 0, 52, 0, "STRING"], [11, 55, 0, 29, 0, "INT"], [12, 55, 1, 29, 1, "INT"], [13, 52, 0, 53, 0, "STRING"],
        ],
    }


def triptych(path: pathlib.Path, size: tuple[int, int] = (120, 200)) -> pathlib.Path:
    """Three identical side-by-side panels, which the subject guard flags."""
    from PIL import Image

    width, height = size
    image = Image.new("RGB", (width * 3, height))
    panel = Image.new("RGB", size, "white")
    for offset in (0, width, width * 2):
        image.paste(panel, (offset, 0))
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)
    return path


def gradient(path: pathlib.Path, size: tuple[int, int] = (360, 200)) -> pathlib.Path:
    """A non-repeating image that the subject guard must accept."""
    from PIL import Image

    width, height = size
    image = Image.new("RGB", size)
    for x in range(width):
        for y in range(height):
            image.putpixel((x, y), (x % 256, y % 256, (x + y) % 256))
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)
    return path


def png_bytes(size: tuple[int, int] = (64, 96)) -> bytes:
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", size, "white").save(buffer, format="PNG")
    return buffer.getvalue()
