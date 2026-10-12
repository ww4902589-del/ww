# -*- coding: utf-8 -*-
"""派生结果缓存（`style_atlas` 里那一套）的行为契约。

这套缓存是 build 从二十来秒降到几秒的关键，但它的失败方式是**静默的**：
key 撞了不会报错，只会让某个查询读到另一个查询的答案。实际踩过一次 ——
`image_dimensions` 读到 `png_prompt_text` 存下的 JSON 字符串，把前两个字符
当宽高，`is_sane_image` 判失败，图鉴里少了三千多张图，而日志一声不吭。

所以这里盯的第一件事就是「同一个文件问不同的事，答案不许互相覆盖」。
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import style_atlas as A          # noqa: E402

try:
    from PIL import Image, PngImagePlugin
    HAVE_PIL = True
except Exception:                                # noqa: BLE001
    HAVE_PIL = False


EXEC_GRAPH = json.dumps({
    "1": {"class_type": "CLIPTextEncode",
          "inputs": {"text": "一件水彩风格的洛丽塔连衣裙，墨线勾边，厚涂笔触" * 3}},
    "2": {"class_type": "easy stylesSelector",
          "inputs": {"styles": "krea2_anime_动漫_styles",
                     "select_styles": "Yoshitaka Amano Style"}},
    "3": {"class_type": "SaveImage", "inputs": {"filename_prefix": "x"}},
}, ensure_ascii=False)


def make_png(path, size=(64, 48), prompt=EXEC_GRAPH):
    info = PngImagePlugin.PngInfo()
    if prompt is not None:
        info.add_text("prompt", prompt)
    Image.new("RGB", size, (200, 30, 30)).save(path, "PNG", pnginfo=info)


@unittest.skipUnless(HAVE_PIL, "需要 Pillow 才能造带 tEXt 的 PNG")
class CacheIsolationTests(unittest.TestCase):
    """核心回归：四个查询共用 files bucket，key 必须分得开。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.png = os.path.join(self.tmp, "a.png")
        make_png(self.png)
        self._saved = (A.DATA_DIR, A._cache_buckets, A._cache_path, A._cache_dirty)
        A.DATA_DIR = self.tmp
        A._cache_buckets = None
        A._cache_path = None
        A._cache_dirty = False

    def tearDown(self):
        A.DATA_DIR, A._cache_buckets, A._cache_path, A._cache_dirty = self._saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_dimensions_survive_earlier_queries(self):
        """先问执行图原文、再问尺寸 —— 尺寸必须是真尺寸。

        这就是踩过的那个坑的最小复现：修复前这里返回的是 JSON 字符串的
        前两个字符，于是「这张图不是有效图片」。
        """
        self.assertEqual(A.png_prompt_text(self.png), EXEC_GRAPH)
        self.assertEqual(A.image_dimensions(self.png), (64, 48))
        self.assertTrue(A.is_sane_image(self.png))

    def test_order_does_not_matter(self):
        """反过来问一遍：先尺寸、后原文，两个都要对。"""
        self.assertEqual(A.image_dimensions(self.png), (64, 48))
        self.assertEqual(A.png_prompt_text(self.png), EXEC_GRAPH)
        self.assertTrue(A.is_sane_image(self.png))

    def test_positive_text_and_dimensions_coexist(self):
        """正面正文（另一件事）也不许盖掉尺寸。"""
        A.png_positive_text(self.png)
        self.assertEqual(A.image_dimensions(self.png), (64, 48))
        self.assertIn("洛丽塔", A.png_positive_text(self.png))

    def test_all_four_queries_together(self):
        """四件事轮流问两遍，每一遍答案都要一样且正确。"""
        styles = A.load_styles(A.DEFAULT_STYLES)
        by_name = A.build_name_index(styles)
        for _round in range(2):
            self.assertEqual(A.image_dimensions(self.png), (64, 48))
            self.assertEqual(A.png_prompt_text(self.png), EXEC_GRAPH)
            self.assertIn("洛丽塔", A.png_positive_text(self.png))
            libs, names, techs, cn = A.resolve_png_styles(self.png, styles, by_name)
            self.assertIsInstance(libs, list)
            self.assertIsInstance(names, list)

    def test_positive_text_of_a_file_having_none(self):
        """没有 tEXt 的图：正面正文空串，尺寸仍然要对。"""
        p = os.path.join(self.tmp, "b.png")
        make_png(p, size=(33, 22), prompt=None)
        self.assertEqual(A.png_positive_text(p), "")
        self.assertEqual(A.image_dimensions(p), (33, 22))


