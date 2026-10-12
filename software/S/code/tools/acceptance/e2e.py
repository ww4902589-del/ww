"""Real end-to-end acceptance run against live ComfyUI."""
import base64, json, pathlib, sys, time, urllib.error, urllib.request

BASE = "http://127.0.0.1:8792"
OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))
WORKFLOW = r"E:\工作流\▶▷Krea2-极清生图流+SeedVR2-int8图像放大.json"
MODEL = "redcraftHybridH3A2A_30Krea2.safetensors"
OUT = r"C:\Users\wyj\Desktop\D\S-v2.14\run\e2e-out"


def post(path, payload):
    req = urllib.request.Request(BASE + path, data=json.dumps(payload).encode(),
                                 method="POST", headers={"Content-Type": "application/json"})
    try:
        with OP.open(req, timeout=600) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return json.loads(exc.read().decode("utf-8"))


def get(path):
    with OP.open(BASE + path, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def config(**extra):
    base = {
        "workflow_path": WORKFLOW, "model": MODEL, "workflow_variant": "save:1",
        "style_application": "native", "styles": [], "loras": [],
        "aspect_ratio": "16:9 (Widescreen)", "megapixels": 0.6,
        "output_dir": OUT, "single_subject_guard": False, "max_retries": 0,
        "negative_prompt": "三视图, 多人", "max_consecutive_failures": 1,
    }
    base.update(extra)
    return base


print("=== 1. import one prompt ===")
raw = json.dumps({"items": [{"title": "极清验收", "prompt": "一件水彩风格的洛丽塔连衣裙，正面展示，纯净背景"}]}).encode("utf-8")
res = post("/api/import", {"filename": "acceptance.json", "base64": base64.b64encode(raw).decode()})
print("ok:", res.get("ok"), "| items:", len((res.get("bundle") or {}).get("items", [])))
if not res.get("ok"):
    print("error:", str(res.get("error"))[:400]); sys.exit(1)

print("\n=== 2. preflight WITHOUT the upscale model (expect blocked) ===")
res = post("/api/preflight", config())
print("ok:", res.get("ok"))
print("error:", str(res.get("error"))[:400])

print("\n=== 3. preflight WITH RealESRGAN replacement ===")
res = post("/api/preflight", config(params={"upscale_model": "RealESRGAN_x4plus_anime_6B.pth"}))
print("ok:", res.get("ok"))
if not res.get("ok"):
    print("error:", str(res.get("error"))[:800])
    sys.exit(1)
pf = res["preflight"]
audit = pf.get("audit") or {}
print("ready:", pf["ready"], "| audit nodes:", audit.get("node_count"),
      "| inputs:", audit.get("input_count"), "| blocking:", len(audit.get("blocking") or []))
print("source_counts:", audit.get("source_counts"))
print("upscale node 11 model_name:", (audit.get("nodes", {}).get("11", {}).get("inputs", {}).get("model_name", {}) or {}).get("value"))

print("\n=== 4. start the real batch (1 item, trial) ===")
res = post("/api/start", config(params={"upscale_model": "RealESRGAN_x4plus_anime_6B.pth"}))
print("started:", res.get("ok"), str(res.get("error") or "")[:300])

deadline = time.time() + 1500
last = ""
while time.time() < deadline:
    st = get("/api/status")["status"]
    line = f"{st['status']} submitted={st['submitted']} completed={st['completed']} errors={st['errors']} current={st.get('current')}"
    if line != last:
        print(f"  [{time.strftime('%H:%M:%S')}] {line}")
        last = line
    if st["status"] not in {"starting", "running", "paused"}:
        break
    time.sleep(5)

print("\n=== 5. result ===")
st = get("/api/status")["status"]
print("final status:", st["status"], "| aborted_reason:", st.get("aborted_reason"))
for row in st.get("results", []):
    print("  index:", row.get("index"), "| status:", row.get("status"),
          "| elapsed:", row.get("elapsed"), "| copied_to:", row.get("copied_to"))
    if row.get("error"):
        print("    error:", str(row["error"])[:400])
    if row.get("copied_to"):
        p = pathlib.Path(row["copied_to"])
        print("    file exists:", p.exists(), "| bytes:", p.stat().st_size if p.exists() else 0)

print("\n=== 6. review state ===")
rev = get("/api/results")
print("review summary:", rev.get("review"))
for item in rev.get("results", []):
    print("  ", item.get("index"), item.get("review_status"), item.get("available"), item.get("copied_to"))
