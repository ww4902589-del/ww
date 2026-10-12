"""``comfybatch_launcher`` 的分组、断点续跑与组序测试。

这个模块承担的是「一次导入、按画幅分批跑完」这条能力，所以测试围绕三件事：

1. **分组键只认任务级画幅**，没有任务级规格的条目归到全局画幅；
2. **断点续跑的判据是磁盘上的图**，不是内存状态 —— 进程被杀掉后重来仍要对；
3. **一组失败不殃及下一组**，但要在结果里如实记下来。
"""

from __future__ import annotations

import pathlib
import sys
import tempfile
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import comfybatch_launcher as launcher  # noqa: E402
from comfybatch_v2_core import BatchConfig, PromptBundle, PromptItem  # noqa: E402


def make_bundle(specs: list[tuple[str, str | None]], *, serials: list[int] | None = None) -> PromptBundle:
    """按 ``(title, aspect_ratio)`` 造一个合集；``None`` 表示不设任务级画幅。"""
    items: list[PromptItem] = []
    for position, (title, aspect) in enumerate(specs, 1):
        metadata: dict = {}
        if serials:
            metadata["serial"] = serials[position - 1]
        else:
            metadata["serial"] = position
        if aspect:
            metadata["generation"] = {"aspect_ratio": aspect}
        items.append(PromptItem(title, "prompt text", metadata, ""))
    return PromptBundle("测试合集", items, "json")


def config(aspect: str = "3:2 (Photo)") -> BatchConfig:
    return BatchConfig(workflow_path="w.json", model="m.safetensors", aspect_ratio=aspect)


class AspectRatioKeyTests(unittest.TestCase):
    def test_full_label_collapses_to_ratio(self):
        self.assertEqual(launcher.aspect_ratio_key("4:5 (Portrait)"), "4:5")
        self.assertEqual(launcher.aspect_ratio_key("16:9 (Widescreen)"), "16:9")

    def test_tolerates_spacing_and_chinese_prefix(self):
        self.assertEqual(launcher.aspect_ratio_key("竖图 4:5"), "4:5")
        self.assertEqual(launcher.aspect_ratio_key("2 : 3"), "2:3")

    def test_unknown_label_stays_itself(self):
        # 认不出来时保留原串：自成一组的代价只是多起一次批次，而错误归并
        # 会把两种尺寸塞进同一批，那是静默出错。
        self.assertEqual(launcher.aspect_ratio_key("weird"), "weird")
        self.assertEqual(launcher.aspect_ratio_key(""), "")

    def test_canonical_list_derives_from_image_presets(self):
        # 必须来自主程序，不能是复制来的字面量，否则两边迟早对不上。
        canonical = launcher.canonical_aspect_ratios()
        self.assertIn("4:5 (Portrait)", canonical)
        self.assertIn("16:9 (Widescreen)", canonical)
        # 去重且顺序稳定
        self.assertEqual(len(canonical), len(set(canonical)))
        for label in canonical:
            self.assertNotIn("Landscape", label, "不存在的标签不该出现")


class SerialTests(unittest.TestCase):
    def test_explicit_serial_wins(self):
        item = PromptItem("TUP-9", "p", {"serial": 47}, "")
        self.assertEqual(launcher._item_serial(item, 1), 47)

    def test_title_number_is_used_when_serial_absent(self):
        item = PromptItem("TUP-47 设计", "p", {}, "")
        self.assertEqual(launcher._item_serial(item, 3), 47)

    def test_position_is_the_last_resort(self):
        item = PromptItem("没有编号", "p", {}, "")
        self.assertEqual(launcher._item_serial(item, 12), 12)


class DoneNumbersTests(unittest.TestCase):
    def test_reads_numbers_recursively_case_insensitively(self):
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            (root / "TUP-1.png").write_bytes(b"x")
            (root / "tup-2.PNG").write_bytes(b"x")
            nested = root / "sub"
            nested.mkdir()
            (nested / "TUP-7.png").write_bytes(b"x")
            (root / "无关文件.txt").write_text("TUP-99", encoding="utf-8")
            self.assertEqual(launcher.done_numbers(root), {1, 2, 7})

    def test_missing_directory_is_empty_not_error(self):
        self.assertEqual(launcher.done_numbers(pathlib.Path("Z:/不存在/目录")), set())