@unittest.skipUnless(HAVE_PIL, "需要 Pillow 才能造 PNG")
class CacheInvalidationTests(unittest.TestCase):
    """文件变了，账就得作废 —— 靠 (mtime, size) 自动作废，不靠人记得清。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.png = os.path.join(self.tmp, "a.png")
        self._saved = (A.DATA_DIR, A._cache_buckets, A._cache_path, A._cache_dirty)
        A.DATA_DIR = self.tmp
        A._cache_buckets = None
        A._cache_path = None
        A._cache_dirty = False

    def tearDown(self):
        A.DATA_DIR, A._cache_buckets, A._cache_path, A._cache_dirty = self._saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_dimensions_follow_the_file(self):
        make_png(self.png, size=(64, 48))
        self.assertEqual(A.image_dimensions(self.png), (64, 48))
        make_png(self.png, size=(120, 90))          # 同一个路径，换一份内容
        self.assertEqual(A.image_dimensions(self.png), (120, 90))

    def test_prompt_follows_the_file(self):
        make_png(self.png, prompt=EXEC_GRAPH)
        self.assertEqual(A.png_prompt_text(self.png), EXEC_GRAPH)
        make_png(self.png, prompt='{"1":{"class_type":"CLIPTextEncode",'
                                  '"inputs":{"text":"换了正文"}}}')
        self.assertIn("换了正文", A.png_prompt_text(self.png))

    def test_missing_file_is_not_cached(self):
        """文件不在时不该产生缓存条目 —— 否则它一旦出现，会读到空结果。"""
        gone = os.path.join(self.tmp, "nope.png")
        self.assertIsNone(A.image_dimensions(gone))
        self.assertEqual(A.png_prompt_text(gone), "")
        files, _text = A._cache()
        self.assertEqual([k for k in files if "nope.png" in k], [])


class TextCacheTests(unittest.TestCase):
    """文本缓存：同样的正文算一次就够，结果必须与直算一致。"""

    def setUp(self):
        self._saved = (A._cache_buckets, A._cache_path, A._cache_dirty)
        A._cache_buckets = ({}, {})
        A._cache_path = None
        A._cache_dirty = False

    def tearDown(self):
        A._cache_buckets, A._cache_path, A._cache_dirty = self._saved

    def _direct(self, text):
        return [d for d, pats in A._COMP.items()
                if any(p.search(text) for p in pats)]

    def test_cached_equals_direct(self):
        samples = [
            "",
            "一件水彩风格的洛丽塔连衣裙，墨线勾边",
            "oil painting, chiaroscuro, baroque",
            "3D render, octane, 三维渲染",
            "没有技法词的一串普通描述",
        ]
        for s in samples:
            first = A.tech_of(s)
            second = A.tech_of(s)              # 第二次走缓存
            self.assertEqual(first, second, s)
            self.assertEqual(first, self._direct(s), s)

    def test_empty_text_short_circuits(self):
        self.assertEqual(A.tech_of(""), [])

    def test_signature_tracks_content(self):
        base = {("lib", "词条A"): {"prompt": "xxx"}, ("lib", "词条B"): {"prompt": "yyy"}}
        changed = {("lib", "词条A"): {"prompt": "xxx"}, ("lib", "词条B"): {"prompt": "ZZZ"}}
        self.assertNotEqual(A.style_signature(base), A.style_signature(changed))
        self.assertEqual(A.style_signature(base), A.style_signature(dict(base)))


class FlushTests(unittest.TestCase):
    """落盘：写出去的必须能读回来，读坏的文件不能把服务带崩。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._saved = (A.DATA_DIR, A._cache_buckets, A._cache_path, A._cache_dirty)
        A.DATA_DIR = self.tmp
        A._cache_buckets = None
        A._cache_path = None
        A._cache_dirty = False

    def tearDown(self):
        A.DATA_DIR, A._cache_buckets, A._cache_path, A._cache_dirty = self._saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_roundtrip(self):
        A._cput(1, "abc123", ["水彩·晕染"])
        A._cput(0, "dim|/x/y.png|1:2", [4, 5])
        A.cache_flush()
        A._cache_buckets = None                # 假装重启
        A._cache_path = None
        files, text = A._cache()
        self.assertEqual(text.get("abc123"), ["水彩·晕染"])
        self.assertEqual(files.get("dim|/x/y.png|1:2"), [4, 5])

    def test_corrupt_cache_degrades_to_empty(self):
        pathlib.Path(self.tmp, "_derive_cache.json").write_text("{ 这不是 json",
                                                                encoding="utf-8")
        A._cache_buckets = None
        A._cache_path = None
        self.assertEqual(A._cache(), ({}, {}))

    def test_schema_mismatch_is_ignored(self):
        pathlib.Path(self.tmp, "_derive_cache.json").write_text(
            json.dumps({"schema": A._CACHE_SCHEMA + 99, "files": {"k": [1]},
                        "text": {"t": [2]}}), encoding="utf-8")
        A._cache_buckets = None
        A._cache_path = None
        self.assertEqual(A._cache(), ({}, {}))


if __name__ == "__main__":
    unittest.main()
