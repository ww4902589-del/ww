"""端到端验证：对比页的淘汰/保留按批次自动分夹。

要证明的事（用户需求：「92 张的批次里选中了一些，不希望和之前的混在一起」）：
    A. 标批次 X 的图 → 落进 verdict-keep@X，**不会**污染其他批次
    B. 两个批次交替标 → 各自独立，互不影响
    C. 取消一个批次的判定 → 另一个批次原样不动
    D. 升级前的老夹（无 @ 后缀）仍能读出来，不会被当作未标记

安全前提：**绝不碰用户真数据**。这里把 DATA_DIR 和 Handler.out_dir
两处都改到临时目录（上次只改 DATA_DIR，marks_file 取自 Handler.out_dir，
测试夹被写进了用户的 _marks.json）。真数据目录是 C:/Users/wyj/Desktop/D/T。

用法：python tests/manual_e2e_batch_sets.py
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.join(ROOT, "src"))

import atlas_server as S  # noqa: E402

PASS, FAIL = [], []


def ok(cond, label):
    (PASS if cond else FAIL).append(label)
    print(("  OK   " if cond else " FAIL  ") + label)


def post(base, path, obj):
    req = urllib.request.Request(
        base + path,
        data=json.dumps(obj).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST")
    # 显式绕开代理：沙箱代理会给 127.0.0.1 造一个假 502
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=10) as r:
        return json.loads(r.read() or b"{}")


def get(base, path):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(base + path, timeout=10) as r:
        return json.loads(r.read() or b"{}")


def main():
    tmp = tempfile.mkdtemp(prefix="e2e_batch_")
    orig_dir = S.DATA_DIR
    orig_out = S.Handler.out_dir
    try:
        S.DATA_DIR = tmp
        S.Handler.out_dir = tmp          # marks_file 取自这里，必须一起改
        # 隔离自证：确认写文件真的落在临时目录
        probe = S.Handler.__new__(S.Handler)
        probe.out_dir = tmp
        assert os.path.abspath(probe.marks_file).startswith(os.path.abspath(tmp)), \
            "隔离失败：marks_file 不在临时目录里"
        print(f"[隔离确认] marks_file = {probe.marks_file}")
        print(f"[隔离确认] 用户真数据目录 = {orig_out}（本次绝不触碰）\n")

        from http.server import ThreadingHTTPServer
        srv = ThreadingHTTPServer(("127.0.0.1", 0), S.Handler)
        port = srv.server_address[1]
        base = f"http://127.0.0.1:{port}"
        th = threading.Thread(target=srv.serve_forever, daemon=True)
        th.start()
        time.sleep(0.3)

        # 造两批真图（GET /api/marks 会过滤指向不存在文件的标记）
        def mk(batch, name):
            d = os.path.join(tmp, batch)
            os.makedirs(d, exist_ok=True)
            p = os.path.join(d, name)
            with open(p, "wb") as f:
                f.write(b"\x89PNG\r\n\x1a\n")
            return p

        bx, by = "926266f5", "1c0c149f"
        nx, ny = "001.png", "002.png"
        px1, px2 = mk(bx, nx), mk(bx, "003.png")
        py1 = mk(by, ny)
        ux1 = f"{px1}#{bx}#1"
        ux2 = f"{px2}#{bx}#2"
        uy1 = f"{py1}#{by}#1"

        print("=== A. 标批次 X 的图，只落进 X 的夹 ===")
        r = post(base, "/api/marks/apply", {"ops": [
            {"set": f"verdict-keep@{bx}", "name": f"保留·{bx}",
             "uids": [ux1], "color": 2}]})
        ok(r.get("ok") is True, f"写入返回 ok（changed={r.get('changed')}）")
        m = get(base, "/api/marks")
        ids = {s["id"]: s for s in m["sets"]}
        ok(f"verdict-keep@{bx}" in ids, f"生成了批次夹 verdict-keep@{bx}")
        ok(ids.get(f"verdict-keep@{bx}", {}).get("name") == f"保留·{bx}",
           "夹名带批次，一眼能认出是哪批")
        ok(ids.get(f"verdict-keep@{bx}", {}).get("color") == 2,
           "色位 = 2（绿）被正确保存")
        ok(("verdict-keep" in ids) is False,
           "**没有**产生无后缀的全局夹（那正是要修的混批问题）")
        # /api/marks 在文件为空时会自动补一个空的「默认」夹（id 形如 s<数字>），
        # 那是服务端的既有行为，不算污染。只计**有内容的**夹。
        nonempty = {k: v for k, v in ids.items() if (v.get("paths") or [])}
        ok(list(nonempty) == [f"verdict-keep@{bx}"],
           f"只有一个**有内容**的夹，未污染其他（实际 {sorted(nonempty)}）")

        print("\n=== B. 两个批次交替标，各自独立 ===")
        post(base, "/api/marks/apply", {"ops": [
            {"set": f"verdict-keep@{bx}", "name": f"保留·{bx}",
             "uids": [ux2], "color": 2}]})
        post(base, "/api/marks/apply", {"ops": [
            {"set": f"verdict-reject@{by}", "name": f"淘汰·{by}",
             "uids": [uy1], "color": 0}]})
        m = get(base, "/api/marks")
        ids = {s["id"]: s for s in m["sets"]}
        bx_paths = set(ids.get(f"verdict-keep@{bx}", {}).get("paths") or [])
        by_paths = set(ids.get(f"verdict-reject@{by}", {}).get("paths") or [])
        ok(bx_paths == {ux1, ux2}, f"批次X 的夹里有 2 张（实际 {len(bx_paths)}）")
        ok(by_paths == {uy1}, f"批次Y 的夹里有 1 张（实际 {len(by_paths)}）")
        ok(not (bx_paths & by_paths), "两批的图**零交叉**（这是核心需求）")
        # 同样只计有内容的夹（服务端会补一个空的「默认」夹）
        nonempty2 = {k for k, v in ids.items() if (v.get("paths") or [])}
        ok(nonempty2 == {f"verdict-keep@{bx}", f"verdict-reject@{by}"},
           f"恰好 2 个批次夹（实际 {sorted(nonempty2)}）")

        print("\n=== C. 取消批次 Y 的判定，批次 X 原样不动 ===")
        before_x = sorted(ids.get(f"verdict-keep@{bx}", {}).get("paths") or [])
        post(base, "/api/marks/apply", {"ops": [
            {"set": f"verdict-reject@{by}", "name": f"淘汰·{by}",
             "uids": [uy1], "remove": True}]})
        m = get(base, "/api/marks")
        ids2 = {s["id"]: s for s in m["sets"]}
        after_x = sorted(ids2.get(f"verdict-keep@{bx}", {}).get("paths") or [])
        ok(after_x == before_x, "批次X 的标记逐条未变（没被误伤）")
        ok((ids2.get(f"verdict-reject@{by}", {}).get("paths") or []) == [],
           "批次Y 的夹已空")
        ok(f"verdict-reject@{by}" in ids2,
           "批次Y 的空夹仍保留（不静默消失，用户能看到它被清空过）")

        print("\n=== D. 升级前的老夹仍能读出（兼容性） ===")
        post(base, "/api/marks/apply", {"ops": [
            {"set": "verdict-reject", "name": "淘汰", "uids": [ux2], "color": 0}]})
        m = get(base, "/api/marks")
        ids3 = {s["id"]: s for s in m["sets"]}
        ok("verdict-reject" in ids3, "老的无后缀夹可以共存（不会被改写）")
        ok(ux2 in (ids3.get("verdict-reject", {}).get("paths") or []),
           "老夹里的图能读出来，升级后不会「凭空消失」")

        print("\n=== E. 原子写 + 并发不丢标记 ===")
        # 并发写两个不同批次的夹，检查都没丢
        errs = []

        def w(setid, name, uid, color):
            try:
                post(base, "/api/marks/apply", {"ops": [
                    {"set": setid, "name": name, "uids": [uid], "color": color}]})
            except Exception as e:
                errs.append(str(e))

        ts = [threading.Thread(target=w, args=(f"verdict-keep@cc{i:06d}",
                                              f"保留·cc{i:06d}",
                                              f"{px1}#cc{i:06d}#1", 2))
              for i in range(6)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        ok(not errs, f"6 个并发写全部成功（错误 {len(errs)}）")
        m = get(base, "/api/marks")
        ids4 = {s["id"] for s in m["sets"]}
        got = [f"verdict-keep@cc{i:06d}" for i in range(6) if f"verdict-keep@cc{i:06d}" in ids4]
        ok(len(got) == 6, f"6 个并发夹全部落盘，无丢失（实际 {len(got)}）")
        # 检查没有 .tmp 残留（原子替换应清理干净）
        tmps = [f for f in os.listdir(tmp) if f.endswith(".tmp")]
        ok(not tmps, f"无 .tmp 残留（实际 {tmps}）")

        print("\n=== F. drop：整夹删除（清空判定后回收空夹） ===")
        r = post(base, "/api/marks/apply", {"ops": [
            {"set": f"verdict-keep@{bx}", "name": f"保留·{bx}",
             "uids": [], "remove": True, "drop": True}]})
        ok(r.get("ok") is True, "drop 返回 ok")
        m = get(base, "/api/marks")
        ids5 = {s["id"] for s in m["sets"]}
        ok(f"verdict-keep@{bx}" not in ids5, "派生夹被删除")
        # 图鉴手动建的夹（无后缀的老夹）不能因为 drop 逻辑被误删
        ok("verdict-reject" in ids5, "无后缀的老夹未被误删")
        # drop 只删夹，不删图
        ok(os.path.isfile(px1) and os.path.isfile(px2),
           "图文件仍在磁盘上（drop 只解引用）")
        # 删掉 active 夹时 active 要自动改指，不能留悬空 id
        m2 = get(base, "/api/marks")
        ok(m2.get("active") in {s["id"] for s in m2["sets"]} or not m2["sets"],
           f"active 未悬空（active={m2.get('active')}）")

        print("\n=== G. drop 不存在的夹是安全的（幂等） ===")
        r = post(base, "/api/marks/apply", {"ops": [
            {"set": "verdict-keep@nosuchbatch", "name": "保留·x",
             "uids": [], "remove": True, "drop": True}]})
        ok(r.get("ok") is True, "drop 不存在的夹不报错")
        ok(r.get("changed") == 0, f"未发生改动（changed={r.get('changed')}）")

        srv.shutdown()
        srv.server_close()
    finally:
        S.DATA_DIR = orig_dir
        S.Handler.out_dir = orig_out
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n=== 结果 ===")
    print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
    if FAIL:
        for f in FAIL:
            print("   - " + f)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