class PlanGroupsTests(unittest.TestCase):
    def test_groups_by_item_aspect_ratio(self):
        bundle = make_bundle([("a", "4:5 (Portrait)"), ("b", "1:1 (Square)"), ("c", "4:5 (Portrait)")])
        with tempfile.TemporaryDirectory() as raw:
            plans = launcher.plan_groups(bundle, config(), pathlib.Path(raw))
        keys = {plan.key: plan for plan in plans}
        self.assertEqual(sorted(keys), ["1:1", "4:5"])
        self.assertEqual(keys["4:5"].indexes, [1, 3])
        self.assertEqual(keys["1:1"].indexes, [2])

    def test_items_without_item_ratio_fall_back_to_global(self):
        bundle = make_bundle([("a", None), ("b", "1:1 (Square)")])
        with tempfile.TemporaryDirectory() as raw:
            plans = launcher.plan_groups(bundle, config("3:2 (Photo)"), pathlib.Path(raw))
        keys = {plan.key: plan for plan in plans}
        self.assertIn("3:2", keys)
        self.assertEqual(keys["3:2"].indexes, [1])

    def test_order_parameter_controls_group_order(self):
        bundle = make_bundle([("a", "1:1 (Square)"), ("b", "4:5 (Portrait)"), ("c", "16:9 (Widescreen)")])
        with tempfile.TemporaryDirectory() as raw:
            plans = launcher.plan_groups(
                bundle, config(), pathlib.Path(raw), order=["4:5", "16:9"]
            )
        self.assertEqual([plan.key for plan in plans], ["4:5", "16:9", "1:1"])

    def test_pending_excludes_numbers_already_on_disk(self):
        bundle = make_bundle([("TUP-1", "4:5 (Portrait)"), ("TUP-2", "4:5 (Portrait)")])
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            (root / "TUP-1.png").write_bytes(b"x")
            plans = launcher.plan_groups(bundle, config(), root)
        self.assertEqual(plans[0].indexes, [1, 2])
        self.assertEqual(plans[0].pending, [2])

    def test_resume_is_disk_based_not_memory_based(self):
        """断点续跑的核心：换一个全新的 planner，仍然只看磁盘。"""
        bundle = make_bundle([("TUP-1", "4:5 (Portrait)"), ("TUP-2", "4:5 (Portrait)"), ("TUP-3", "4:5 (Portrait)")])
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            (root / "TUP-1.png").write_bytes(b"x")
            (root / "TUP-3.png").write_bytes(b"x")
            # 第二次调用完全不共享任何内存状态
            plans = launcher.plan_groups(bundle, config(), root)
        self.assertEqual(plans[0].pending, [2])


class SubsetTests(unittest.TestCase):
    def test_subset_keeps_only_pending_and_preserves_metadata(self):
        bundle = make_bundle([("TUP-1", "4:5 (Portrait)"), ("TUP-2", "4:5 (Portrait)")])
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            (root / "TUP-1.png").write_bytes(b"x")
            plan = launcher.plan_groups(bundle, config(), root)[0]
        subset = launcher.subset_bundle(bundle, plan)
        self.assertEqual([item.title for item in subset.items], ["TUP-2"])
        # 元数据要原样带过去，编号在里面
        self.assertEqual(subset.items[0].metadata["serial"], 2)

    def test_resolve_subset_applies_presets_after_trimming(self):
        bundle = make_bundle([("TUP-1", "4:5 (Portrait)"), ("TUP-2", "4:5 (Portrait)")])
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            (root / "TUP-1.png").write_bytes(b"x")
            plan = launcher.plan_groups(bundle, config(), root)[0]

        calls = {"count": 0}

        def resolve() -> PromptBundle:
            calls["count"] += 1
            items = []
            for item in bundle.items:
                metadata = dict(item.metadata)
                metadata["generation"] = {**metadata.get("generation", {}), "loras": ["x"]}
                items.append(PromptItem(item.title, item.prompt, metadata, item.negative_prompt))
            return PromptBundle(bundle.name, items, bundle.source_format)

        resolved = launcher.resolve_subset(resolve, bundle, plan)
        self.assertEqual(calls["count"], 1)
        self.assertEqual([item.title for item in resolved.items], ["TUP-2"])
        self.assertEqual(resolved.items[0].metadata["generation"]["loras"], ["x"])


class StatusFailedTests(unittest.TestCase):
    def test_threshold_boundary(self):
        self.assertFalse(launcher.status_failed(0, 3))
        self.assertFalse(launcher.status_failed(2, 3))
        self.assertTrue(launcher.status_failed(3, 3))

    def test_zero_threshold_still_requires_one_failure(self):
        self.assertFalse(launcher.status_failed(0, 0))
        self.assertTrue(launcher.status_failed(1, 0))


class PreviewTests(unittest.TestCase):
    def test_preview_counts_total_and_pending(self):
        bundle = make_bundle([("TUP-1", "4:5 (Portrait)"), ("TUP-2", "1:1 (Square)")])
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            (root / "TUP-1.png").write_bytes(b"x")
            payload = launcher.BatchLauncher().preview(bundle, config(), root)
        self.assertEqual(payload["group_count"], 2)
        self.assertEqual(payload["total"], 2)
        self.assertEqual(payload["pending"], 1)


