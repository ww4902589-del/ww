# -*- coding: utf-8 -*-
"""「文件夹分类」这条派生规则的契约。

分类标签要回答的是「这张图在磁盘上的哪个文件夹里」。它出过一次真实故障：

    磁盘上  Desktop/D/T/926266f5/001-….png
    旧分类  D/926266f5          ← 中间的 T 被吞了
    用户按磁盘上的 T 去分类里找 → 界面上根本没有 T 这一项

根因是旧实现「从下往上找第一个看起来像批次目录的段」（8 位 hex / 含
轮批套组 / 含中文 / 带日期），`926266f5` 命中 hex 就直接返回，`T` 因为
不命中任何规则被跳过。所以这组测试盯两件事：

1. **标签必须真的是路径的一部分**（`/label/` 出现在原路径里）——
   这是「真实目录层级」这个需求的形式化表达，比逐个断言字符串更难被绕过；
2. 用户报的那个具体路径必须落成 `D/T/926266f5`，且 `T` 可见。

第 1 条是这次真正的护栏：任何人再把「猜哪一段像批次」写回去，它立刻会红。
"""
from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import style_atlas as A          # noqa: E402


def norm(p: str) -> str:
    return "/" + str(p).replace("\\", "/").strip("/") + "/"


class FolderRuleTests(unittest.TestCase):
    """具体路径 → 标签。"""

    def test_reported_case_keeps_the_intermediate_level(self):
        """用户报的那条：T 不能被吞掉。"""
        p = "C:/Users/wyj/Desktop/D/T/926266f5/001-01_第1批服装1-01_00001_.png"
        self.assertEqual(A.folder_of(p), "D/T/926266f5")

    def test_second_run_under_the_same_parent(self):
        """同一天同一父目录下的另一个批次（同批 94 张里的另外 2 张）。"""
        p = "C:/Users/wyj/Desktop/D/T/a118df08/x.png"
        self.assertEqual(A.folder_of(p), "D/T/a118df08")

    def test_desktop_relative_paths_keep_their_first_level(self):
        """搬家整理后的那棵树（Desktop 下辖 c/D、c/E…）——与旧值保持一致。"""
        self.assertEqual(A.folder_of("C:/Users/wyj/Desktop/c/D/x.png"), "c/D")
        self.assertEqual(
            A.folder_of("C:/Users/wyj/Desktop/c/D/21/x.png"), "c/D/21")

    def test_other_scan_roots(self):
        self.assertEqual(A.folder_of("E:/工作流/xxx/y.png"), "xxx")
        self.assertEqual(A.folder_of("D:/liu/2026-01-01/abc/y.png"),
                         "2026-01-01/abc")

    def test_same_named_dirs_in_different_roots_stay_apart(self):
        """磁盘上确有 22 组同名目录，撞在一起就分不清是哪一批。"""
        a = A.folder_of("C:/Users/wyj/Desktop/D/34393175/x.png")
        b = A.folder_of("C:/Users/wyj/Desktop/c/34393175/x.png")
        self.assertEqual(a, "D/34393175")
        self.assertEqual(b, "c/34393175")
        self.assertNotEqual(a, b)

    def test_degenerate_inputs_never_yield_an_empty_label(self):
        """标签为空会让筛选按钮变成空白方块 —— 边界输入也得有话说。"""
        for p in ("", "/", "x.png", "C:/x.png",
                  "C:/Users/wyj/Desktop/x.png",
                  "C:/Users/wyj/Desktop/D/x.png",
                  r"C:\Users\wyj\Desktop\D\T\926266f5\x.png"):
            lab = A.folder_of(p)
            self.assertTrue(lab, f"{p!r} 的分类标签是空的")
            self.assertFalse(lab.startswith("/") or lab.endswith("/"),
                             f"{p!r} 的标签带了多余斜杠：{lab!r}")

    def test_backslashes_are_normalized(self):
        """Windows 路径两种分隔符都可能来（os.walk 用反斜杠）。"""
        self.assertEqual(A.folder_of(r"C:\Users\wyj\Desktop\D\T\926266f5\x.png"),
                         "D/T/926266f5")


class LabelIsARealSubpathTests(unittest.TestCase):
    """护栏：标签必须是原路径的一段连续目录链，不是猜出来的近似值。

    旧实现产出的 `D/926266f5` 在 `…/Desktop/D/T/926266f5/…` 里**不是**连续子串，
    这条断言会红 —— 正是想要的效果。
    """

    SAMPLES = [
        "C:/Users/wyj/Desktop/D/T/926266f5/001.png",
        "C:/Users/wyj/Desktop/c/D/087-提示词_087_00001_.png",
        "C:/Users/wyj/Desktop/c/HS/051-提示词_051_00001_.png",
        "C:/Users/wyj/Desktop/D/1c0c149f/036-提示词_036_00001_.png",
        "C:/Users/wyj/Desktop/c/2/52f1b4802d3ae4415e7983254319d56c.jpg",
        "C:/Users/wyj/Desktop/c/ComfyUI集中整理_2026-09-02至2026-09-03/"
        "Krea2宣传片人物_三轮/第2轮/2-2.png",
        "C:/Users/wyj/Desktop/c/D/S-v2.14/run/probe-out/exp_4v10a_00009.png",
        "E:/工作流/xxx/y.png",
        "D:/liu/2026-01-01/abc/y.png",
    ]

    def test_label_appears_verbatim_in_the_source_path(self):
        for p in self.SAMPLES:
            lab = A.folder_of(p)
            self.assertIn("/" + lab + "/", norm(p),
                          f"分类 {lab!r} 不是 {p!r} 的真实子路径")

    def test_label_never_contains_the_scan_root_itself(self):
        """`Desktop/…` 前缀没意义（所有图都在它下面），不该出现在标签里。"""
        for p in self.SAMPLES:
            lab = A.folder_of(p)
            for root in A._ROOT_HINTS:
                self.assertNotEqual(lab, root)
                self.assertFalse(lab.startswith(root + "/"),
                                 f"{p!r} 的标签带上了扫描根：{lab!r}")


class FolderRuleVersionTests(unittest.TestCase):
    """folder 是派生列，改了规则要能「作废重算」而不是端旧值。"""

    def test_rule_version_is_exposed_for_the_data_cache_guard(self):
        self.assertIsInstance(A.FOLDER_RULE, int)
        self.assertGreater(A.FOLDER_RULE, 0)

    def test_rule_version_is_part_of_the_payload(self):
        """payload 里必须带 folder_rule —— 服务端预载时校验的就是它。

        这里读源码而不是跑一次 build（build 要扫上万张图，测试里不合适）。
        断言的是「这个常量确实被写进了 data」，改动把它删掉会红。
        """
        src = (ROOT / "tools" / "style_atlas.py").read_text(encoding="utf-8")
        self.assertIn('"folder_rule": FOLDER_RULE', src)
        # 还要喂进 build_id 指纹：rules 变了但 build_id 不变的话，前端轮询
        # 发现不到「数据变了」，会一直显示按旧规则分的类。
        self.assertIn('"folder_rule=%d" % FOLDER_RULE', src)


if __name__ == "__main__":
    unittest.main()
