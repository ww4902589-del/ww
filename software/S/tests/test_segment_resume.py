"""分段生成、下一段与断点续跑（任务清单第 6 项）。

Three behaviours are pinned here:

* 分段生成 — a batch stops after every ``segment_size`` items instead of running
  to the end, and the queue keeps owning the slot while it waits.
* 下一段 — ``next_segment`` releases exactly one boundary, and a stray call
  cannot arm the gate for the next one.
* 断点续跑 — the progress snapshot is complete enough that a *fresh*
  ``BatchRunner`` continues the batch without regenerating finished items.

The fakes are the ones ``test_stop_loss.py`` already uses, so these run offline
and never touch a real ComfyUI.
"""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from fakes import (  # noqa: E402
    make_comfy_tree,
    ui_workflow,
    write_workflow,
)

from comfybatch_errors import ComfyError  # noqa: E402
from comfybatch_gateway import FakeComfyGateway  # noqa: E402
from comfybatch_v2_core import (  # noqa: E402
    ACTIVE_STATUSES,
    BatchConfig,
    BatchRunner,
    PromptBundleParser,
    RunProgressStore,
)

#: Statuses that mean the batch will not do anything else on its own.
TERMINAL = {"completed", "cancelled", "aborted", "error"}
#: ``segment`` is a resting state too, but it is *reversible*: the queue owns the
#: slot and waits for 「下一段」. Keeping it apart from TERMINAL is what lets the
#: tests tell "stopped at a boundary" from "finished".
SETTLED = TERMINAL | {"segment"}


def wait_until(runner: BatchRunner, predicate, timeout: float = 8.0) -> dict:
    """Poll until ``predicate(status)`` holds. Immune to state transitions."""
    deadline = time.time() + timeout
    status = runner.status()
    while not predicate(status) and time.time() < deadline:
        time.sleep(0.01)
        status = runner.status()
    return status


def wait_for_segment(runner: BatchRunner, timeout: float = 8.0) -> dict:
    """Wait for the queue to park at a segment boundary."""
    return wait_until(runner, lambda status: status["status"] == "segment", timeout)


def wait_settled(runner: BatchRunner, timeout: float = 8.0) -> dict:
    """Wait for the queue to stop doing work, at a boundary or for good."""
    return wait_until(runner, lambda status: status["status"] in SETTLED, timeout)


def wait_finished(runner: BatchRunner, timeout: float = 8.0) -> dict:
    """Wait for a terminal status. Use this whenever the answer must be final."""
    return wait_until(runner, lambda status: status["status"] in TERMINAL, timeout)


def wait_after_release(runner: BatchRunner, timeout: float = 8.0) -> dict:
    """Wait for a released boundary to be left behind, then for the next stop.

    ``next_segment`` only sets an event; the worker thread flips the status a
    moment later. Reading the status before that sees the *old* ``segment`` and
    mistakes "not moving yet" for "stopped again", which is what made these
    tests pass or fail depending on machine speed.
    """
    deadline = time.time() + timeout
    while runner.status()["status"] == "segment" and time.time() < deadline:
        time.sleep(0.01)
    return wait_settled(runner, timeout)


def bundle(count: int):
    payload = {"items": [{"title": f"任务{i:02d}", "prompt": f"提示词 {i}"} for i in range(1, count + 1)]}
    return PromptBundleParser.parse("items.json", json.dumps(payload).encode())


class FakeGatewayClient:
    """Legacy-shaped client backed by the reusable gateway fake."""

    def __init__(self, gateway: FakeComfyGateway):
        self.gateway = gateway

    def submit(self, graph, client_id):
        return self.gateway.submit(graph, client_id)

    def poll(self, prompt_id):
        return self.gateway.poll(prompt_id)

    def output(self, prompt_id):
        images = self.gateway.poll(prompt_id)["images"]
        return images[0] if images else None

    def interrupt(self):
        self.gateway.interrupt()