class LauncherLifecycleTests(unittest.TestCase):
    def test_busy_reflects_lifecycle(self):
        engine = launcher.BatchLauncher()
        self.assertFalse(engine.busy)
        self.assertEqual(engine.status()["status"], "idle")

    def test_start_rejects_when_nothing_pending(self):
        bundle = make_bundle([("TUP-1", "4:5 (Portrait)")])
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            (root / "TUP-1.png").write_bytes(b"x")
            engine = launcher.BatchLauncher()
            with self.assertRaises(ValueError):
                engine.start(
                    runner=object(),
                    bundle=bundle,
                    config=config(),
                    resolve=lambda: bundle,
                    output_dir=root,
                )

    def test_start_refuses_concurrent_runs(self):
        """上一轮没结束就再点一次，必须报错而不是静默排队。"""
        engine = launcher.BatchLauncher()
        engine._state["status"] = "running"
        with self.assertRaises(ValueError):
            engine.start(
                runner=object(),
                bundle=make_bundle([("TUP-1", "4:5 (Portrait)")]),
                config=config(),
                resolve=lambda: PromptBundle("b", [], "json"),
                output_dir=pathlib.Path("."),
            )


class ScriptedRunner:
    """最小的 runner 替身：按组顺序报告 running → completed。

    它记录每次 ``start`` 收到的合集，让测试能断言「每组提交的是哪些编号」，
    而不是只看最终状态 —— 分批的价值就在「谁在什么时候跑」，只断言终态会把
    顺序错误漏掉。
    """

    def __init__(self) -> None:
        self.submitted: list[list[str]] = []
        self.aspect_ratios: list[str] = []
        self.output_dirs: list[str] = []
        self._status = {"status": "idle", "submitted": 0, "errors": 0}

    def start(self, bundle: PromptBundle, group_config: BatchConfig) -> dict:
        self.submitted.append([item.title for item in bundle.items])
        self.aspect_ratios.append(group_config.aspect_ratio)
        self.output_dirs.append(group_config.output_dir)
        self._status = {"status": "completed", "submitted": len(bundle.items), "errors": 0}
        return dict(self._status)

    def status(self) -> dict:
        return dict(self._status)

    def cancel(self) -> dict:
        self._status = {**self._status, "status": "cancelled"}
        return dict(self._status)


class SequentialGroupTests(unittest.TestCase):
    """端到端：真实跑一遍分组循环（不碰 ComfyUI，只替身 runner）。"""

    def _run(self, specs, *, existing=(), order=None):
        engine = launcher.BatchLauncher()
        bundle = make_bundle(specs)
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            for number in existing:
                (root / f"TUP-{number}.png").write_bytes(b"x")
            runner = ScriptedRunner()
            engine.start(
                runner=runner,
                bundle=bundle,
                config=config("16:9 (Widescreen)"),
                resolve=lambda: bundle,
                output_dir=root,
                gap_seconds=0.0,
            )
            deadline = time.time() + 10
            while engine.busy and time.time() < deadline:
                time.sleep(0.02)
            # 等到线程真正退出，避免断言和数据竞争。
            if engine._thread is not None:
                engine._thread.join(timeout=5)
            return engine.status(), runner

    def test_each_group_is_submitted_once_in_order(self):
        status, runner = self._run([
            ("TUP-1", "4:5 (Portrait)"),
            ("TUP-2", "4:5 (Portrait)"),
            ("TUP-3", "1:1 (Square)"),
        ])
        # 两组，各提交一次；同组的两条在同一批里。
        self.assertEqual(len(runner.submitted), 2)
        self.assertEqual(runner.submitted[0], ["TUP-1", "TUP-2"])
        self.assertEqual(runner.submitted[1], ["TUP-3"])
        self.assertEqual(status["status"], "completed")
        self.assertEqual(status["group_count"], 2)

    def test_group_config_carries_the_groups_aspect_ratio(self):
        _, runner = self._run([("TUP-1", "4:5 (Portrait)"), ("TUP-2", "1:1 (Square)")])
        self.assertEqual(runner.aspect_ratios, ["4:5 (Portrait)", "1:1 (Square)"])

    def test_already_generated_numbers_are_skipped_per_group(self):
        status, runner = self._run(
            [("TUP-1", "4:5 (Portrait)"), ("TUP-2", "4:5 (Portrait)"), ("TUP-3", "1:1 (Square)")],
            existing=[1],
        )
        # 第一组只剩 TUP-2；第二组照跑。
        self.assertEqual(runner.submitted, [["TUP-2"], ["TUP-3"]])
        self.assertEqual(status["status"], "completed")

    def test_a_fully_generated_group_is_not_submitted(self):
        _, runner = self._run(
            [("TUP-1", "4:5 (Portrait)"), ("TUP-2", "1:1 (Square)")],
            existing=[1],
        )
        self.assertEqual(runner.submitted, [["TUP-2"]])

    def test_group_ratio_is_still_in_effect_when_generation_meta_absent(self):
        """任务级没写画幅时，分组落到全局画幅；组配置也要写回该画幅。"""
        _, runner = self._run([("TUP-1", None)])
        self.assertEqual(runner.submitted, [["TUP-1"]])
        self.assertEqual(runner.aspect_ratios, ["16:9 (Widescreen)"])


if __name__ == "__main__":
    unittest.main()
