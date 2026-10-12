# -*- coding: utf-8 -*-
"""内嵌图鉴宿主（``src/atlas_host.py``）的行为契约。

盯的是「一个程序、两个模块」这条线上的四处接缝：

* 数据目录怎么选 —— 打包运行的 ``__file__`` 在解包临时目录里，
  状态文件写那儿等于每次退出都清零；
* 8793 上已经有图鉴时复用，而不是抢绑；
* 8793 被别的程序占着时明确失败，而不是假装成功；
* 索引还在后台建时，页面端出等待页，而不是让用户读一句
  「请先运行 build_atlas.py」（内嵌后他没有这个动作可做）。
"""
from __future__ import annotations

import http.server
import json
import os
import pathlib
import re
import shutil
import socket
import sys
import tempfile
import threading
import unittest
import urllib.request
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
TOOLS = ROOT / "tools"
for _p in (str(SRC), str(TOOLS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import atlas_host                       # noqa: E402
import style_atlas                      # noqa: E402


def free_port() -> int:
    """拿一个当前空闲的端口号。拿到与用上之间有理论上的窗口期，
    单机测试里够用；真撞上会以「无法绑定」的形式暴露出来，不会静默。"""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def same_path(a: str, b: str) -> bool:
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


class _FakeAtlas(http.server.BaseHTTPRequestHandler):
    """/api/version 会应答的最小服务 —— 用来冒充「外部已有图鉴」。"""

    def do_GET(self):
        body = json.dumps({"version": 7, "images": 123,
                           "build_id": "deadbeef", "build_label": "外部图鉴"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class DataDirChoiceTests(unittest.TestCase):
    """状态文件放哪 —— 三条分支各一条断言。"""

    def test_env_override_wins_over_everything(self):
        with mock.patch.dict(os.environ, {"ATLAS_DATA_DIR": r"D:\x\atlas"}, clear=True):
            with mock.patch.object(sys, "frozen", True, create=True):
                self.assertTrue(same_path(style_atlas.resolve_data_dir(), r"D:\x\atlas"))

    def test_frozen_run_uses_local_appdata(self):
        env = {"LOCALAPPDATA": r"C:\Users\someone\AppData\Local"}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch.object(sys, "frozen", True, create=True):
                got = style_atlas.resolve_data_dir()
        self.assertTrue(same_path(
            got, r"C:\Users\someone\AppData\Local\ComfyBatch-S\atlas"))

    def test_source_run_keeps_old_behaviour(self):
        """源码运行时数据仍在图鉴目录 —— 命令行用法与之前完全一致，
        不能因为要内嵌就把开发机上已有的标记挪个地方。"""
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(sys, "frozen", False, create=True):
                got = style_atlas.resolve_data_dir()
        self.assertTrue(same_path(got, str(TOOLS)))

    def test_index_cache_follows_data_dir(self):
        """索引缓存必须跟着数据目录走。留在代码目录会写进临时解包目录，
        每次启动都白扫一遍盘。"""
        self.assertTrue(same_path(
            style_atlas.CACHE, os.path.join(style_atlas.DATA_DIR, "_img_index.json")))


class ProbeTests(unittest.TestCase):
    """探测：区分「图鉴在跑」和「端口被占」。"""

    def test_probe_returns_none_when_nothing_listens(self):
        self.assertIsNone(atlas_host.probe(port=free_port(), timeout=0.3))

    def test_probe_recognises_atlas(self):
        port = free_port()
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), _FakeAtlas)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            j = atlas_host.probe(port=port)
        finally:
            srv.shutdown()
            srv.server_close()
        self.assertIsNotNone(j)
        self.assertEqual(j.get("images"), 123)

    def test_probe_rejects_a_foreign_port(self):
        """端口通了但应答不是图鉴 —— 不能被当成「图鉴已在运行」。
        探针的契约是「不认识就返回 None」，所以这里断言 None 而不是抛错。"""
        port = free_port()
        srv = http.server.ThreadingHTTPServer(
            ("127.0.0.1", port), http.server.BaseHTTPRequestHandler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            self.assertIsNone(atlas_host.probe(port=port, timeout=0.5))
        finally:
            srv.shutdown()
            srv.server_close()


class StartTests(unittest.TestCase):
    """start() 的两条边界：复用外部实例 / 端口冲突时明确失败。"""

    def test_reuses_external_atlas_instead_of_taking_the_port(self):
        port = free_port()
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), _FakeAtlas)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            info = atlas_host.start(port=port)
        finally:
            srv.shutdown()
            srv.server_close()
        self.assertTrue(info["ok"])
        self.assertFalse(info["embedded"], "外部图鉴在跑，不该再起一个")
        self.assertEqual(info["images"], 123)

    def test_busy_port_by_foreign_process_fails_loudly(self):
        """端口被非图鉴程序占着：必须报失败并给出原因。
        静默降级成「图鉴不可用」会让用户以为程序坏了，而他其实什么都没做错。"""
        import atlas_server

        port = free_port()
        holder = socket.socket()
        holder.bind(("127.0.0.1", port))
        holder.listen(1)
        tmp = tempfile.mkdtemp()
        old_dir = atlas_server.DATA_DIR
        atlas_server.DATA_DIR = tmp
        try:
            info = atlas_host.start(port=port, watch_interval=0)
        finally:
            atlas_server.DATA_DIR = old_dir
            holder.close()
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertFalse(info["ok"])
        self.assertIn("无法绑定", info["reason"])

    def test_start_never_raises(self):
        """任何失败都必须以返回值的形式出现 —— 它是在主程序启动路径上调用的，
        抛出去就是一个起不来的 S。"""
        with mock.patch.object(atlas_host, "ensure_importable",
                               side_effect=RuntimeError("boom")):
            info = atlas_host.start(port=free_port(), watch_interval=0)
        self.assertIn("ok", info)          # 结构完整，调用方不用 try


class BootstrapWindowTests(unittest.TestCase):
    """索引后台构建的那几十秒里，服务必须已经能应答。"""

    @classmethod
    def setUpClass(cls):
        import atlas_server
        cls.S = atlas_server

    def setUp(self):
        self._state = dict(self.S._state)
        self._dir = self.S.DATA_DIR
        self.tmp = tempfile.mkdtemp()
        self.S.DATA_DIR = self.tmp
        # 首屏 HTML 一定不存在，用来触发「等待页」那条分支
        self.page = os.path.join(self.tmp, "风格图鉴.html")
        self.assertFalse(os.path.isfile(self.page))

    def tearDown(self):
        self.S.DATA_DIR = self._dir
        self.S._state.clear()
        self.S._state.update(self._state)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _serve_building(self, port):
        """起服务，并把 rebuild 换成「只把 building 置真」——
        真扫一遍盘要二十多秒，而这里要验的是窗口期的行为，不是建索引本身。"""
        def fake_rebuild(*a, **k):
            self.S._state["building"] = True
            return True, 1, 0.0

        patcher = mock.patch.object(self.S, "rebuild", fake_rebuild)
        patcher.start()
        self.addCleanup(patcher.stop)
        srv, _ = self.S.serve(port=port, bootstrap="background", watch_interval=0)
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        return srv

    def test_index_serves_a_waiting_page_while_building(self):
        port = free_port()
        self._serve_building(port)
        body = urllib.request.urlopen(
            f"http://127.0.0.1:{port}/", timeout=5).read().decode("utf-8")
        self.assertIn("正在建立索引", body)
        self.assertIn("location.reload", body, "等待页必须自己重试，不能要求用户手动刷新")

    def test_version_answers_during_bootstrap(self):
        """前端靠 /api/version 判断「该等还是该报错」，所以窗口期它必须先活着。"""
        port = free_port()
        self._serve_building(port)
        j = json.loads(urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/version", timeout=5).read().decode("utf-8"))
        self.assertTrue(j.get("building"))
        self.assertEqual(j.get("images"), 0)
        self.assertIn("build_id", j)


class PreloadTests(unittest.TestCase):
    """启动预载：上次算过的结果先端上来，别让人对着「正在建立索引」干等。"""

    @classmethod
    def setUpClass(cls):
        import atlas_server
        cls.S = atlas_server

    def setUp(self):
        self._dir = self.S.DATA_DIR
        self._state = dict(self.S._state)
        self.tmp = tempfile.mkdtemp()
        self.S.DATA_DIR = self.tmp
        self.S._state["data"] = None
        self.S._state["version"] = 0

    def tearDown(self):
        self.S.DATA_DIR = self._dir
        self.S._state.clear()
        self.S._state.update(self._state)
        shutil.rmtree(self.tmp, ignore_errors=True)

    @staticmethod
    def _fake_data(folder_rule=None):
        return {
            "images": [{"path": "x.png", "techs": [], "styles": [], "libs": []}],
            "stats": {}, "facets": {}, "thumbs": {},
            "build_id": "test1234", "build_label": "测试用",
            "image_sources": {'roots': __import__('atlas_sources').normalize(__import__('atlas_sources').defaults())},
            # 派生规则版本：真实 payload 一定带它，预载会校验 —— 测试数据也得带，
            # 否则测的是「一个线上不存在的形状」。
            "folder_rule": (style_atlas.FOLDER_RULE if folder_rule is None
                            else folder_rule),
        }

    def test_save_then_preload(self):
        self.S._save_data_cache(self._fake_data())
        self.assertTrue(self.S._preload(48, lambda m: None))
        self.assertEqual(len(self.S._state["data"]["images"]), 1)

    def test_changed_sources_reject_old_index(self):
        data = self._fake_data()
        data['image_sources'] = {'roots': [self.tmp.replace('\\', '/')]}
        self.S._save_data_cache(data)
        self.assertFalse(self.S._preload(48, lambda m: None))
        self.assertIsNone(self.S._state['data'])

    def test_without_cache_preload_is_a_noop(self):
        self.assertFalse(self.S._preload(48, lambda m: None))
        self.assertIsNone(self.S._state["data"])

    def test_page_is_rendered_when_missing(self):
        """数据在、页面不在（换数据目录 / 首次升级）：现场渲染，别走等待页。"""
        page = os.path.join(self.tmp, "风格图鉴.html")
        self.S._save_data_cache(self._fake_data())
        self.assertFalse(os.path.isfile(page))
        self.S._preload(48, lambda m: None)
        self.assertTrue(os.path.isfile(page))
        self.assertGreater(os.path.getsize(page), 1000)

    def test_existing_data_is_not_clobbered(self):
        """已经在内存里的数据不该被磁盘上那份旧的覆盖掉。"""
        self.S._state["data"] = {"images": [1, 2, 3], "build_id": "live"}
        self.S._save_data_cache(self._fake_data())
        self.assertFalse(self.S._preload(48, lambda m: None))
        self.assertEqual(len(self.S._state["data"]["images"]), 3)

    def test_corrupt_cache_is_ignored(self):
        pathlib.Path(self.tmp, "_data_cache.json").write_text("不是 json",
                                                              encoding="utf-8")
        self.assertFalse(self.S._preload(48, lambda m: None))

    def test_cache_from_an_older_folder_rule_is_rejected(self):
        """派生规则升级后，旧缓存里的「文件夹分类」是按旧规则算的。

        端上去就是在升级后的最初几秒里重演原故障：用户在分类里找不到
        磁盘上明明存在的那个文件夹。宁可多等一次构建，也不要端错数据。
        这里的对照是同一份数据把版本号改对就必须能预载 —— 免得上一条
        「拒绝」的实现顺手把所有缓存都拒了却没人发现。
        """
        stale = self._fake_data(folder_rule=style_atlas.FOLDER_RULE - 1)
        self.S._save_data_cache(stale)
        self.assertFalse(self.S._preload(48, lambda m: None))
        self.assertIsNone(self.S._state["data"])

        self.S._save_data_cache(self._fake_data())
        self.assertTrue(self.S._preload(48, lambda m: None))
        self.assertEqual(len(self.S._state["data"]["images"]), 1)

    def test_rebuild_writes_the_cache(self):
        """rebuild 成功一次，就得留下可供下次预载的那份。"""
        import atlas_server

        def fake_payload(reviews, styles, max_thumb):
            return self._fake_data()

        with mock.patch.object(atlas_server, "build_payload", fake_payload), \
             mock.patch.object(atlas_server, "_sync_desktop_copy", lambda p: None):
            ok, ver, secs = atlas_server.rebuild("r", "s", 48, why="测试")
        self.assertTrue(ok)
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "_data_cache.json")))


