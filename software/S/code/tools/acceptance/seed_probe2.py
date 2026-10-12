"""Continue the seed probe from the existing run: same_seed vs new_seed."""
import json, pathlib, time, urllib.error, urllib.request

BASE = "http://127.0.0.1:8798"
OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


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
    d = call("/api/results")["review"]
    item = next((i for i in d["results"] if i["index"] == 1), {})
    return {
        "seeds": (item.get("generation") or {}).get("seeds") or {},
        "copied_to": item.get("copied_to") or "",
        "attempts": len(item.get("attempts") or []),
        "history": len(item.get("history") or []),
        "review_status": item.get("review_status"),
    }


def pixels(path):
    from PIL import Image
    with Image.open(path) as im:
        return im.convert("RGB").tobytes(), im.size


first = shown()
print("### 1. 首次生成（批次指定 seed=123456）")
print("    提交图种子:", first["seeds"], "| 输出:", pathlib.Path(first["copied_to"]).name)

print("\n### 2. same_seed 重做（应保持 123456）")
print("    redo:", call("/api/review/redo", {"index": 1, "mode": "same_seed"}).get("ok"))
wait("same_seed")
second = shown()
print("    提交图种子:", second["seeds"], "| 输出:", pathlib.Path(second["copied_to"]).name)

print("\n### 3. new_seed 重做（应为新随机种子）")
print("    redo:", call("/api/review/redo", {"index": 1, "mode": "new_seed"}).get("ok"))
wait("new_seed")
third = shown()
print("    提交图种子:", third["seeds"], "| 输出:", pathlib.Path(third["copied_to"]).name)

print("\n### 4. 显式钉种子重做（pin=777）")
print("    redo:", call("/api/review/redo", {"index": 1, "mode": "new_seed", "seed": 777}).get("ok"))
wait("pinned")
fourth = shown()
print("    提交图种子:", fourth["seeds"], "| 输出:", pathlib.Path(fourth["copied_to"]).name)

print("\n### 5. 结论")
print("    首次            :", first["seeds"])
print("    same_seed       :", second["seeds"])
print("    new_seed        :", third["seeds"])
print("    new_seed + pin777:", fourth["seeds"])
print()
print("    same_seed 与首次种子一致        :", first["seeds"] == second["seeds"])
print("    new_seed 与 same_seed 种子不同  :", third["seeds"] != second["seeds"])
print("    pin777 写入提交图               :", 777 in set((fourth["seeds"] or {}).values()))

try:
    a, size = pixels(first["copied_to"])
    b, _ = pixels(second["copied_to"])
    c, _ = pixels(third["copied_to"])
    print(f"\n    像素对比（{size[0]}×{size[1]}）：")
    print("      首次 vs same_seed 完全相同 :", a == b)
    print("      首次 vs new_seed  不同     :", a != c)
    print("    版本记录：attempts", fourth["attempts"], "history", fourth["history"], "| 状态", fourth["review_status"])
except Exception as exc:
    print("    像素对比失败:", exc)
