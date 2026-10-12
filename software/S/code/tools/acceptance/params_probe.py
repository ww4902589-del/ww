"""Real proof that a workbench value reaches ComfyUI: it changes the output size."""
import base64, json, pathlib, time, urllib.error, urllib.request

BASE = "http://127.0.0.1:8803"
OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))
WORKFLOW = r"E:\工作流\▶▷Krea2-极清生图流+SeedVR2-int8图像放大.json"
OUT = r"C:\Users\wyj\Desktop\D\S-v2.14\run\param-out"
BASELINE = (3168, 1776)


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
        "seed": 123456,
    }
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


print("### 1. 读取参数工作台定义")
params = call("/api/params")["params"]
groups = {g["id"]: g for g in params["groups"]}
print("    常用参数:", [p["key"] for p in groups["common"]["params"]])
print("    高级参数:", [p["key"] for p in groups["advanced"]["params"]])
sampler = next(p for p in groups["advanced"]["params"] if p["key"] == "sampler_name")
print("    采样器候选（真实 schema）:", (sampler.get("options") or [])[:3], "... 共", len(sampler.get("options") or []))

print("\n### 2. 保存工作流默认值：放大倍数 2.0 -> 1.0")
r = call("/api/params/apply", {"workflow_path": WORKFLOW, "scope": "workflow",
                               "params": {"upscale_by": 1.0, "steps_stage1": 6}})
print("    ok:", r.get("ok"), "| 已保存:", r.get("params", {}).get("workflow_defaults"))

print("\n### 3. 预检（不传任何 params，默认值应自动生效）")
pf = call("/api/preflight", cfg())
print("    ok:", pf.get("ok"))
audit = (pf.get("preflight") or {}).get("audit") or {}
node3 = (audit.get("nodes", {}).get("3", {}).get("inputs", {}) or {})
node21 = (audit.get("nodes", {}).get("21", {}).get("inputs", {}) or {})
print("    节点3 放大倍数:", node3.get("upscale_by"))
print("    节点21 步数:", node21.get("steps"))
print("    来源为参数工作台的输入:", [(o["node_id"], o["input"], o["value"])
      for o in (audit.get("overrides") or []) if o["source"] == "param"])

print("\n### 4. 真实生成（放大倍数 1.0）")
raw = json.dumps({"items": [{"title": "参数验证", "prompt": "一件水彩风格的洛丽塔连衣裙，正面展示"}]}).encode()
call("/api/import", {"filename": "p.json", "base64": base64.b64encode(raw).decode()})
print("    start:", call("/api/start", cfg()).get("ok"))
st = wait("生成")
row = next((r for r in st.get("results", []) if r.get("status") == "completed"), {})
print("    耗时:", row.get("elapsed"), "秒")

print("\n### 5. 结果对比")
res = call("/api/results")["review"]
item = next((i for i in res["results"] if i["index"] == 1), {})
final = item.get("final_dimensions") or {}
print("    最终尺寸:", final, "| 基线（未改放大倍数）:", {"width": BASELINE[0], "height": BASELINE[1]})
print("    尺寸确实变小:", final.get("width", 0) < BASELINE[0])
print("    卡片会显示:", item.get("base_dimensions"), "->", final, "放大", item.get("upscale_factor"))
print("    种子:", (item.get("generation") or {}).get("seeds"))

print("\n### 6. 清理工作流默认值")
r = call("/api/params/clear", {"workflow_path": WORKFLOW})
print("    已清空:", r.get("params", {}).get("workflow_defaults"))
pf = call("/api/preflight", cfg())
audit = (pf.get("preflight") or {}).get("audit") or {}
print("    清理后节点3 放大倍数回到:", (audit.get("nodes", {}).get("3", {}).get("inputs", {}) or {}).get("upscale_by"))
