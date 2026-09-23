"""The single seam through which all ComfyUI I/O passes.

Two behaviours here were directly responsible for the 45-failure incident:

1. ``urllib`` raises :class:`HTTPError` for any 4xx/5xx and the old client never
   called ``.read()``, so ComfyUI's 400 body -- node id, offending value, legal
   candidates -- was thrown away. Every item then reported the useless
   ``HTTP Error 400: Bad Request``.
2. ``/history/<id>`` was only inspected for images. When a node raised inside
   ComfyUI, history carried ``status.status_str == "error"`` and no images, which
   looked identical to "still running", so each failed item span for the full
   1800-second deadline before giving up.

``ComfyGateway`` keeps the raw body as a :class:`ComfyError` and reports
execution failure the moment history says so. ``FakeComfyGateway`` implements the
same interface in memory so the whole suite runs without a live ComfyUI.
"""

from __future__ import annotations

import json
import pathlib
import urllib.error
import urllib.request
from typing import Any, Protocol

from comfybatch_errors import ComfyError, ErrorTranslator, Problem


class ComfyGateway(Protocol):
    """Everything the batch runner is allowed to know about ComfyUI."""

    def object_info(self, *, refresh: bool = False) -> dict[str, Any]: ...
    def models(self, folder: str) -> list[str]: ...
    def submit(self, graph: dict[str, Any], client_id: str, *, timeout: float = 60) -> str: ...
    def poll(self, prompt_id: str, *, timeout: float = 30) -> dict[str, Any]: ...
    def interrupt(self) -> None: ...


