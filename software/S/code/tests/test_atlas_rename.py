# -*- coding: utf-8 -*-
"""图片重命名接口 /api/rename 的行为契约。

这个接口是图鉴里**唯一**会真的动用户磁盘文件的入口，所以它出错的方式
比别的接口贵：改错名字至少还能改回来，但「改了名字而没改引用」会把
收藏和评级一起变成悬空引用 —— 用户看到的现象和「收藏的图不见了」一模一样，
而且这次的丢失是我们自己造成的。

因此这里盯三件事：
  1. 文件的改名本身：成功路径、后缀保护、不覆盖、只改大小写；
  2. 引用必须跟着走：_marks.json 与 _grades.json 里的 uid 一起搬，
     而且夹里的条数不变（不漏、不重）；
  3. 拒绝面：路径穿越、外来 Origin、非法字符。

测试用真 PNG 和真 HTTP 服务，所有数据落在临时目录里。
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import socket
import sys
import tempfile
import unittest
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
for _p in (str(ROOT / "tests"), str(ROOT / "src"), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import atlas_server as S  # noqa: E402

try:
    from PIL import Image
    HAVE_PIL = True
except Exception:                                     # noqa: BLE001
    HAVE_PIL = False


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def make_png(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Image.new("RGB", (40, 30), (10, 90, 160)).save(path, "PNG")


def post(port, route, payload, origin=None, timeout=10):
    """发一个 POST，返回 (status, 解析后的 body)。4xx 不抛异常。"""
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        "http://127.0.0.1:%d%s" % (port, route), data=data, method="POST",
        headers={"Content-Type": "application/json",
                 "Origin": origin if origin is not None
                 else "http://127.0.0.1:%d" % port})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw}


def get(port, route, timeout=10):
    with urllib.request.urlopen("http://127.0.0.1:%d%s" % (port, route),
                               timeout=timeout) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


@unittest.skipUnless(HAVE_PIL, "需要 Pillow 才能造真 PNG")
class RenameEndpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.imgs = os.path.join(self.tmp, "imgs")
        self.data = os.path.join(self.tmp, "data")
        os.makedirs(self.data, exist_ok=True)
        self.a = os.path.join(self.imgs, "a.png")
        self.b = os.path.join(self.imgs, "b.png")
        make_png(self.a)
        make_png(self.b)

        self._dir = S.DATA_DIR
        self._state = dict(S._state)
        self._log = S.Handler.log
        S.DATA_DIR = self.data
        S._state["data"] = self._payload()
        S._state["version"] = 0

    def tearDown(self):
        S.DATA_DIR = self._dir
        S._state.clear()
        S._state.update(self._state)
        S.Handler.log = self._log
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---------- 夹具 ----------
    def _payload(self):
        return {
            "build_id": "renametest",
            "build_label": "rename",
            "images": [
                {"path": self.a.replace("\\", "/"), "name": "a.png",
                 "uid": self.a.replace("\\", "/") + "#batch1#1",
                 "th": "aaaa1111aaaa1111", "size": 100, "run": "batch1",
                 "index": 1, "styles": [], "loras": [], "libs": [], "techs": []},
                {"path": self.b.replace("\\", "/"), "name": "b.png",
                 "uid": self.b.replace("\\", "/") + "#batch1#2",
                 "th": "bbbb2222bbbb2222", "size": 100, "run": "batch1",
                 "index": 2, "styles": [], "loras": [], "libs": [], "techs": []},
            ],
            "stats": {}, "facets": {}, "thumbs": {},
        }

    def old_uid_a(self):
        return self.a.replace("\\", "/") + "#batch1#1"

    def new_uid(self, name):
        return os.path.join(self.imgs, name).replace("\\", "/") + "#batch1#1"

    def write_marks(self, sets, active=None):
        obj = {"schema_version": 2, "sets": sets,
               "active": active or (sets[0]["id"] if sets else None)}
        with open(os.path.join(self.data, "_marks.json"), "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)

    def write_grades(self, grades):
        with open(os.path.join(self.data, "_grades.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"schema_version": 1, "grades": grades}, f,
                      ensure_ascii=False)

    def read_marks(self):
        with open(os.path.join(self.data, "_marks.json"), encoding="utf-8") as f:
            return json.load(f)

    def read_grades(self):
        with open(os.path.join(self.data, "_grades.json"), encoding="utf-8") as f:
            return json.load(f)["grades"]

    def serve(self):
        self.port = free_port()
        srv, _ = S.serve(port=self.port, bootstrap="background",
                         watch_interval=0, log=lambda *a, **k: None)
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        return srv

    # ---------- 1. 改名本身 ----------
    def test_rename_moves_the_file_and_reports_new_uid(self):
        self.write_marks([{"id": "s1", "name": "默认",
                           "paths": [self.old_uid_a()], "note": {}}])
        self.serve()
        st, j = post(self.port, "/api/rename",
                     {"path": self.a, "name": "c.png"})
        self.assertEqual(200, st, j)
        self.assertTrue(j["ok"], j)
        self.assertFalse(os.path.exists(self.a), "旧文件还在")
        self.assertTrue(os.path.exists(os.path.join(self.imgs, "c.png")))
        self.assertEqual("c.png", j["name"])
        self.assertTrue(j["uid"].endswith("#batch1#1"), j["uid"])
        self.assertIn("c.png", j["uid"])

    def test_extension_is_kept_when_only_the_stem_is_given(self):
        self.serve()
        st, j = post(self.port, "/api/rename", {"path": self.a, "name": "改个名"})
        self.assertEqual(200, st, j)
        self.assertEqual("改个名.png", j["name"])

    def test_extension_cannot_be_changed(self):
        """后缀参与「这是不是一张图」的判断，不许借改名换掉。"""
        self.serve()
        st, j = post(self.port, "/api/rename", {"path": self.a, "name": "a.jpg"})
        self.assertEqual(400, st)
        self.assertFalse(j["ok"])
        self.assertTrue(os.path.exists(self.a), "被拒绝的请求却动了文件")

    def test_existing_target_is_not_overwritten(self):
        self.serve()
        st, j = post(self.port, "/api/rename", {"path": self.a, "name": "b.png"})
        self.assertEqual(400, st)
        self.assertIn("同名", j["error"])
        self.assertTrue(os.path.exists(self.a) and os.path.exists(self.b),
                        "拒绝的请求不该动任何一个文件")

    def test_path_separator_is_rejected(self):
        """带了分隔符就能借改名把文件搬出扫描根 —— 必须挡住。"""
        self.serve()
        for bad in ("../x.png", "sub/x.png", "C:x.png", "sub\\x.png"):
            st, j = post(self.port, "/api/rename", {"path": self.a, "name": bad})
            self.assertEqual(400, st, bad)
            self.assertFalse(j["ok"], bad)

    def test_quote_characters_are_rejected(self):
        """页面里到处是 `onclick=\"f('${path}')\"` 的内联写法，
        文件名里的引号会把那些处理程序的语法拆掉。"""
        self.serve()
        for bad in ("a'b.png", 'a"b.png', "a&b.png", "a<b.png"):
            st, j = post(self.port, "/api/rename", {"path": self.a, "name": bad})
            self.assertEqual(400, st, bad)

    def test_missing_source_is_reported_not_crashed(self):
        self.serve()
        st, j = post(self.port, "/api/rename",
                     {"path": os.path.join(self.imgs, "没有这张.png"),
                      "name": "x.png"})
        self.assertEqual(400, st)
        self.assertIn("不存在", j["error"])

    def test_same_name_is_a_noop_not_an_error_on_disk(self):
        self.serve()
        st, j = post(self.port, "/api/rename", {"path": self.a, "name": "a.png"})
        self.assertEqual(400, st)
        self.assertTrue(os.path.exists(self.a))

    def test_case_only_rename_works_on_windows(self):
        """只改大小写时，大小写不敏感的文件系统认为目标已经存在。
        不绕中转名的话，这里会以「同名文件」为由被拒 —— 而用户什么也没做错。"""
        self.serve()
        st, j = post(self.port, "/api/rename", {"path": self.a, "name": "A.PNG"})
        self.assertEqual(200, st, j)
        self.assertEqual("A.PNG", j["name"])
        names = os.listdir(self.imgs)
        self.assertIn("A.PNG", names)
        self.assertNotIn("a.png", names)

    # ---------- 2. 引用必须跟着走 ----------
    def test_marks_follow_the_rename(self):
        self.write_marks([
            {"id": "s1", "name": "默认", "paths": [self.old_uid_a()], "note": {}},
            {"id": "s2", "name": "另一夹",
             "paths": [self.old_uid_a(), self.b.replace("\\", "/") + "#batch1#2"],
             "note": {}},
        ])
        self.serve()
        st, j = post(self.port, "/api/rename", {"path": self.a, "name": "c.png"})
        self.assertEqual(200, st, j)
        self.assertEqual(2, j["marks_updated"])
        sets = {s["name"]: s["paths"] for s in self.read_marks()["sets"]}
        self.assertEqual([self.new_uid("c.png")], sets["默认"])
        self.assertEqual([self.new_uid("c.png"),
                          self.b.replace("\\", "/") + "#batch1#2"], sets["另一夹"])
        # 条数不许变（漏了就是丢收藏，多了就是重复计数）
        self.assertEqual(1, len(sets["默认"]))
        self.assertEqual(2, len(sets["另一夹"]))

    def test_marks_with_a_path_only_record_are_deduped(self):
        """同一个夹里可能同时存着 uid 一条、路径一条指向同一张图
        （历史迁移留下的）。改名把它们并成一条 —— 并完还留两条，
        夹里的数字就会比真实张数多。"""
        self.write_marks([{"id": "s1", "name": "默认",
                           "paths": [self.old_uid_a(),
                                     self.a.replace("\\", "/")],
                           "note": {}}])
        self.serve()
        st, j = post(self.port, "/api/rename", {"path": self.a, "name": "c.png"})
        self.assertEqual(200, st, j)
        paths = self.read_marks()["sets"][0]["paths"]
        self.assertEqual([self.new_uid("c.png")], paths)

    def test_grades_follow_the_rename(self):
        self.write_grades({self.old_uid_a(): {"g": 1},
                           self.b.replace("\\", "/") + "#batch1#2": {"g": 0}})
        self.serve()
        st, j = post(self.port, "/api/rename", {"path": self.a, "name": "c.png"})
        self.assertEqual(200, st, j)
        self.assertEqual(1, j["grades_updated"])
        g = self.read_grades()
        self.assertEqual({"g": 1}, g.get(self.new_uid("c.png")))
        self.assertNotIn(self.old_uid_a(), g)
        self.assertEqual({"g": 0}, g.get(self.b.replace("\\", "/") + "#batch1#2"),
                         "没改到的那条评级不该被动")

    def test_index_and_cache_follow_the_rename(self):
        """内存索引与落盘缓存都要更新 —— 否则重启后又变回旧路径，
        收藏再次指向一个不存在的文件。"""
        self.serve()
        st, j = post(self.port, "/api/rename", {"path": self.a, "name": "c.png"})
        self.assertEqual(200, st, j)
        ent = S._state["data"]["images"][0]
        self.assertEqual("c.png", ent["name"])
        self.assertTrue(ent["path"].endswith("/c.png"), ent["path"])
        self.assertEqual(j["uid"], ent["uid"])
        # 缩略图键故意保持不变（内容没变，缓存照样命中）
        self.assertEqual("aaaa1111aaaa1111", ent["th"])
        with open(os.path.join(self.data, "_data_cache.json"),
                  encoding="utf-8") as f:
            cached = json.load(f)
        self.assertTrue(cached["images"][0]["path"].endswith("/c.png"),
                        "落盘缓存没跟着改")

    # ---------- 3. 拒绝外来来源 ----------
    def test_foreign_origin_is_refused(self):
        """_send 对所有响应都加了 ACAO:*（给 S 前端跨端口探测用的）。
        不挡 Origin 的话，任何网页都能在 S 开着的时候改用户文件名。"""
        self.serve()
        st, j = post(self.port, "/api/rename",
                     {"path": self.a, "name": "evil.png"},
                     origin="https://evil.example.com")
        self.assertEqual(403, st)
        self.assertFalse(j["ok"])
        self.assertTrue(os.path.exists(self.a), "外来来源却改了文件")

    def test_null_origin_is_refused(self):
        self.serve()
        st, _j = post(self.port, "/api/rename", {"path": self.a, "name": "x.png"},
                      origin="null")
        self.assertEqual(403, st)
        self.assertTrue(os.path.exists(self.a))

    def test_local_origin_is_accepted(self):
        """正向对照：没有这一条，上面两个 403 可能只是因为接口根本不通。"""
        self.serve()
        st, j = post(self.port, "/api/rename", {"path": self.a, "name": "ok.png"},
                     origin="http://127.0.0.1:%d" % self.port)
        self.assertEqual(200, st, j)


@unittest.skipUnless(HAVE_PIL, "需要 Pillow 才能造真 PNG")
class MarksApiKeepsDeadRecordsTests(unittest.TestCase):
    """收藏接口不许再静默丢弃「文件已不在记录路径上」的记录。

    旧实现在 /api/marks 里直接过滤掉这些记录，制造了用户报的那个症状：
    夹标签上的数字来自前端本地副本（合并时又把过滤掉的并回去），数字不变，
    而图渲染不出来 —— 「收藏的图片不在了，但数量没变」。
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.imgs = os.path.join(self.tmp, "imgs")
        self.data = os.path.join(self.tmp, "data")
        os.makedirs(self.data, exist_ok=True)
        self.live = os.path.join(self.imgs, "live.png")
        make_png(self.live)
        self._dir = S.DATA_DIR
        self._state = dict(S._state)
        self._log = S.Handler.log
        S.DATA_DIR = self.data
        S._state["data"] = {"images": [], "stats": {}, "facets": {},
                            "thumbs": {}, "build_id": "x"}
        self.dead_uid = os.path.join(self.imgs, "gone.png").replace("\\", "/") + "#b#1"
        self.live_uid = self.live.replace("\\", "/") + "#b#1"
        with open(os.path.join(self.data, "_marks.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"schema_version": 2, "active": "s1", "sets": [
                {"id": "s1", "name": "默认",
                 "paths": [self.live_uid, self.dead_uid], "note": {}}]}, f)

    def tearDown(self):
        S.DATA_DIR = self._dir
        S._state.clear()
        S._state.update(self._state)
        S.Handler.log = self._log
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_dead_record_is_returned_and_flagged(self):
        port = free_port()
        srv, _ = S.serve(port=port, bootstrap="background", watch_interval=0,
                         log=lambda *a, **k: None)
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        st, m = get(port, "/api/marks")
        self.assertEqual(200, st)
        paths = m["sets"][0]["paths"]
        self.assertIn(self.dead_uid, paths,
                      "死记录被静默吃掉了 —— 用户既看不到也救不回来")
        self.assertIn(self.live_uid, paths)
        self.assertEqual([self.dead_uid], m["dead"])
        self.assertEqual(1, m["dropped"])          # 兼容旧字段名

    def test_dead_list_is_complete_across_sets(self):
        with open(os.path.join(self.data, "_marks.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"schema_version": 2, "active": "s1", "sets": [
                {"id": "s1", "name": "默认", "paths": [self.dead_uid], "note": {}},
                {"id": "s2", "name": "二", "paths": [self.dead_uid], "note": {}},
            ]}, f)
        port = free_port()
        srv, _ = S.serve(port=port, bootstrap="background", watch_interval=0,
                         log=lambda *a, **k: None)
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        _st, m = get(port, "/api/marks")
        # 两个夹各一条 —— dead 是「逐条上报」，不去重（去重会让夹级计数对不上）
        self.assertEqual(2, len(m["dead"]))


if __name__ == "__main__":
    unittest.main()