class EmbeddedWiringTests(unittest.TestCase):
    """S 侧接线：启动路径上必须真的调了图鉴，且能关掉。"""

    def test_main_starts_the_atlas(self):
        src = (SRC / "comfybatch_v2_app.py").read_text(encoding="utf-8")
        # 找「被调用」而不是「被定义」：函数定义行里也含同样的名字，
        # 只搜裸字符串会把 def 当成调用点，断言就白写了。
        call = re.search(r"^\s+(?:atlas_http\s*=\s*)?start_embedded_atlas\(\)\s*$", src, re.M)
        self.assertIsNotNone(call, "main() 启动路径上必须真的调用图鉴")
        self.assertLess(call.start(), src.index("server.serve_forever()"),
                        "图鉴启动必须排在主循环之前，否则永远等不到")

    def test_atlas_can_be_disabled_for_tests(self):
        src = (SRC / "comfybatch_v2_app.py").read_text(encoding="utf-8")
        self.assertIn("COMFYBATCH_NO_ATLAS", src)

    def test_start_does_not_block_the_host(self):
        """bootstrap=background 下 start() 必须立刻返回 ——
        主程序启动不能被二十多秒的扫盘挡住。"""
        import atlas_server

        tmp = tempfile.mkdtemp()
        old_dir = atlas_server.DATA_DIR
        old_state = dict(atlas_server._state)
        atlas_server.DATA_DIR = tmp
        started = threading.Event()

        def slow_rebuild(*a, **k):
            started.wait(3)                # 假装在慢慢建索引
            return True, 1, 0.0

        port = free_port()
        srv = None
        try:
            with mock.patch.object(atlas_server, "rebuild", slow_rebuild):
                srv, _ = atlas_server.serve(port=port, bootstrap="background",
                                            watch_interval=0)
            self.assertIsNotNone(atlas_host.probe(port=port, timeout=1.0),
                                 "服务线程应当已经在线，哪怕索引还没建完")
        finally:
            started.set()
            if srv is not None:
                srv.shutdown()
                srv.server_close()
            atlas_server.DATA_DIR = old_dir
            atlas_server._state.clear()
            atlas_server._state.update(old_state)
            shutil.rmtree(tmp, ignore_errors=True)


