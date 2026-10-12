"""Real lifecycle: restart recovery -> run -> confirm -> same-seed redo."""
import base64, json, pathlib, sys, time, urllib.error, urllib.request

BASE = "http://127.0.0.1:8793"
OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))
WORKFLOW = r"E:\工作流\▶▷Krea2-极清生图流+SeedVR2-int8图像放大.json"
MODEL = "redcraftHybridH3A2A_30Krea2.safetensors"
OUT = r"C:\Users\wyj\Desktop\D\S-v2.14\run\e2e-out"


def call(path, payload=None, t=120):
    try:
        if payload is None:
            with OP.open(BASE + path, timeout=t) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        req = urllib.request.Request(BASE + path, data=json.dumps(payload).encode(),
                                     method="POST", headers={"Content-Type": "application/json"})
        with OP.open(req, timeout=t) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def config(**extra):
    base = {
        "workflow_path": WORKFLOW, "model": MODEL, "workflow_variant": "save:1",
        "style_application": "native", "styles": [], "loras": [],
        "aspect_ratio": "16:9 (Widescreen)", "megapixels": 0.6,
        "output_dir": OUT, "single_subject_guard": False, "max_retries": 0,
        "negative_prompt": "三视图, 多人", "max_consecutive_failures": 1,
        "params": {"upscale_model": "RealESRGAN_x4plus_anime_6B.enabled"},
    }
    base["params"] = {"upscale_model": "RealESRGAN_x4plus_anime_6B.pth"}
    base.update(extra)
    return base


def wait(label):
    deadline = time.time() + 1800
    last = ""
    while time.time() < deadline:
        _s, d = call("/api/status")
        st = d["status"]
        line = f"{st['status']} completed={st['completed']} errors={st['errors']}"
        if line != last:
            print(f"  [{time.strftime('%H:%M:%S')}] {label}: {line}")
            last = line
        if st["status"] not in {"starting", "running", "paused"}:
            return st
        time.sleep(5)
    return call("/api/status")[1]["status"]


print("=== A. restart recovery: previous run should still be visible ===")
_s, d = call("/api/results")
print("recovered run_id:", d.get("run_id") or "(none)")
print("restored:", d.get("restored"), "| items:", len(d.get("results") or []))
print("review:", d.get("review"))
for item in d.get("results") or []:
    print("   ", item.get("index"), item.get("review_status"), "| available:", item.get("available"),
          "| restored:", item.get("restored"))
    print("    image:", item.get("copied_to"))

print("\n=== B. confirm the recovered image (must answer instantly) ===")
started = time.time()
code, d = call("/api/review/confirm", {"index": 1, "status": "已通过", "note": "重启后仍可确认"})
print("confirm:", code, f"{time.time() - started:.3f}s", "| review:", d.get("review"))

print("\n=== C. new run so redo has a bundle/config ===")
raw = json.dumps({"items": [{"title": "重做验收", "prompt": "一件水彩风格的洛丽塔连衣裙，正面展示"}]}).encode("utf-8")
call("/api/import", {"filename": "redo.json", "base64": base64.b64encode(raw).decode("ascii")})
code, d = call("/api/start", config())
print("start:", code, d.get("ok"), str(d.get("error") or "")[:200])
st = wait("run")
print("run final:", st["status"])
for row in st.get("results", []):
    print("   ", row.get("index"), row.get("status"), row.get("copied_to"), str(row.get("error") or "")[:160])

print("\n=== D. same_seed redo of that item ===")
code, d = call("/api/review/redo", {"index": 1, "mode": "same_seed", "note": "同种子重做验证"})
print("redo:", code, d.get("ok"), d.get("mode"), str(d.get("error") or "")[:200])
st = wait("redo")
print("redo final:", st["status"])

print("\n=== E. review state after redo ===")
_s, d = call("/api/results")
print("summary:", d.get("review"))
for item in d.get("results") or []:
    print("   ", item.get("index"), "| review_status:", item.get("review_status"),
          "| attempts:", len(item.get("attempts") or []), "| history:", len(item.get("history") or []))
    print("    active  :", item.get("copied_to"), "| exists:", pathlib.Path(item.get("copied_to") or "").exists())
    for h in item.get("history") or []:
        print("    previous:", h.get("copied_to"), "| was:", h.get("review_status"))
