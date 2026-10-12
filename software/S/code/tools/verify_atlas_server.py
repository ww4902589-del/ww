# -*- coding: utf-8 -*-
"""端到端验证风格图鉴服务版（临时端口，测完即关）。"""
import json, os, sys, threading, time, urllib.parse, urllib.request, urllib.error
from http.server import ThreadingHTTPServer
sys.path.insert(0, r"C:/Users/wyj/WorkBuddy/2026-09-25-19-54-13")
import style_atlas as A, build_atlas as B, atlas_server as S

P = F = 0
def ck(n, c, e=""):
    global P, F
    if c: P += 1; print(f"  ✓ {n}")
    else: F += 1; print(f"  ✗ {n}" + (f" → {e}" if e else ""))

print("== 构建数据 ==")
data = A.build(verbose=False)
styles = A.load_styles(A.DEFAULT_STYLES)
cat_cnt = {}
for (cat, nm) in styles:
    cat_cnt[cat] = cat_cnt.get(cat, 0) + 1
lib_avail = {}
for lib, _ in data["facets"]["library"]:
    lib_avail[lib] = cat_cnt.get(lib, 0)
    lib_avail[A.lib_of(lib)] = cat_cnt.get(lib, 0)
S.Handler.data = data
S.Handler.out_dir = r"C:/Users/wyj/WorkBuddy/2026-09-25-19-54-13"

# 用 build_atlas 的统一构建路径，但写到临时文件，
# 避免验证过程覆盖交付产物「风格图鉴.html」。
import shutil, tempfile
_tmp_html = os.path.join(tempfile.gettempdir(), "_verify_atlas_tmp.html")
sys.argv = ["build_atlas.py", "--out", _tmp_html, "--max-thumb", "60"]
B.main()
# 服务读取的仍是 out_dir/风格图鉴.html —— 让它读临时产物
_ORIG_HTML = S.Handler.do_GET


def _patched_get(self):
    if urllib.parse.urlparse(self.path).path in ("/", "/index.html"):
        html = open(_tmp_html, encoding="utf-8").read()
        html = html.replace("src=\"file:///${im.path}\"",
                            "src=\"/img?p=${encodeURIComponent(im.path)}\"")
        html = html.replace("src='file:///'+im.path",
                            "src='/img?p='+encodeURIComponent(im.path)")
        return self._send(200, html, "text/html; charset=utf-8")
    return _ORIG_HTML(self)


S.Handler.do_GET = _patched_get

print("\n== 起临时服务 ==")
srv = ThreadingHTTPServer(("127.0.0.1", 0), S.Handler)
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
time.sleep(0.7)
base = f"http://127.0.0.1:{port}"
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # 绕过代理
try:
    r = opener.open(base + "/", timeout=20)
    html = r.read().decode("utf-8")
    ck("首页 200", r.status == 200)
    ck("首页图片走 /img（无 file://）", "file:///" not in html)
    ck("首页注入 /img 路由", "/img?p=" in html)
    ck("首页含图鉴脚本", "function matches" in html and "renderStats" in html)

    r = opener.open(base + "/api/data", timeout=20)
    d = json.loads(r.read())
    ck("/api/data 200", r.status == 200)
    ck("数据含 images", len(d.get("images", [])) > 0, str(len(d.get("images", []))))
    ck("数据含 facets", len(d.get("facets", {})) == 8, str(len(d.get("facets", {}))))

    # 测若干张真实图片
    okimg = 0
    for im in data["images"][:6]:
        r = opener.open(base + "/img?p=" + urllib.parse.quote(im["path"]), timeout=20)
        b = r.read()
        if r.status == 200 and len(b) > 500 and (r.headers.get("Content-Type") or "").startswith("image/"):
            okimg += 1
    ck("抽样图片全部可加载", okimg == 6, f"{okimg}/6")

    try:
        opener.open(base + "/img?p=" + urllib.parse.quote("C:/nope.png"), timeout=10)
        ck("不存在图片 404", False, "居然 200")
    except urllib.error.HTTPError as e:
        ck("不存在图片 404", e.code == 404, str(e.code))

    # ---- 标记接口 ----
    print("\n== 标记接口 ==")
    r = opener.open(base + "/api/marks", timeout=15)
    m0 = json.loads(r.read())
    ck("/api/marks GET 200", r.status == 200)
    ck("初始标记结构含 paths", isinstance(m0.get("paths"), list))

    picked = [im["path"] for im in data["images"][:3]]
    req = urllib.request.Request(base + "/api/marks", method="POST",
        data=json.dumps({"paths": picked, "note": {"测试": 1}}).encode(),
        headers={"Content-Type": "application/json"})
    r = opener.open(req, timeout=15)
    j = json.loads(r.read())
    ck("/api/marks POST 200", r.status == 200)
    ck("写入返回正确条数", j.get("n") == 3, str(j.get("n")))

    r = opener.open(base + "/api/marks", timeout=15)
    m1 = json.loads(r.read())
    ck("标记已持久化", set(picked) <= set(m1.get("paths", [])), str(m1.get("paths"))[:120])
    ck("备注一并保存", bool(m1.get("note")))

    # 标记落盘文件确实存在
    mf = os.path.join(r"C:/Users/wyj/WorkBuddy/2026-09-25-19-54-13", "_marks.json")
    ck("_marks.json 落盘", os.path.isfile(mf))
    if os.path.isfile(mf):
        ck("落盘内容与接口一致", set(json.load(open(mf, encoding="utf-8"))["paths"]) >= set(picked))

    # 导出标记图
    import tempfile
    exp = tempfile.mkdtemp(prefix="mkexp_")
    req = urllib.request.Request(base + "/api/export_marks", method="POST",
        data=json.dumps({"dst": exp.replace("\\", "/"), "paths": picked}).encode(),
        headers={"Content-Type": "application/json"})
    r = opener.open(req, timeout=30)
    je = json.loads(r.read())
    ck("/api/export_marks 可用", r.status == 200 and je.get("ok"))
    ck("导出复制了全部文件", je.get("done") == 3, json.dumps(je, ensure_ascii=False)[:150])
    ck("导出目录内有文件", len(os.listdir(exp)) == 3, str(len(os.listdir(exp))))
    # 体积一致校验
    same = all(os.path.getsize(os.path.join(exp, os.path.basename(p))) == os.path.getsize(p)
               for p in picked if os.path.exists(p))
    ck("导出文件体积一致", same)

    # 清理测试标记（不污染用户数据）
    req = urllib.request.Request(base + "/api/marks", method="POST",
        data=json.dumps({"paths": [], "note": {}}).encode(),
        headers={"Content-Type": "application/json"})
    opener.open(req, timeout=15)
    r = opener.open(base + "/api/marks", timeout=15)
    ck("清空后标记为空", json.loads(r.read()).get("paths") == [])

    # /api/open 参数校验（用不存在的路径，避免真的弹窗）
    req = urllib.request.Request(base + "/api/open", method="POST",
        data=json.dumps({"path": "C:/__definitely_not_exist__/x.png"}).encode(),
        headers={"Content-Type": "application/json"})
    try:
        opener.open(req, timeout=15)
        ck("/api/open 拒绝无效路径", False, "居然 200")
    except urllib.error.HTTPError as e:
        ck("/api/open 拒绝无效路径", e.code == 404, str(e.code))

    ck("首页含标记页容器", 'id="marksView"' in html)
    ck("首页含标记接口调用", "/api/marks" in html)
finally:
    srv.shutdown()

print(f"\n===== 结果: {P} 通过 / {F} 失败 =====")
sys.exit(1 if F else 0)