class FailOnSubmit:
    """Fails the submits whose 1-based number appears in ``fail_at``.

    ``FakeComfyGateway.fail_with`` fails *every* submit, which cannot express
    "the second item broke". The error raised here is task-level, so the batch
    carries on past it -- which is exactly how a real batch reaches a segment
    boundary with one failure behind it.
    """

    def __init__(self, gateway: FakeComfyGateway, fail_at: set[int]):
        self.gateway = gateway
        self.fail_at = set(fail_at)
        self.calls = 0

    def submit(self, graph, client_id):
        self.calls += 1
        if self.calls in self.fail_at:
            raise ComfyError(
                "HTTP 400",
                status=400,
                path="/prompt",
                body={"error": {"type": "a_transient_problem", "message": "偶发问题", "details": "偶发"}, "node_errors": {}},
            )
        return self.gateway.submit(graph, client_id)

    def poll(self, prompt_id):
        return self.gateway.poll(prompt_id)

    def output(self, prompt_id):
        images = self.gateway.poll(prompt_id)["images"]
        return images[0] if images else None

    def interrupt(self):
        self.gateway.interrupt()


class BrokenProgressStore:
    """A store whose writes always fail, for the "disk is full" boundary."""

    def save(self, document):
        raise OSError("磁盘已满")

    def load(self, run_id):
        return None

    def latest(self, *, resumable_only=False):
        return None

    def summary(self, document):
        return None

    def clear(self, run_id):
        return None


class SegmentFixture(unittest.TestCase):
    """Shared setup: a real workflow file plus an offline gateway."""

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._temp.name)
        self.comfy = make_comfy_tree(self.root)
        (self.comfy / "models" / "diffusion_models" / "Krea2-red.safetensors").write_bytes(b"")
        self.workflow = write_workflow(self.root / "workflow.json", ui_workflow(model="Krea2-red.safetensors"))

    def tearDown(self):
        self._temp.cleanup()

    def client(self, fail_at: set[int] | None = None):
        gateway = FakeComfyGateway(output_dir=self.comfy / "output")
        self.gateway = gateway
        return FailOnSubmit(gateway, fail_at or set())

    def make_runner(self, client=None, store=None):
        return BatchRunner(self.comfy, client or self.client(), progress_store=store)

    def make_config(self, **overrides):
        values = {"output_dir": str(self.root / "out")}
        values.update(overrides)
        return BatchConfig(str(self.workflow), "Krea2-red.safetensors", **values)