class ProductionGateway:
    """Real ComfyUI over HTTP, with the response body preserved on failure."""

    def __init__(self, base_url: str = "http://127.0.0.1:8188"):
        self.base_url = base_url.rstrip("/")
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self._object_info: dict[str, Any] | None = None
        self._models: dict[str, list[str]] = {}

    # ---------------------------------------------------------------- transport

    def request(self, path: str, method: str = "GET", payload: dict[str, Any] | None = None, timeout: int = 30) -> Any:
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + path, data=body, method=method, headers={"Content-Type": "application/json"}
        )
        try:
            with self.opener.open(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = None
            raise ComfyError(
                f"HTTP {exc.code} {path}",
                status=exc.code,
                raw=raw,
                body=parsed,
                path=path,
            ) from None
        except urllib.error.URLError as exc:
            raise ComfyError(f"无法连接 ComfyUI：{exc.reason}", status=0, path=path) from None

    # ----------------------------------------------------------------- queries

    def system_stats(self) -> dict[str, Any]:
        return self.request("/system_stats", timeout=6)

    def object_info(self, *, refresh: bool = False) -> dict[str, Any]:
        if self._object_info is None or refresh:
            self._object_info = self.request("/object_info", timeout=60)
        return self._object_info

    def models(self, folder: str) -> list[str]:
        """Authoritative merged resource list for one model folder.

        ``/models/{folder}`` is preferred over scanning the filesystem because
        ComfyUI merges ``extra_model_paths.yaml`` into it. On this machine that
        matters: the LoRA root is ``E:/模型（放入models）/loras``, and the API
        returns subfolder-relative names such as
        ``Krea2-功能\\QuadView_krea2_v1.safetensors`` -- exactly the string
        ``lora_name`` needs.
        """
        if folder not in self._models:
            try:
                values = self.request(f"/models/{folder}", timeout=30)
            except ComfyError:
                values = []
            self._models[folder] = [str(item) for item in values] if isinstance(values, list) else []
        return self._models[folder]

    def all_models(self) -> dict[str, list[str]]:
        inventory: dict[str, list[str]] = {}
        for folder in (
            "checkpoints", "diffusion_models", "loras", "vae", "text_encoders",
            "clip", "upscale_models", "controlnet", "style_models", "latent_upscale_models",
        ):
            found = self.models(folder)
            if found:
                inventory[folder] = found
        return inventory

    # ------------------------------------------------------------- execution

    def submit(self, graph: dict[str, Any], client_id: str, *, timeout: float = 60) -> str:
        result = self.request("/prompt", "POST", {"prompt": graph, "client_id": client_id}, timeout=timeout)
        if not isinstance(result, dict):
            raise ComfyError("ComfyUI 返回了无法解析的提交结果", status=0, body=result)
        if result.get("node_errors"):
            raise ComfyError(
                "ComfyUI 校验未通过",
                status=400,
                raw=json.dumps(result, ensure_ascii=False),
                body=result,
                path="/prompt",
            )
        if not result.get("prompt_id"):
            # Previously this surfaced as a bare KeyError('prompt_id').
            raise ComfyError(
                "ComfyUI 未返回 prompt_id",
                status=0,
                raw=json.dumps(result, ensure_ascii=False),
                body=result,
                path="/prompt",
            )
        return str(result["prompt_id"])

    def poll(self, prompt_id: str, *, timeout: float = 30) -> dict[str, Any]:
        """One history probe, normalised.

        Returns ``{"state": "pending"|"done"|"error", "images": [...],
        "problems": [...], "entry": {...}}`` so the runner never has to guess
        whether "no images yet" means running or dead.
        """
        history = self.request("/history/" + prompt_id, timeout=timeout)
        entry = (history or {}).get(prompt_id)
        if not entry:
            return {"state": "pending", "images": [], "problems": [], "entry": {}}

        status = entry.get("status") or {}
        status_str = str(status.get("status_str") or "")
        images: list[dict[str, Any]] = []
        for output in (entry.get("outputs") or {}).values():
            for image in (output.get("images") or []):
                if isinstance(image, dict):
                    images.append(image)

        if status_str == "error":
            return {
                "state": "error",
                "images": images,
                "problems": ErrorTranslator.from_history(prompt_id, entry),
                "entry": entry,
            }
        if images:
            return {"state": "done", "images": images, "problems": [], "entry": entry}
        # Text-only output nodes have no images. A successful history entry is
        # still complete; otherwise image-to-prompt waits until its deadline.
        if status_str == "success":
            return {"state": "done", "images": [], "problems": [], "entry": entry}
        return {"state": "pending", "images": [], "problems": [], "entry": entry}

    def interrupt(self) -> None:
        try:
            self.request("/interrupt", "POST", {}, timeout=15)
        except ComfyError:
            pass


class FakeComfyGateway:
    """In-memory gateway so every test runs offline and deterministically.

    Mirrors the observable contract of :class:`ProductionGateway`: same return
    shapes, same error types. ``fail_with`` makes the next submit raise an
    arbitrary :class:`ComfyError`, which is how the batch stop-loss tests drive
    workflow-level failures.
    """

    def __init__(
        self,
        *,
        object_info: dict[str, Any] | None = None,
        models: dict[str, list[str]] | None = None,
        output_dir: pathlib.Path | None = None,
        payload: bytes = b"image",
        fail_with: ComfyError | None = None,
        execution_error: list[Problem] | None = None,
    ):
        self._object_info = object_info or {}
        self._models = models or {}
        self.output_dir = output_dir
        self.payload = payload
        self.fail_with = fail_with
        self.execution_error = execution_error
        self.submitted: list[dict[str, Any]] = []
        self.graphs: list[dict[str, Any]] = []
        self.interrupted = False
        self.fail_times = 0

    # ------------------------------------------------------------------ setup

    @property
    def graph(self) -> dict[str, Any] | None:
        return self.graphs[-1] if self.graphs else None

    def make_output(self, name: str = "out.png") -> pathlib.Path | None:
        if self.output_dir is None:
            return None
        self.output_dir.mkdir(parents=True, exist_ok=True)
        path = self.output_dir / name
        path.write_bytes(self.payload)
        return path

    # --------------------------------------------------------------- protocol

    def object_info(self, *, refresh: bool = False) -> dict[str, Any]:
        return self._object_info

    def models(self, folder: str) -> list[str]:
        return list(self._models.get(folder) or [])

    def all_models(self) -> dict[str, list[str]]:
        return {folder: list(values) for folder, values in self._models.items() if values}

    def submit(self, graph: dict[str, Any], client_id: str, *, timeout: float = 60) -> str:
        if self.fail_with is not None:
            self.fail_times += 1
            raise self.fail_with
        self.submitted.append(graph)
        self.graphs.append(graph)
        self.make_output(f"out{len(self.submitted)}.png")
        return f"prompt-{len(self.submitted)}"

    def poll(self, prompt_id: str, *, timeout: float = 30) -> dict[str, Any]:
        if self.execution_error is not None:
            return {"state": "error", "images": [], "problems": list(self.execution_error), "entry": {}}
        if self.output_dir is None:
            return {"state": "pending", "images": [], "problems": [], "entry": {}}
        index = int(str(prompt_id).split("-")[-1])
        path = self.output_dir / f"out{index}.png"
        if not path.exists():
            return {"state": "pending", "images": [], "problems": [], "entry": {}}
        return {
            "state": "done",
            "images": [{"filename": path.name, "subfolder": "", "type": "output"}],
            "problems": [],
            "entry": {},
        }

    def interrupt(self) -> None:
        self.interrupted = True
