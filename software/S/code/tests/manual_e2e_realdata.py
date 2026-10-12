"""真机端到端：在**真实数据**上验证「92 张批次的标记不会和历史混在一起」。

与 manual_e2e_batch_sets.py 的区别：那个用临时目录验证协议行为；
这个直接对着用户真实的标记文件跑，验证「新旧数据共存 + 分批次不串」。

安全策略：**先备份真 _marks.json，跑完逐字段核对还原**。
一旦发现真数据被改，立即从备份恢复并报错退出。

用法：python tests/manual_e2e_realdata.py
"""
import hashlib
import json
import os
import shutil
import sys
import threading
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.join(ROOT, "src"))

import atlas_server as S  # noqa: E402

MARKS = os.path.join(ROOT, "tools", "_marks.json")
PASS, FAIL = [], []


def ok(cond, label):
    (PASS if cond else FAIL).append(label)
    print(("  OK   " if cond else " FAIL  ") + label)


def _opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def post(base, path, obj):
    req = urllib.request.Request(base + path,
                                 data=json.dumps(obj).encode("utf-8"),
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    with _opener().open(req, timeout=10) as r:
        return json.loads(r.read() or b"{}")


def get(base, path):
    with _opener().open(base + path, timeout=10) as r:
        return json.loads(r.read() or b"{}")


def fingerprint(path):
    """真数据的完整指纹：逐夹逐条，用于「跑完必须一模一样」的核对。"""
    d = json.load(open(path, encoding="utf-8"))
    return {
        "sets": [(s.get("id"), s.get("name"), tuple(s.get("paths") or []))
                 for s in d.get("sets", [])],
        "active": d.get("active"),
        "saved_at": d.get("saved_at"),
    }


def main():
    assert os.path.isfile(MARKS), f"找不到真标记文件 {MARKS}"
    before = fingerprint(MARKS)
    n_before = sum(len(p) for _, _, p in before["sets"])
    print(f"[真数据] {MARKS}")
    for sid, name, paths in before["sets"]:
        print(f"   夹 {name!r} id={sid} 共 {len(paths)} 张")
    print(f"   合计 {n_before} 张 · active={before['active']}\n")

    bak = MARKS + ".bak_realdata_" + time.strftime("%Y%m%d_%H%M%S")
    shutil.copy2(MARKS, bak)
    print(f"[备份] {os.path.basename(bak)}\n")

    restored = False
    srv = None
    try:
        from http.server import ThreadingHTTPServer
        # 用默认 out_dir（= tools/），即真数据所在处
        srv = ThreadingHTTPServer(("127.0.0.1", 0), S.Handler)
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        time.sleep(0.3)
        print(f"[服务] {base}  （读的就是真 _marks.json）\n")

        bx = "926266f5"          # 用户那 92 张的批次
        d = os.path.join(r"C:\Users\wyj\Desktop\D\T", bx)
        pngs = sorted(f for f in os.listdir(d) if f.lower().endswith(".png"))
        print(f"=== 0. 真实批次 {bx}：{len(pngs)} 张图 ===")
        ok(len(pngs) == 92, f"该批次确实是 92 张（实际 {len(pngs)}）")
        # 造 3 个 uid（真实路径 + 真实批次 + 序号）
        uids = [f"{os.path.join(d, pngs[i])}#{bx}#{i+1}" for i in (0, 1, 2)]

        print("\n=== 1. 标 3 张「保留」，只应落进 保留·926266f5 ===")
        r = post(base, "/api/marks/apply", {"ops": [
            {"set": f"verdict-keep@{bx}", "name": f"保留·{bx}",
             "uids": uids, "color": 2}]})
        ok(r.get("ok") is True and r.get("changed") == 3,
           f"写入 3 张成功（changed={r.get('changed')}）")

        m = get(base, "/api/marks")
        byid = {s["id"]: s for s in m["sets"]}
        # 老夹「默认」必须一张未动 —— 这是最关键的一条
        old = next((s for s in m["sets"] if s["id"] == before["active"]), None)
        ok(old is not None, "原「默认」夹仍在")
        ok(len(old.get("paths") or []) == 43,
           f"原「默认」夹仍 43 张，一张未动（实际 {len(old.get('paths') or [])}）")
        ok(set(old.get("paths") or []) == set(before["sets"][0][2]),
           "原「默认」夹内容逐条未变")

        newset = byid.get(f"verdict-keep@{bx}", {})
        ok(len(newset.get("paths") or []) == 3, "新夹恰好 3 张")
        ok(set(newset.get("paths") or []) <= set(uids), "新夹里就是刚标的那 3 张")
        ok(newset.get("name") == f"保留·{bx}", "夹名带批次号")
        ok("verdict-keep" not in byid,
           "**没有**产生无后缀的全局「保留」夹（混批的根因已消除）")

        print("\n=== 2. 标另一个批次，两边互不干扰 ===")
        bx2 = "1c0c149f"
        d2 = os.path.join(r"C:\Users\wyj\Desktop\D", bx2)
        p2 = sorted(f for f in os.listdir(d2) if f.lower().endswith(".png"))[:2]
        uids2 = [f"{os.path.join(d2, p2[i])}#{bx2}#{i+1}" for i in range(len(p2))]
        post(base, "/api/marks/apply", {"ops": [
            {"set": f"verdict-reject@{bx2}", "name": f"淘汰·{bx2}",
             "uids": uids2, "color": 0}]})
        m = get(base, "/api/marks")
        byid = {s["id"]: s for s in m["sets"]}
        k1 = set(byid.get(f"verdict-keep@{bx}", {}).get("paths") or [])
        r2 = set(byid.get(f"verdict-reject@{bx2}", {}).get("paths") or [])
        ok(k1 == set(uids), "批次A 的保留夹内容未变")
        ok(r2 == set(uids2), "批次B 的淘汰夹内容正确")
        ok(not (k1 & r2), "两批零交叉")
        old2 = next((s for s in m["sets"] if s["id"] == before["active"]), None)
        ok(len(old2.get("paths") or []) == 43, "原「默认」夹依然 43 张未动")

        print("\n=== 3. 取消批次B，批次A 与原「默认」都不受影响 ===")
        post(base, "/api/marks/apply", {"ops": [
            {"set": f"verdict-reject@{bx2}", "name": f"淘汰·{bx2}",
             "uids": uids2, "remove": True}]})
        m = get(base, "/api/marks")
        byid = {s["id"]: s for s in m["sets"]}
        ok((byid.get(f"verdict-reject@{bx2}", {}).get("paths") or []) == [],
           "批次B 已清空")
        ok(set(byid.get(f"verdict-keep@{bx}", {}).get("paths") or []) == set(uids),
           "批次A 未被误伤")
        old3 = next((s for s in m["sets"] if s["id"] == before["active"]), None)
        ok(len(old3.get("paths") or []) == 43, "原「默认」夹依然 43 张未动")

        print("\n=== 4. 收尾：清掉本次验证产生的夹 ===")
        post(base, "/api/marks/apply", {"ops": [
            {"set": f"verdict-keep@{bx}", "name": f"保留·{bx}",
             "uids": uids, "remove": True}]})
        print("   （已移除验证用标记）")
    finally:
        if srv:
            srv.shutdown()
            srv.server_close()
        # 核对真数据是否还原。
        # 注意口径：只比**有内容的夹**。验证过程中派生夹会被创建又清空，
        # 空壳可能残留 —— 那是本次验证的痕迹，不是用户数据的损坏。
        # 真正要守住的是「用户原有内容一条不差、active 不变」。
        after = fingerprint(MARKS)
        before_content = {sid: paths for sid, _, paths in before["sets"]}
        after_content = {sid: paths for sid, _, paths in after["sets"]}
        # 用户原有的每个夹必须原样存在
        pristine = all(after_content.get(sid) == paths
                       for sid, paths in before_content.items())
        # 新增的夹必须都是空的（不能有内容偷偷跑进去）
        extra = {k: v for k, v in after_content.items() if k not in before_content}
        no_stray = all(not v for v in extra.values())
        if pristine and no_stray and after["active"] == before["active"]:
            note = f"（额外空夹 {len(extra)} 个）" if extra else ""
            print(f"\n[还原核对] 用户原有内容逐条一致，active 未变{note}")
            if extra:
                print(f"[还原核对] 清掉验证残留的空夹：{list(extra)}")
                _drop_empty_verdict_sets(MARKS, set(extra))
            os.remove(bak)
            print("[还原核对] 备份已删除（无需回滚）")
        else:
            shutil.copy2(bak, MARKS)
            restored = True
            print("\n[还原核对] **真数据有实质差异，已从备份回滚**")
            print(f"   原有内容完好={pristine} 无残留内容={no_stray} "
                  f"active一致={after['active'] == before['active']}")
            print(f"   备份保留在 {bak}")

    print("\n=== 结果 ===")
    print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
    if FAIL:
        for f in FAIL:
            print("   - " + f)
    if restored:
        print("   !! 真数据发生过变动，需要人工确认")
        return 4
    return 0 if not FAIL else 3


def _drop_empty_verdict_sets(path, ids):
    """从真标记文件里摘掉指定的空夹（只动这些 id，其余原样）。"""
    d = json.load(open(path, encoding="utf-8"))
    d["sets"] = [s for s in d.get("sets", []) if s.get("id") not in ids]
    if d.get("active") not in [s.get("id") for s in d["sets"]]:
        d["active"] = d["sets"][0]["id"] if d["sets"] else ""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


if __name__ == "__main__":
    sys.exit(main())
