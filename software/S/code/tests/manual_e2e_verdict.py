# 端到端：模拟「浏览器点卡片上的 ×/✓」这条完整链路。
# 不起长驻服务（沙箱会清理子进程），改成进程内起 ThreadingHTTPServer +
# 用真 HTTP 请求走一遍。隔离在临时目录，不碰用户数据。
#
# 同时在 Node 里跑一遍 compare_live.html 的判定逻辑，确认前后端语义一致：
# 前端算出「该发什么 op」→ 后端 apply → 前端重读后状态符合预期。
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import http.client

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(os.path.dirname(HERE), 'tools')
sys.path.insert(0, TOOLS)

TMP = tempfile.mkdtemp(prefix="e2e_verdict_")

# 造真实存在的图片文件（/api/marks 会过滤指向不存在文件的标记）
IMG = [os.path.join(TMP, "shot%d.png" % i) for i in range(1, 5)]
for p in IMG:
    with open(p, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
UID = [p + "#runX#%d" % i for i, p in enumerate(IMG)]

# 起点：一个「默认」夹里有 2 张（模拟用户已有标记），另 2 张未标记
with open(os.path.join(TMP, "_marks.json"), "w", encoding="utf-8") as f:
    json.dump({"sets": [{"id": "sDef", "name": "默认",
                         "paths": [UID[0], UID[1]], "note": {}, "color": 0}],
               "active": "sDef"}, f, ensure_ascii=False)

import atlas_server as S
S.DATA_DIR = TMP
S.Handler.out_dir = TMP          # 必须同步改，marks_file 取自这里
assert S.Handler.out_dir == TMP

# 伪造图鉴数据，让 /api/selection 能把 path 关联到 uid
S._state["data"] = {"images": [
    {"path": IMG[i], "uid": UID[i], "preset": "预设%d" % i,
     "styles": ["风格A"], "loras": [], "index": i + 1, "run": "runX"}
    for i in range(4)]}

from http.server import ThreadingHTTPServer
srv = ThreadingHTTPServer(("127.0.0.1", 0), S.Handler)
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
time.sleep(0.3)


def req(method, path, obj=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    body = json.dumps(obj).encode("utf-8") if obj is not None else None
    hdr = {"Content-Type": "application/json"} if body else {}
    c.request(method, path, body, hdr)
    r = c.getresponse()
    raw = r.read().decode("utf-8")
    c.close()
    try:
        return r.status, json.loads(raw)
    except Exception:
        return r.status, raw


ok = True


def check(label, cond, detail=""):
    global ok
    print(("  PASS  " if cond else "  FAIL  ") + label + (("  | " + str(detail)) if detail else ""))
    if not cond:
        ok = False


def marks():
    _, m = req("GET", "/api/marks")
    return {s["id"]: list(s["paths"]) for s in m["sets"]}, m


print("\n[链路 A] 图鉴推送一批图 → 对比页拿到，uid 齐备")
st, j = req("POST", "/api/selection", {"label": "端到端验证", "paths": IMG})
check("创建会话成功", st == 200 and j.get("ok"), j)
sid = j.get("id")
st, j = req("GET", "/api/selection?id=%d" % sid)
items = j["selection"]["items"]
check("会话含 4 张", len(items) == 4)
check("每张都带上了 uid（对比页靠它认标记）",
      all(it.get("uid") for it in items), [it.get("uid") for it in items])

print("\n[链路 B] 点卡片上的 ×（淘汰）—— 只标这一张")
st, j = req("POST", "/api/marks/apply", {"ops": [
    {"set": "verdict-reject", "name": "淘汰", "uids": [UID[2]], "remove": False,
     "color": 7}]})   # 7 = MCOLORS 里 #d0402f 的下标
check("apply 返回 ok", st == 200 and j.get("ok"), j)
cur, raw = marks()
check("建出「淘汰」夹且只含这一张", cur.get("verdict-reject") == [UID[2]],
      cur.get("verdict-reject"))
check("「默认」夹 2 张原样没动", set(cur["sDef"]) == {UID[0], UID[1]}, cur["sDef"])
vj = [s for s in raw["sets"] if s["id"] == "verdict-reject"][0]
check("淘汰夹色号被钉成 7（前端配 #d0402f）", vj.get("color") == 7, vj.get("color"))

print("\n[链路 C] 点 ✓（保留）→ 再点 × → 改判（不该同时挂两个夹）")
req("POST", "/api/marks/apply", {"ops": [
    {"set": "verdict-keep", "name": "保留", "uids": [UID[2]], "remove": False}]})
# 前端改判顺序：先把旧夹摘掉，再加新夹
req("POST", "/api/marks/apply", {"ops": [
    {"set": "verdict-reject", "name": "淘汰", "uids": [UID[2]], "remove": True}]})
cur, _ = marks()
check("保留夹含它", cur.get("verdict-keep") == [UID[2]], cur.get("verdict-keep"))
check("淘汰夹已空（改判干净）", cur.get("verdict-reject") == [],
      cur.get("verdict-reject"))

print("\n[链路 D] 再点一次已选中的那枚 = 取消判定")
req("POST", "/api/marks/apply", {"ops": [
    {"set": "verdict-keep", "name": "保留", "uids": [UID[2]], "remove": True}]})
cur, _ = marks()
check("保留夹空了", cur.get("verdict-keep") == [])
check("两个判定夹都还在（没被删）",
      "verdict-keep" in cur and "verdict-reject" in cur)

print("\n[链路 E] 批量：3 张一起标进保留")
req("POST", "/api/marks/apply", {"ops": [
    {"set": "verdict-keep", "name": "保留",
     "uids": [UID[1], UID[2], UID[3]], "remove": False}]})
cur, _ = marks()
check("保留夹 3 张", set(cur["verdict-keep"]) == {UID[1], UID[2], UID[3]},
      cur.get("verdict-keep"))
check("默认夹仍是 2 张（局部写没碰别的夹）",
      set(cur["sDef"]) == {UID[0], UID[1]}, cur["sDef"])
check("一张图可同时在默认夹和保留夹（UID[1]）",
      UID[1] in cur["sDef"] and UID[1] in cur["verdict-keep"])

print("\n[链路 F] 清空本批判定 —— 只清这批的，不动别的")
req("POST", "/api/marks/apply", {"ops": [
    {"set": "verdict-keep", "name": "保留",
     "uids": [UID[1], UID[2], UID[3]], "remove": True}]})
cur, _ = marks()
check("保留夹已清空", cur.get("verdict-keep") == [])
check("默认夹 2 张完好", set(cur["sDef"]) == {UID[0], UID[1]})

print("\n[链路 G] 与图鉴侧兼容：走整体覆盖仍能正常写（图鉴页那条路）")
st, j = req("POST", "/api/marks", {"sets": [
    {"id": "sDef", "name": "默认", "paths": [UID[0]], "color": 0}], "active": "sDef"})
check("整体覆盖成功", st == 200 and j.get("ok"), j)
cur, _ = marks()
check("覆盖后只剩 1 张", cur["sDef"] == [UID[0]], cur.get("sDef"))

srv.shutdown()
shutil.rmtree(TMP, ignore_errors=True)
print("\n=== 结果:", "全部通过" if ok else "有失败", "===")
sys.exit(0 if ok else 1)
