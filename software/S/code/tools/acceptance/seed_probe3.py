"""Definitive seed answer: run with a pinned seed, then compare all redo modes."""
import base64, json, pathlib, time, urllib.error, urllib.request

BASE = "http://127.0.0.1:8798"
OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))
WORKFLOW = r"E:\工作流\▶▷Krea2-极清生图流+SeedVR2-int8图像放大.json"
OUT = r"C:\Users\wyj\Desktop\D\S-v2.14\run\seed-probe"
PINNED = 123456


def call(path, payload=None, t=180):
    try:
        if payload is None:
            with OP.open(BASE + path, timeout=t) as r:
                return json.loads(r.read().decode("utf-8"))
        req = urllib.request.Request(BASE + path, data=json.dumps(payload).encode(),
                                     method="POST", headers={"Content-Type": "application/json"})
        with OP.open(req, timeout=t) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return json.loads(exc.read().decode("utf-8"))


def cfg(**extra):
    base = {
        "workflow_path": WORKFLOW, "model": "redcraftHybridH3A2A_30Krea2.safetensors",
        "workflow_variant": "save:1", "style_application": "native", "styles": [], "loras": [],
        "aspect_ratio": "16:9 (Widescreen)", "megapixels": 0.6, "output_dir": OUT,
        "single_subject_guard": False, "max_retries": 0, "max_consecutive_failures": 1,
        "resource_overrides": {"11": {"model_name": "RealESRGAN_x4plus_anime_6B.enabled"}},
        "seed": PINNED,
    }
    base["resource_overrides"] = {"11": {"model_name": "RealESRGAN_x4plus_anime_6B.pth"}}
    base.update(extra)
    return base


def wait(label):
    deadline, seen = time.time() + 1800, ""
    while time.time() < deadline:
        st = call("/api/status")["status"]
        line = f"{st['status']} completed={st['completed']} errors={st['errors']}"
        if line != seen:
            print(f"    [{time.strftime('%H:%M:%S')}] {label}: {line}")
            seen = line
        if st["status"] not in {"starting", "running", "paused"}:
            return st
        time.sleep(5)
    return call("/api/status")["status"]


def shown():
    d = call("/api/results")["review"]
    item = next((i for i in d["results"] if i["index"] == 1), {})
    return {
        "seeds": (item.get("generation") or {}).get("seeds") or {},
        "file": pathlib.Path(item.get("copied_to") or "").name,
        "path": item.get("copied_to") or "",
        "attempts": len(item.get("attempts") or []),
        "history": len(item.get("history") or []),
        "status": item.get("review_status"),
    }


def pixels(path):
    from PIL import Image
    with Image.open(path) as im:
        return im.convert("RGB").tobytes(), im.size


raw = json.dumps({"items": [{"title": "种子验证2", "prompt": "一件水彩风格的洛丽塔连衣裙，正面展示"}]}).encode()
call("/api/import", {"filename": "seed.json", "base64": base64.b64encode(raw).decode()})

print(f"### 1. 首次生成，批次指定 seed={PINNED}")
print("    start:", call("/api/start", cfg()).get("ok"))
wait("首次")
first = shown()
print("    种子:", first["seeds"], "|", first["file"], "| 状态", first["status"])

print("\n### 2. same_seed 重做（预期：种子不变）")
call("/api/review/redo", {"index": 1, "mode": "same_seed"})
wait("same_seed")
second = shown()
print("    种子:", second["seeds"], "|", second["file"])

print("\n### 3. new_seed 重做（预期：换新随机种子）")
call("/api/review/redo", {"index": 1, "mode": "new_seed"})
wait("new_seed")
third = shown()
print("    种子:", third["seeds"], "|", third["file"])

print("\n### 4. 显式钉种子重做 seed=777（预期：写入 777）")
call("/api/review/redo", {"index": 1, "mode": "new_seed", "seed": 777})
wait("pin777")
fourth = shown()
print("    种子:", fourth["seeds"], "|", fourth["file"])

print("\n### 5. 结论")
print("    首次              :", first["seeds"])
print("    same_seed         :", second["seeds"])
print("    new_seed          :", third["seeds"])
print("    new_seed + pin777 :", fourth["seeds"])
print()
print("    same_seed 与首次一致            :", first["seeds"] == second["seeds"])
print("    new_seed 与 same_seed 不同      :", third["seeds"] != second["seeds"])
print("    pin777 写入提交图               :", 777 in set(fourth["seeds"].values()))
print("    版本记录 attempts/history       :", fourth["attempts"], "/", fourth["history"])

try:
    a, size = pixels(first["path"])
    b, _ = pixels(second["path"])
    c, _ = pixels(third["path"])
    d, _ = pixels(fourth["path"])
    print(f"\n    像素对比（{size[0]}×{size[1]}）：")
    print("      首次 vs same_seed 完全相同 :", a == b)
    print("      首次 vs new_seed  不同     :", a != c)
    print("      首次 vs pin777    不同     :", a != d)
except Exception as exc:
    print("    像素对比失败:", exc)
