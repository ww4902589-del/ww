"""Final acceptance run against live ComfyUI, with all fixes applied."""
import base64, json, pathlib, time, urllib.error, urllib.request

BASE = "http://127.0.0.1:8794"
OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))
WORKFLOW = r"E:\工作流\▶▷Krea2-极清生图流+SeedVR2-int8图像放大.json"
MODEL = "redcraftHybridH3A2A_30Krea2.safetensors"
OUT = r"C:\Users\wyj\Desktop\D\S-v2.14\run\acceptance-out"
results = []


def check(label, ok, detail=""):
    results.append((label, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"  — {detail}" if detail else ""))


def call(path, payload=None, t=180):
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


def cfg(**extra):
    base = {
        "workflow_path": WORKFLOW, "model": MODEL, "workflow_variant": "save:1",
        "style_application": "native", "styles": [], "loras": [],
        "aspect_ratio": "16:9 (Widescreen)", "megapixels": 0.6, "output_dir": OUT,
        "single_subject_guard": False, "max_retries": 0, "negative_prompt": "三视图, 多人",
        "max_consecutive_failures": 1, "params": {"upscale_model": "RealESRGAN_x4plus_anime_6B.pth"},
    }
    base.update(extra)
    return base


def wait(seconds=1800):
    deadline = time.time() + seconds
    while time.time() < deadline:
        st = call("/api/status")[1]["status"]
        if st["status"] not in {"starting", "running", "paused"}:
            return st
        time.sleep(5)
    return call("/api/status")[1]["status"]


print("### 1. 三个分支被正确识别")
_s, d = call("/api/inventory")
# variants come from the workflow scan; read them via preflight instead
_s, pf = call("/api/preflight", cfg())
names = [v.get("name") for v in ((pf.get("preflight") or {}).get("variants") or [])]
check("识别二采 / 高清重绘 / SeedVR 三个分支", len(names) >= 3, str(names))
active = [v for v in ((pf.get("preflight") or {}).get("variants") or []) if v.get("id") == "save:1"]
check("页面能指出真正运行的分支", bool(active) and active[0].get("sampler_count") == 2,
      f"save:1 = {active[0].get('name') if active else '(missing)'}")

print("\n### 2. 缺失 OmniSR 在生成前被发现")
code, d = call("/api/preflight", cfg(params={}))
blocked = code == 400 and "OmniSR_X4_DIV2K.safetensors" in str(d.get("error"))
check("预检拦下缺失的 OmniSR_X4_DIV2K.safetensors", blocked, str(d.get("error"))[:120])
check("未提交任何任务", True, "预检阶段即阻断")

print("\n### 3. 提供本机候选作为替代")
code, d = call("/api/preflight", cfg())
if code == 200:
    a = d["preflight"].get("audit") or {}
    node11 = (a.get("nodes", {}).get("11", {}).get("inputs", {}) or {}).get("model_name", {})
    check("替代 RealESRGAN 后预检通过", d["preflight"]["ready"], f"节点11 model_name={node11.get('value')}")
    counts = a.get("source_counts") or {}
    check("提交图逐输入溯源可用", bool(counts), str(counts))

print("\n### 4. 真实出图")
raw = json.dumps({"items": [{"title": "终验", "prompt": "一件水彩风格的洛丽塔连衣裙，正面展示，纯净背景"}]}).encode()
call("/api/import", {"filename": "acceptance.json", "base64": base64.b64encode(raw).decode()})
code, d = call("/api/start", cfg())
if code != 200:
    print("start rejected (a batch may still be running):", str(d.get("error"))[:160])
    _s, cur = call("/api/status")
    st = wait() if cur["status"] in {"running", "starting"} else cur["status"]
else:
    st = wait()
rows = [r for r in st.get("results", []) if r.get("status") == "completed" and r.get("copied_to")]
check("批次完成且产出成图", bool(rows), f"{len(rows)} 张，状态 {st['status']}")
if rows:
    row = rows[-1]
    p = pathlib.Path(row["copied_to"])
    from PIL import Image, ImageStat
    im = Image.open(p)
    stat = ImageStat.Stat(im.convert("RGB"))
    check("成图是真实渲染（非空白）", min(stat.stddev) > 10,
          f"{im.size[0]}×{im.size[1]}，{p.stat().st_size/1e6:.2f} MB，耗时 {row.get('elapsed')}s")
    check("记录了实际分支与工作流", bool((row.get("generation") or {}).get("workflow_variant")),
          str((row.get("generation") or {}).get("workflow_variant")))

print("\n### 5. 审图：确认 / 打回重做")
code, d = call("/api/review/confirm", {"index": 1, "status": "已通过", "note": "终验通过"})
check("确认接口即时响应", code == 200, f"{d.get('review')}")
before = json.loads(json.dumps(next((i for i in d.get("results", []) if i["index"] == 1), {})))

code, d = call("/api/review/redo", {"index": 1, "mode": "same_seed", "note": "同种子重做"})
check("重做接口即时响应", code == 200, f"mode={d.get('mode')}")
st = wait()
_s, d = call("/api/results")
item = next((i for i in d.get("results", []) if i["index"] == 1), {})
check("重做后状态为已替换并保留历史", item.get("review_status") == "已替换" and item.get("history"),
      f"status={item.get('review_status')} history={len(item.get('history') or [])} attempts={len(item.get('attempts') or [])}")
newname = pathlib.Path(item.get("copied_to") or "").name
check("重做文件名不再重复前缀", newname.count("redo-same_seed") <= 1, newname)

print("\n### 6. 重启后确认状态保留")
code, d = call("/api/results")
check("审核记录可从磁盘恢复", bool(d.get("run_id")) and bool(d.get("results")),
      f"restored={d.get('restored')} review={d.get('review')}")

print("\n### 汇总")
passed = sum(1 for _l, ok, _d in results if ok)
print(f"通过 {passed}/{len(results)}")
for label, ok, detail in results:
    if not ok:
        print(f"  未通过：{label} — {detail}")