class SegmentBoundaryTests(SegmentFixture):
    def test_a_segment_stops_after_exactly_its_size(self):
        runner = self.make_runner()
        runner.start(bundle(5), self.make_config(segment_size=2))
        status = wait_for_segment(runner)

        self.assertEqual("segment", status["status"])
        self.assertEqual(2, len(self.gateway.submitted), "分段边界没有拦住后面的任务")
        self.assertEqual(3, status["next_index"], "下一段的起点不是紧接在第 2 条之后")
        self.assertEqual(2, status["segment_size"])

    def test_no_segment_size_keeps_the_old_single_pass_behaviour(self):
        runner = self.make_runner()
        runner.start(bundle(5), self.make_config())
        status = wait_after_release(runner)

        self.assertEqual("completed", status["status"])
        self.assertEqual(5, len(self.gateway.submitted))
        self.assertIsNone(status["next_index"])

    def test_trial_first_means_one_item_then_a_decision(self):
        runner = self.make_runner()
        config = BatchConfig.from_dict({
            "workflow_path": str(self.workflow),
            "model": "Krea2-red.safetensors",
            "output_dir": str(self.root / "out"),
            "trial_first": True,
        })

        runner.start(bundle(4), config)
        status = wait_for_segment(runner)

        self.assertEqual(1, status["segment_size"])
        self.assertEqual(1, len(self.gateway.submitted))
        self.assertEqual(2, status["next_index"])

    def test_next_segment_runs_the_next_slice_and_stops_again(self):
        runner = self.make_runner()
        runner.start(bundle(5), self.make_config(segment_size=2))
        wait_for_segment(runner)

        runner.next_segment()
        # 必须等「离开这道边界、再在下一道边界停下」；直接等 segment 会立刻读到
        # 还没被后台线程更新掉的老状态。
        status = wait_after_release(runner)

        self.assertEqual(4, len(self.gateway.submitted), "第二段没有跑满 2 条")
        self.assertEqual(5, status["next_index"])

    def test_the_last_segment_finishes_instead_of_waiting(self):
        runner = self.make_runner()
        runner.start(bundle(4), self.make_config(segment_size=2))
        wait_for_segment(runner)

        runner.next_segment()
        status = wait_after_release(runner)

        self.assertEqual("completed", status["status"])
        self.assertEqual(4, len(self.gateway.submitted))
        self.assertIsNone(status["next_index"], "批次结束了却还挂着下一段起点")

    def test_a_segment_larger_than_the_batch_never_pauses(self):
        runner = self.make_runner()
        runner.start(bundle(3), self.make_config(segment_size=10))
        status = wait_after_release(runner)

        self.assertEqual("completed", status["status"])
        self.assertEqual(3, len(self.gateway.submitted))

    def test_next_segment_is_refused_when_nothing_is_waiting(self):
        runner = self.make_runner()
        runner.start(bundle(4), self.make_config(segment_size=2))
        wait_for_segment(runner)

        runner.next_segment()
        wait_after_release(runner)

        # A stray click after the run finished must not arm the gate, otherwise
        # the *next* batch would sail straight through its first boundary.
        with self.assertRaises(ValueError):
            runner.next_segment()

    def test_a_batch_waiting_at_a_boundary_still_owns_the_slot(self):
        runner = self.make_runner()
        runner.start(bundle(4), self.make_config(segment_size=2))
        wait_for_segment(runner)

        with self.assertRaises(ValueError):
            runner.start(bundle(4), self.make_config(segment_size=2))
        with self.assertRaises(ValueError):
            runner.redo(1)

        self.assertEqual(2, len(self.gateway.submitted), "等待确认时放进了第二批任务")
        self.assertIn("segment", ACTIVE_STATUSES)

    def test_cancelling_while_waiting_ends_the_batch(self):
        runner = self.make_runner()
        runner.start(bundle(6), self.make_config(segment_size=2))
        wait_for_segment(runner)

        runner.cancel()
        status = wait_after_release(runner)

        self.assertEqual("cancelled", status["status"])
        self.assertEqual(2, len(self.gateway.submitted), "取消后仍然提交了后续任务")

    def test_pause_does_not_steal_the_segment_state(self):
        runner = self.make_runner()
        runner.start(bundle(4), self.make_config(segment_size=2))
        wait_for_segment(runner)

        # 「暂停」在分段等待时无意义：这道门的开关是「下一段」，不是「继续」。
        runner.pause()
        self.assertEqual("segment", runner.status()["status"])

        runner.next_segment()
        self.assertEqual("completed", wait_after_release(runner)["status"])