class CorsTests(unittest.TestCase):
    """图鉴服务（8793）要答得出 CORS 预检 —— 这是加固，不是某个故障的修复。

    背景需要说清楚，否则很容易又被讲成"预检害的"：曾经怀疑
    ``fetch(...,{cache:'no-store'})`` 会让浏览器给请求补上 Cache-Control/Pragma
    从而触发 OPTIONS 预检，而本服务当时没有 ``do_OPTIONS``（BaseHTTPRequestHandler
    默认只回 501 且不带任何 ``Access-Control-*`` 头），预检失败 → fetch reject。
    用真浏览器实测后这个解释被推翻了：cache mode 自己加的头不算"作者自定义头"，
    浏览器**不发**预检，那条 fetch 9ms 就正常 resolve。真因是 S 前端启动序列踩了
    TDZ（见 tests/test_page_assets.py 的 BootOrderTests）。

    保留并钉住 ``do_OPTIONS`` 的理由是它本身就是对的：8793 是一个被 8790
    跨端口调用的 API，任何一天有人给请求加一个自定义头（比如将来带
    ``X-Client: s``），预检就会真的发出来，而答不出预检的服务就是个隐患。
    顺带把请求日志接上 —— 排查这次问题时最缺的证据恰恰是「浏览器到底发过什么」。
    """

    @classmethod
    def setUpClass(cls):
        import atlas_server

        cls.S = atlas_server

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._dir = self.S.DATA_DIR
        self._state = dict(self.S._state)
        self._log = self.S.Handler.log
        self.lines = []
        self.S.DATA_DIR = self.tmp

    def tearDown(self):
        self.S.DATA_DIR = self._dir
        self.S._state.clear()
        self.S._state.update(self._state)
        self.S.Handler.log = self._log
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _serve(self, port):
        """真起一个 8793 —— 预检是 HTTP 层的事，mock 掉就没有意义了。

        ``log`` 必须走参数传进去：``serve()`` 会用 ``Handler.log =
        staticmethod(log)`` **无条件覆盖**类属性，所以在 setUp 里提前挂一个
        测试 logger 是没用的 —— 它会在这一步被换成默认的 ``print``，
        断言拿到一个空列表，白通过。
        """
        def fake_rebuild(*a, **k):
            return True, 0, 0.0

        patcher = mock.patch.object(self.S, "rebuild", fake_rebuild)
        patcher.start()
        self.addCleanup(patcher.stop)
        srv, _ = self.S.serve(port=port, bootstrap="background", watch_interval=0,
                              log=self.lines.append)
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        return srv

    @staticmethod
    def _preflight(port):
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/version", method="OPTIONS",
            headers={"Origin": "http://127.0.0.1:8790",
                     "Access-Control-Request-Method": "GET",
                     "Access-Control-Request-Headers": "cache-control,pragma"})
        return urllib.request.urlopen(req, timeout=5)

    def test_preflight_is_answered(self):
        port = free_port()
        self._serve(port)
        with self._preflight(port) as r:
            self.assertEqual(204, r.status)
            self.assertEqual("*", r.headers.get("Access-Control-Allow-Origin"))
            self.assertIn("GET", r.headers.get("Access-Control-Allow-Methods") or "")
            self.assertIn("Cache-Control",
                          r.headers.get("Access-Control-Allow-Headers") or "")

    def test_preflight_leaves_a_log_line(self):
        """下一回再出问题，日志里必须能看出浏览器到底发了什么。"""
        port = free_port()
        self._serve(port)
        self.lines.clear()
        with self._preflight(port) as r:
            r.read()
        self.assertTrue(any("OPTIONS" in line for line in self.lines),
                        f"预检没进日志：{self.lines!r}")

    def test_thumbnail_requests_stay_out_of_the_log(self):
        """缩略图每页几十个 —— 记下来只会把有用的信息淹掉。"""
        port = free_port()
        self._serve(port)
        self.lines.clear()
        try:
            urllib.request.urlopen(
                f"http://127.0.0.1:{port}/thumb?h=deadbeef&s=t", timeout=5).read()
        except Exception:
            pass
        # 正向对照：同一条路径上，主接口必须留下痕迹。没有这一句的话，
        # 「日志里看不到 /thumb」也可能只是因为日志压根没接上。
        urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/version", timeout=5).read()
        self.assertTrue(any("api/version" in line for line in self.lines),
                        f"请求日志没接上：{self.lines!r}")
        self.assertFalse([line for line in self.lines if "/thumb" in line],
                         f"缩略图请求不该进日志：{self.lines!r}")


if __name__ == "__main__":
    unittest.main()
