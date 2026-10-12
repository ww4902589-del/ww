"""Real review lifecycle: confirm, persist across restart, reject + redo."""
import json, pathlib, sys, time, urllib.error, urllib.request

BASE = "http://127.0.0.1:8792"
OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def call(path, payload=None):
    if payload is None:
        with OP.open(BASE + path, timeout=60) as r:
            return json.loads(r.read().decode("utf-8"))
    req = urllib.request.Request(BASE + path, data=json.dumps(payload).encode(),
                                 method="POST", headers={"Content-Type": "application/json"})
    try:
        with OP.open(req, timeout=600) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return json.loads(exc.read().decode("utf-8"))


print("=== A. confirm item 1 as 已通过 ===")
res = call("/api/review/confirm", {"index": 1, "status": "已通过", "note": "构图与服装细节合格"})
print("ok:", res.get("ok"), "| summary:", res.get("review"))
rev = json.loads(pathlib.Path("run/reviews").glob("*.json").__next__().read_text(encoding="utf-8")) if list(pathlib.Path("run/reviews").glob("*.json")) else {}
print("on-disk review doc run_id:", rev.get("run_id"))
print("on-disk item 1:", json.dumps(rev.get("items", {}).get("1", {}), ensure_ascii=False)[:260])

print("\n=== B. mark 需重做 and start a real redo (same_seed) ===")
res = call("/api/review/redo", {"index": 1, "mode": "same_seed", "note": "验收同种子重做"})
print("started:", res.get("ok"), "| mode:", res.get("mode"), str(res.get("error") or "")[:200])

deadline = time.time() + 1500
last = ""
while time.time() < deadline:
    st = call("/api/status")["status"]
    line = f"{st['status']} completed={st['completed']} errors={st['errors']}"
    if line != last:
        print(f"  [{time.strftime('%H:%M:%S')}] {line}")
        last = line
    if st["status"] not in {"starting", "running", "paused"}:
        break
    time.sleep(5)

print("\n=== C. after redo ===")
rev = call("/api/results")
print("summary:", rev.get("review"))
for item in rev.get("results", []):
    print("  index:", item.get("index"), "| review_status:", item.get("review_status"),
          "| attempts:", len(item.get("attempts") or []), "| history:", len(item.get("history") or []))
    print("    active:", item.get("copied_to"))
    for h in item.get("history") or []:
        print("    previous:", h.get("copied_to"), "| was:", h.get("review_status"))
    if item.get("error"):
        print("    error:", str(item["error"])[:300])