class SnapshotTests(SegmentFixture):
    def test_the_snapshot_records_what_finished_and_where_to_continue(self):
        store = RunProgressStore(self.root / "runs")
        runner = self.make_runner(store=store)
        runner.start(bundle(5), self.make_config(segment_size=2))
        wait_for_segment(runner)

        document = store.latest()

        self.assertIsNotNone(document)
        self.assertEqual([1, 2], document["completed_indexes"])
        self.assertEqual(3, document["next_index"])
        self.assertEqual(5, document["total"])
        self.assertEqual(2, document["segment_size"])
        self.assertEqual(5, len(document["bundle"]["items"]), "快照没带够续跑需要的提示词")

    def test_a_finished_run_is_never_offered_for_resume(self):
        store = RunProgressStore(self.root / "runs")
        runner = self.make_runner(store=store)
        runner.start(bundle(3), self.make_config())
        wait_after_release(runner)

        self.assertIsNone(runner.resumable())
        with self.assertRaises(ValueError):
            runner.resume_interrupted()

    def test_a_restarted_app_is_offered_the_unfinished_run(self):
        store = RunProgressStore(self.root / "runs")
        first = self.make_runner(store=store)
        first.start(bundle(7), self.make_config(segment_size=3))
        wait_for_segment(first)

        # A brand new runner is what a restarted application looks like: its own
        # state is empty, and only the snapshot knows the batch existed.
        restarted = self.make_runner(store=store)
        summary = restarted.resumable()

        self.assertIsNotNone(summary)
        self.assertEqual(4, summary["next_index"], "第 1 段是 1、2、3 条，续跑要从第 4 条起")
        self.assertEqual(3, summary["completed"])
        self.assertEqual(7, summary["total"])

    def test_a_batch_waiting_at_a_boundary_is_not_offered_for_resume(self):
        store = RunProgressStore(self.root / "runs")
        runner = self.make_runner(store=store)
        runner.start(bundle(6), self.make_config(segment_size=2))
        wait_for_segment(runner)

        # The queue is alive and owns the run; the page must offer 「下一段」,
        # not 「续跑」 -- a second queue into the same run would duplicate work.
        self.assertIsNone(runner.resumable())

    def test_the_snapshot_never_carries_the_llm_api_key(self):
        store = RunProgressStore(self.root / "runs")
        runner = self.make_runner(store=store)
        config = self.make_config(
            llm={"enabled": False, "base_url": "http://127.0.0.1:1234/v1", "model": "m", "api_key": "SECRET-VALUE"}
        )

        runner.start(bundle(1), config)
        wait_after_release(runner)

        document = store.latest()
        self.assertEqual("", document["config"]["llm"]["api_key"])
        self.assertNotIn("SECRET-VALUE", store.path_for(document["run_id"]).read_text(encoding="utf-8"))

    def test_a_snapshot_write_failure_is_reported_without_killing_the_batch(self):
        runner = self.make_runner(store=BrokenProgressStore())
        runner.start(bundle(3), self.make_config())
        status = wait_after_release(runner)

        self.assertEqual("completed", status["status"], "快照写不进去就把批次也带停了")
        self.assertIn("磁盘已满", status["snapshot_error"])

    def test_the_store_writes_atomically_and_leaves_no_temp_file(self):
        store = RunProgressStore(self.root / "runs")
        store.save({"run_id": "abc123", "total": 1, "next_index": 1})

        leftovers = sorted(path.name for path in (self.root / "runs").iterdir() if path.name != "abc123.json")
        self.assertEqual([], leftovers)
        self.assertEqual(1, store.load("abc123")["total"])


