"""Record real ComfyUI data into tests/fixtures so the suite runs offline.

Usage::

    python tools/record_fixtures.py [--comfy-url http://127.0.0.1:8188]

Records:
  object_info.json          full /object_info node schema
  upscale_models.json       /models/upscale_models listing
  loras.json                /models/loras listing
  workflows_*.json          copies of the reference workflows on disk
  http400_*.json            real 400 bodies captured by submitting invalid graphs

The 400 captures are the important ones: the old ComfyClient discarded
``HTTPError.read()``, so the 45-failure incident in the handover package could
only report ``HTTP Error 400: Bad Request`` with no cause. These fixtures pin
the real response shape that ErrorTranslator must explain.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import urllib.error
import urllib.request

PACKAGE_ROOT = pathlib.Path(__file__).resolve().parents[1]
FIXTURE_ROOT = PACKAGE_ROOT / "tests" / "fixtures"

REFERENCE_WORKFLOWS = [
    pathlib.Path(r"E:\工作流\▶▷Krea2-极清生图流+SeedVR2-int8图像放大.json"),
    pathlib.Path(r"E:\工作流\▶▷Krea2-高清生图优化流(整合).json"),
    pathlib.Path(r"E:\工作流\▶▷Krea2-风格扩展流（397种）.json"),
]


def opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def request_json(url: str, payload: dict | None = None, timeout: int = 60) -> dict:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="GET" if payload is None else "POST", headers={"Content-Type": "application/json"})
    with opener().open(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def capture_error(url: str, payload: dict, timeout: int = 60) -> dict:
    """POST and return the parsed error body instead of raising."""
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url + "/prompt", data=body, method="POST", headers={"Content-Type": "application/json"})
    try:
        with opener().open(request, timeout=timeout) as response:
            return {"status": response.status, "body": json.loads(response.read().decode("utf-8"))}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
        return {"status": exc.code, "raw": raw, "body": parsed}


def write(name: str, payload) -> None:
    FIXTURE_ROOT.mkdir(parents=True, exist_ok=True)
    path = FIXTURE_ROOT / name
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    size = path.stat().st_size
    print(f"  wrote {name}  ({size} B)" + (f" = {size / 1024:.0f} KB" if size >= 4096 else ""))


def first_of(kind: str) -> str:
    """First entry of a recorded model listing, used to build valid captures."""
    path = FIXTURE_ROOT / f"models_{kind}.json"
    if not path.exists():
        return ""
    values = json.loads(path.read_text(encoding="utf-8"))
    return str(values[0]) if values else ""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--comfy-url", default="http://127.0.0.1:8188")
    args = parser.parse_args()
    base = args.comfy_url.rstrip("/")

    print(f"probing {base} ...")
    stats = request_json(base + "/system_stats", timeout=10)
    print(f"  reachable: {json.dumps(stats.get('system', {}), ensure_ascii=False)}")

    print("recording node schema")
    object_info = request_json(base + "/object_info")
    write("object_info.json", object_info)
    print(f"  {len(object_info)} node types")
    for name in ("UltimateSDUpscale", "KSamplerAdvanced", "easy stylesSelector", "SeedVR2-7/3b-int8图像放大"):
        print(f"    {'OK ' if name in object_info else 'MISSING'} {name}")

    for label, endpoint in (("upscale_models", "/models/upscale_models"), ("loras", "/models/loras"), ("checkpoints", "/models/checkpoints"), ("diffusion_models", "/models/diffusion_models")):
        try:
            write(f"models_{label}.json", request_json(base + endpoint, timeout=30))
        except Exception as exc:  # noqa: BLE001 - recording is best-effort
            print(f"  skip {label}: {exc}")

    print("copying reference workflows")
    for path in REFERENCE_WORKFLOWS:
        if not path.exists():
            print(f"  missing {path}")
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        slug = path.stem.replace("▶▷", "").replace("（", "_").replace("）", "").replace("(", "_").replace(")", "").replace("+", "_")
        write(f"workflow_{slug}.json", payload)

    print("capturing real 400 bodies")
    # A node type that is not installed at all.
    write("http400_unknown_node.json", capture_error(base, {
        "prompt": {"1": {"class_type": "ComfyBatchDefinitelyNotANode", "inputs": {}}},
        "client_id": "comfybatch-fixture-recorder",
    }))
    # The real OmniSR case: an upscale model the workflow references but that is
    # not installed. This is the single most valuable fixture -- it carries the
    # input name, the offending value AND the legal candidate list.
    write("http400_missing_upscale_model.json", capture_error(base, {
        "prompt": {
            "1": {"class_type": "UpscaleModelLoader", "inputs": {"model_name": "OmniSR_X4_DIV2K.safetensors"}},
            "2": {"class_type": "EmptyImage", "inputs": {"width": 64, "height": 64, "batch_size": 1, "color": 0}},
            "3": {"class_type": "ImageUpscaleWithModel", "inputs": {"upscale_model": ["1", 0], "image": ["2", 0]}},
            "4": {"class_type": "SaveImage", "inputs": {"filename_prefix": "comfybatch-fixture", "images": ["3", 0]}},
        },
        "client_id": "comfybatch-fixture-recorder",
    }))
    # A combo value outside the declared candidate list, on a non-resource input,
    # so the translator must categorise it as a range problem rather than a file.
    checkpoint = first_of("checkpoints")
    if checkpoint:
        write("http400_value_not_in_list.json", capture_error(base, {
            "prompt": {
                "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": checkpoint}},
                "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "x", "clip": ["1", 1]}},
                "3": {"class_type": "EmptyLatentImage", "inputs": {"width": 64, "height": 64, "batch_size": 1}},
                "4": {"class_type": "KSampler", "inputs": {
                    "model": ["1", 0], "positive": ["2", 0], "negative": ["2", 0], "latent_image": ["3", 0],
                    "seed": 1, "steps": 8, "cfg": 1.0,
                    "sampler_name": "definitely_not_a_sampler", "scheduler": "simple", "denoise": 1.0,
                }},
                "5": {"class_type": "VAEDecode", "inputs": {"samples": ["4", 0], "vae": ["1", 2]}},
                "6": {"class_type": "SaveImage", "inputs": {"filename_prefix": "comfybatch-fixture", "images": ["5", 0]}},
            },
            "client_id": "comfybatch-fixture-recorder",
        }))
    else:
        print("  skip value_not_in_list: no checkpoint installed")
    # A missing required input, which is a distinct validation type.
    write("http400_missing_input.json", capture_error(base, {
        "prompt": {
            "1": {"class_type": "EmptyImage", "inputs": {"width": 64, "height": 64, "batch_size": 1, "color": 0}},
            "2": {"class_type": "ImageScale", "inputs": {"image": ["1", 0], "upscale_method": "nearest-exact"}},
            "3": {"class_type": "SaveImage", "inputs": {"filename_prefix": "comfybatch-fixture", "images": ["2", 0]}},
        },
        "client_id": "comfybatch-fixture-recorder",
    }))
    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
