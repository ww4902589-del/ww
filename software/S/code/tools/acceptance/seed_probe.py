"""Answer: after a reject/redo, is the seed fixed? Answered with real runs.

Pins a known seed, generates once, then compares the seeds and pixels produced by
``same_seed`` and ``new_seed`` redos.
"""
import base64, json, pathlib, time, urllib.error, urllib.request

BASE = "http://127.0.0.1:8797"
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
        "resource_overrides": {"11": {"model_name": "RealESRGAN_x4plus_anime_6B.pth"}},
        "seed": PINNED,
    }
    base.update(extra)
    return base


def wait(label):
    deadline = time.time() + 1800
    seen = ""
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
    _s, d = call("/api/results")
    review = d["review"]
    item = next((i for i in review["results"] if i["index"] == 1), {})
    return {
        "seeds": (item.get("generation") or {}).get("seeds") or {},
        "copied_to": item.get("copied_to"),
        "attempts": len(item.get("attempts") or []),
        "review_status": item.get("review_status"),
    }


def pixels(path):
    from PIL import Image
    with Image.open(path) as im:
        return im.convert("RGB").tobytes(), im.size


print(f"### 0. 导入提示词，批次指定种子 seed={PINNED}")
raw = json.dumps({"items": [{"title": "种子验证", "prompt": "一件水彩风格的洛丽塔连衣裙，正面展示"}]}).encode()
call("/api/import", {"filename": "seed.json", "base64": base64.b64encode(raw).decode()})

print("\n### 1. 首次生成（指定种子）")
print("    start:", call("/api/start", cfg()).get("ok"))
wait("run")
first = shown()
print("    提交图种子:", first["seeds"])
print("    输出:", pathlib.Path(first["copied_to"] or "").name)

print(f"\n### 2. same_seed 重做（应保持 seed={PINNED}）")
print("    redo:", call("/api/review/redo", {"index": 1, "mode": "same_seed"}).get("ok"))
wait("same_seed")
second = shown()
print("    提交图种子:", second["seeds"])
print("    输出:", pathlib.Path(second["copied_to"] or "").name)

print("\n### 3. new_seed 重做（应为新随机种子）")
print("    redo:", call("/api/review/redo", {"index": 1, "mode": "new_seed"}).get("ok"))
wait("new_seed")
third = shown()
print("    提交图种子:", third["seeds"])
print("    输出:", pathlib.Path(third["copied_to"] or "").name)

print("\n### 4. 结论")
print("    首次        :", first["seeds"])
print("    same_seed   :", second["seeds"])
print("    new_seed    :", third["seeds"])
same_fixed = first["seeds"] == second["seeds"]
seed_changed = third["seeds"] != second["seeds"]
print(f"    same_seed 种子与首次一致      : {same_fixed}")
print(f"    new_seed  种子与 same_seed 不同: {seed_changed}")

try:
    a, size_a = pixels(first["copied_to"])
    b, _ = pixels(second["copied_to"])
    c, _ = pixels(third["copied_to"])
    print(f"\n    像素对比（{size_a[0]}×{size_a[1]}）：")
    print(f"      首次 vs same_seed 完全相同 : {a == b}")
    print(f"      首次 vs new_seed  不同     : {a != c}")
except Exception as exc:
    print("    像素对比失败:", exc)