class ResumeTests(SegmentFixture):
    def test_a_fresh_runner_continues_without_regenerating_finished_items(self):
        store = RunProgressStore(self.root / "runs")
        first = self.make_runner(store=store)
        first.start(bundle(6), self.make_config(segment_size=2))
        wait_for_segment(first)
        self.assertEqual(2, len(self.gateway.submitted))

        # A brand new runner and a brand new gateway: exactly what a restart
        # after a crash looks like.
        second = self.make_runner(store=store)
        second.resume_interrupted()
        status = wait_for_segment(second)

        self.assertEqual(2, len(self.gateway.submitted), "续跑重跑了已经完成的 1、2 条")
        self.assertEqual(5, status["next_index"], "续跑又停在了中断前那道边界上")
        self.assertEqual(4, status["completed"])

    def test_a_resumed_run_keeps_its_original_run_id(self):
        store = RunProgressStore(self.root / "runs")
        first = self.make_runner(store=store)
        first.start(bundle(6), self.make_config(segment_size=2))
        original = wait_for_segment(first)["run_id"]

        second = self.make_runner(store=store)
        resumed = second.resume_interrupted()

        self.assertEqual(original, resumed["run_id"], "续跑换了 run_id，输出目录与审图历史会对不上")
        self.assertEqual(3, resumed["resumed_from"])

    def test_a_failed_item_at_the_break_point_is_retried_on_resume(self):
        store = RunProgressStore(self.root / "runs")
        first = self.make_runner(client=self.client(fail_at={2}), store=store)
        first.start(bundle(6), self.make_config(segment_size=2))
        status = wait_for_segment(first)

        self.assertEqual("error", status["results"][1]["status"])
        self.assertEqual([1], store.latest()["completed_indexes"])
        self.assertEqual(2, store.latest()["next_index"], "失败的条目没有被算成待重跑")

        second = self.make_runner(store=store)
        second.resume_interrupted()
        resumed = wait_for_segment(second)

        # 第 2 条上一次是失败的，续跑必须把它重跑一次。重跑的结果要**替换**那条
        # 失败记录，而不是并排留着——否则失败会永远挂在页面上，计数也是错的。
        self.assertEqual([1, 2, 3], [row["index"] for row in resumed["results"]])
        self.assertEqual(["completed", "completed", "completed"], [row["status"] for row in resumed["results"]])
        self.assertEqual(4, resumed["next_index"])

    def test_a_cancelled_batch_can_still_be_resumed_later(self):
        store = RunProgressStore(self.root / "runs")
        first = self.make_runner(store=store)
        first.start(bundle(6), self.make_config(segment_size=2))
        wait_for_segment(first)
        first.cancel()
        wait_after_release(first)

        second = self.make_runner(store=store)
        summary = second.resumable()

        self.assertIsNotNone(summary, "取消过的批次仍然可以续跑，快照不该被丢掉")
        self.assertEqual(3, summary["next_index"])

    def test_resume_picks_the_newest_snapshot(self):
        store = RunProgressStore(self.root / "runs")
        store.save({"run_id": "older", "total": 9, "next_index": 2, "updated_at": "2026-01-01T00:00:00"})
        store.save({"run_id": "newer", "total": 9, "next_index": 5, "updated_at": "2026-06-01T00:00:00"})

        runner = self.make_runner(store=store)
        summary = runner.resumable()

        self.assertEqual("newer", summary["run_id"])
        self.assertEqual(5, summary["next_index"])

    def test_resume_is_refused_without_a_snapshot_store(self):
        runner = self.make_runner()
        with self.assertRaises(ValueError):
            runner.resume_interrupted()

    def test_resume_is_refused_while_a_batch_waits_at_a_boundary(self):
        store = RunProgressStore(self.root / "runs")
        runner = self.make_runner(store=store)
        runner.start(bundle(4), self.make_config(segment_size=2))
        wait_for_segment(runner)

        with self.assertRaises(ValueError):
            runner.resume_interrupted()

    def test_a_snapshot_without_prompts_is_refused_instead_of_running_empty(self):
        store = RunProgressStore(self.root / "runs")
        store.save({"run_id": "empty", "total": 3, "next_index": 1, "bundle": {"items": []}, "config": {}})

        runner = self.make_runner(store=store)
        with self.assertRaises(ValueError):
            runner.resume_interrupted("empty")


class ProgressStoreSafetyTests(unittest.TestCase):
    def test_a_run_id_that_could_escape_the_folder_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            store = RunProgressStore(pathlib.Path(temp) / "runs")

            for hostile in ("../escape", "..\\escape", "a/b", ""):
                with self.assertRaises(ValueError):
                    store.save({"run_id": hostile, "total": 1, "next_index": 1})

            self.assertFalse((pathlib.Path(temp) / "escape.json").exists())
            self.assertFalse((pathlib.Path(temp) / "runs").exists(), "被拒绝的写入仍然建了目录")

    def test_a_corrupt_snapshot_is_ignored_instead_of_raising(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            store = RunProgressStore(root)
            (root / "broken.json").write_text("{not json", encoding="utf-8")

            self.assertIsNone(store.load("broken"))
            self.assertEqual([], store.documents())

    def test_is_resumable_only_while_items_remain(self):
        self.assertTrue(RunProgressStore.is_resumable({"total": 10, "next_index": 4}))
        self.assertFalse(RunProgressStore.is_resumable({"total": 10, "next_index": 11}))
        self.assertFalse(RunProgressStore.is_resumable({}))
        self.assertFalse(RunProgressStore.is_resumable(None))


if __name__ == "__main__":
    unittest.main()
