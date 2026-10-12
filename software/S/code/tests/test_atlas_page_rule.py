# -*- coding: utf-8 -*-
"""渲染产物版本戳（``build_atlas.RENDER_RULE``）的行为契约。

## 为什么需要这个护栏

`风格图鉴.html` 是一份**派生文件**：由 `build_atlas.py` 的模板 + 数据渲染出来，
落在数据目录里，服务端 `GET /` 直接把它端给浏览器。

问题就在这里：换了 exe（里面是新的模板）之后，磁盘上那份**旧模板渲染的页面
依然存在**。服务端原来只判「文件在不在」，于是原样返回旧页面 ——
用户在升级后看到的还是旧界面，会以为改动没生效。**这类「静默不生效」
比报错难查得多**，因为它没有任何错误信号。

真机上确实碰上了：部署 V2.34（查看器改成左右分栏）之后，`GET /` 回来的
仍然是没有 `.vstage` 的旧页面 —— 那份页面是几分钟前还在跑的旧版 exe
在自动重建时写下的（mtime 21:02）。

## 与 FOLDER_RULE 的分工

* `style_atlas.FOLDER_RULE` 管「数据里的派生列怎么算」（folder / bigfolder）；
* `build_atlas.RENDER_RULE` 管「页面长什么样」（模板 / CSS / 前端 JS）。

两者都是「改了规则就要让旧产物失效」，但失效的对象不同：
一个是 `_data_cache.json`，一个是 `风格图鉴.html`。
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile
import threading
import unittest
import urllib.request
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import atlas_server as S         # noqa: E402
import build_atlas as B          # noqa: E402

META_RE = r'<meta name="atlas-render" content="(\d+)">'


def free_port() -> int:
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def tiny_payload():
    """够 render_html 跑完就行 —— 它只做占位符替换，不做结构校验。"""
    return {"images": [{"uid": "u", "path": "p", "th": "h"}],
            "thumbs": {"ok": False, "dir": ""},
            "build_id": "test", "build_label": "test"}


def snapshot_state(case):
    """把 atlas_server._state 整份存下来、测完还原。

    **必须整份还原，不能只还 data。** 第一版只把 data 置回 None，忘了 building：
    于是 `_state["building"]` 一直是 True，下游 `test_atlas_host` 里的
    `rebuild()` 撞上开头那句
        if _state["building"]: return False, ...
    直接短路返回 False —— 一个看着毫不相干的测试挂了。
    这类跨模块的全局污染最难查，所以入口就整份快照。
    """
    with S._state_lock:
        saved = dict(S._state)
    case.addCleanup(lambda: S._state.update(saved))


class RenderRuleTests(unittest.TestCase):
    """版本戳本身：写得进、读得回、读不到要能被识别为「读不到」。"""

    def test_rule_is_a_positive_int(self):
        self.assertIsInstance(B.RENDER_RULE, int)
        self.assertGreaterEqual(B.RENDER_RULE, 1)

    def test_render_html_stamps_the_rule(self):
        html = B.render_html(tiny_payload(), 8)
        self.assertIn('<meta name="atlas-render" content="%d">' % B.RENDER_RULE, html)

    def test_version_lands_in_first_2kb(self):
        """服务端只读开头 2KB 做判断 —— meta 必须在里面，否则判断永远失败。"""
        html = B.render_html(tiny_payload(), 8)
        pos = html.index('name="atlas-render"')
        self.assertLess(pos, 2048, "版本戳跑到 2KB 之外，page_render_rule 会读不到")

    def test_page_render_rule_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "风格图鉴.html")
            with open(p, "w", encoding="utf-8") as f:
                f.write(B.render_html(tiny_payload(), 8))
            self.assertEqual(B.page_render_rule(p), B.RENDER_RULE)

    def test_old_page_without_stamp_reads_as_none(self):
        """旧版渲染的页面里没有这个 meta —— 必须被判为「过期」，而不是碰巧相等。"""
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "风格图鉴.html")
            with open(p, "w", encoding="utf-8") as f:
                f.write("<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
                        "<title>旧页面</title></head><body>老模板</body></html>")
            self.assertIsNone(B.page_render_rule(p))

    def test_missing_file_reads_as_none(self):
        self.assertIsNone(B.page_render_rule(os.path.join(
            tempfile.gettempdir(), "__definitely_not_here__.html")))

    def test_placeholder_never_leaks(self):
        """模板里的占位符漏替换的话，浏览器里就是一句字面量。"""
        html = B.render_html(tiny_payload(), 8)
        self.assertNotIn("__RENDER_RULE__", html)
        self.assertNotIn("__RENDER_META__", html)


class StalePageNotServedTests(unittest.TestCase):
    """端到端：磁盘上放着旧页面时，`GET /` 到底端出什么。"""

    def setUp(self):
        snapshot_state(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.data = self.tmp.name
        self.page = os.path.join(self.data, "风格图鉴.html")
        self.old = ("<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
                    "<title>旧模板</title></head><body><div id=\"OLD_TEMPLATE_MARK\"></div>"
                    "</body></html>")
        with open(self.page, "w", encoding="utf-8") as f:
            f.write(self.old)

        self._patches = [
            mock.patch.object(S, "DATA_DIR", self.data),
            # 重建要扫全盘（150 秒），测试里不需要 —— / 这条路径与它无关。
            mock.patch.object(S, "rebuild", return_value=(True, 1, 0.0)),
            # 绝不能因为跑测试而改用户桌面上的副本。
            mock.patch.object(S, "_sync_desktop_copy", return_value=None),
        ]
        for p in self._patches:
            p.start()
        self.addCleanup(self.tmp.cleanup)
        for p in reversed(self._patches):
            self.addCleanup(p.stop)

        self.server, _ = S.serve(port=free_port(), bootstrap="sync",
                                 watch_interval=0, log=lambda *a: None)
        self.port = self.server.server_address[1]
        # 只 shutdown 不 close 的话，socket 留到进程退出才回收，
        # 一跑测试就刷一屏 ResourceWarning，把真正的告警淹掉。
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def _get(self):
        with urllib.request.urlopen("http://127.0.0.1:%d/" % self.port, timeout=10) as r:
            return r.status, r.read().decode("utf-8", "replace")

    def _set_state(self, data, building=False):
        with S._state_lock:
            S._state["data"] = data
            S._state["building"] = building

    def test_stale_page_is_rerendered_not_served(self):
        self._set_state(tiny_payload())
        status, html = self._get()
        self.assertEqual(status, 200)
        self.assertIn('<meta name="atlas-render" content="%d">' % B.RENDER_RULE, html)
        self.assertNotIn("OLD_TEMPLATE_MARK", html,
                         "把旧模板渲染的页面端出去了 —— 用户会以为改动没生效")

    def test_rerender_also_fixes_the_file_on_disk(self):
        """不能只在内存里现渲一次：下一次 / 又要重来，且桌面副本会一直是旧的。"""
        self._set_state(tiny_payload())
        self._get()
        self.assertEqual(B.page_render_rule(self.page), B.RENDER_RULE)

    def test_stale_page_never_leaks_while_data_missing(self):
        """没数据可渲时退到等待页，而不是「凑合端旧的」。"""
        self._set_state(None, building=True)
        status, html = self._get()
        self.assertEqual(status, 200)
        self.assertIn("正在建立索引", html)
        self.assertNotIn("OLD_TEMPLATE_MARK", html)

    def test_fresh_page_is_served_as_is(self):
        """已经是对的版本就别每请求重渲一遍（render_html 虽快，也不该白跑）。"""
        self._set_state(tiny_payload())
        self._get()                                   # 先把它修好
        fresh = B.render_html(tiny_payload(), 8)
        with open(self.page, "w", encoding="utf-8") as f:
            f.write(fresh + "<!--SENTINEL-->")
        status, html = self._get()
        self.assertEqual(status, 200)
        self.assertIn("SENTINEL", html, "版本一致时不该重渲，应当原样端出")


class PreloadRuleTests(unittest.TestCase):
    """预载路径：上次的数据能用、但页面是旧模板时，也要重渲。"""

    def test_preload_rerenders_stale_page(self):
        snapshot_state(self)
        with tempfile.TemporaryDirectory() as tmp:
            page = os.path.join(tmp, "风格图鉴.html")
            with open(page, "w", encoding="utf-8") as f:
                f.write("<!doctype html><html><head><title>旧模板</title></head></html>")
            cache = os.path.join(tmp, "_data_cache.json")
            with open(cache, "w", encoding="utf-8") as f:
                json.dump({"images": [{"uid": "u", "path": "p"}],
                           "thumbs": {"ok": False, "dir": ""},
                           "folder_rule": S.A.FOLDER_RULE,
                           "image_sources": S.Sources.load(tmp)}, f)

            logs = []
            with mock.patch.object(S, "DATA_DIR", tmp), \
                 mock.patch.object(S, "_data_cache_path", return_value=cache), \
                 mock.patch.object(S, "_sync_desktop_copy", return_value=None), \
                 mock.patch.object(S.Handler, "out_dir", tmp):
                # _preload 里用的是模块级 DATA_DIR
                ok = S._preload(8, logs.append)

            self.assertTrue(ok, "数据能用就该预载成功")
            self.assertEqual(B.page_render_rule(page), B.RENDER_RULE,
                             "预载路径漏了重渲，旧页面会一直留在磁盘上")
            self.assertTrue(any("旧模板" in m for m in logs),
                            "重渲这件事该留下日志，否则升级后又是一个静默")


if __name__ == "__main__":
    unittest.main()
