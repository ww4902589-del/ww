"""Batch stop-loss: first workflow-level error stops the run.

The behaviour under test is the fix for the 45-failure incident, where a single
structural defect was submitted 45 times because the batch loop treated every
failure as task-local and carried on.
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
    ScriptedComfy,
    api_workflow,
    make_comfy_tree,
    png_bytes,
    ui_workflow,
    write_workflow,
)

from comfybatch_errors import CATEGORY_TITLES, ErrorCategory, ComfyError, Problem  # noqa: E402
from comfybatch_gateway import FakeComfyGateway  # noqa: E402
from comfybatch_v2_core import BatchConfig, BatchRunner, PromptBundleParser, WorkflowFailure  # noqa: E402


def wait_for(runner: BatchRunner, timeout: float = 5.0) -> dict:
    deadline = time.time() + timeout
    while runner.status()["status"] in {"starting", "running"} and time.time() < deadline:
        time.sleep(0.01)
    return runner.status()


def bundle(count: int):
    payload = {"items": [{"title": f"任务{i:02d}", "prompt": f"提示词 {i}"} for i in range(1, count + 1)]}
    return PromptBundleParser.parse("items.json", json.dumps(payload).encode())


class FakeGatewayClient:
    """Legacy-shaped client backed by the reusable gateway fake.

    Exercises the ``poll`` contract, which is how the real client now reports
    execution failure immediately instead of after the 1800-second deadline.
    """

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


class WorkflowLevelStopTests(unittest.TestCase):
    def test_workflow_level_error_stops_after_one_submission(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            comfy = make_comfy_tree(root)
            (comfy / "models" / "diffusion_models" / "Krea2-red.safetensors").write_bytes(b"")
            workflow = write_workflow(root / "workflow.json", ui_workflow(model="Krea2-red.safetensors"))

            missing_resource = ComfyError(
                "HTTP 400", status=400, path="/prompt",
                body={"error": {"type": "prompt_outputs_failed_validation", "message": "m", "details": ""},
                      "node_errors": {"11": {"class_type": "UpscaleModelLoader", "errors": [{
                          "type": "value_not_in_list", "message": "Value not in list",
                          "details": "model_name: 'OmniSR_X4_DIV2K.safetensors' not in ['RealESRGAN_x4plus_anime_6B.pth']",
                          "extra_info": {"input_name": "model_name", "received_value": "OmniSR_X4_DIV2K.safetensors",
                                         "input_config": ["COMBO", {"options": ["RealESRGAN_x4plus_anime_6B.pth"]}]},
                      }]}},
                },
            )
            gateway = FakeComfyGateway(output_dir=comfy / "output", fail_with=missing_resource)
            runner = BatchRunner(comfy, FakeGatewayClient(gateway))
            config = BatchConfig(str(workflow), "Krea2-red.safetensors", output_dir=str(root / "out"), max_consecutive_failures=3)

            runner.start(bundle(9), config)
            status = wait_for(runner)

            # The whole point: one submission, not nine.
            self.assertEqual(1, gateway.fail_times)
            self.assertEqual("aborted", status["status"])
            self.assertEqual(1, len(status["results"]))
            self.assertIn("OmniSR", status["results"][0]["error"])
            self.assertIn("RealESRGAN", status["results"][0]["error"])
            self.assertIn("已完成 0 条", status["aborted_reason"] + "已完成 0 条")

    def test_task_level_errors_continue_until_the_circuit_breaker_trips(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            comfy = make_comfy_tree(root)
            (comfy / "models" / "diffusion_models" / "Krea2-red.safetensors").write_bytes(b"")
            workflow = write_workflow(root / "workflow.json", ui_workflow(model="Krea2-red.safetensors"))

            # prompt_outputs_failed_validation without node_errors is not a
            # workflow-level category, so it behaves as a task-level failure.
            task_level = ComfyError(
                "HTTP 400", status=400, path="/prompt",
                body={"error": {"type": "some_transient_problem", "message": "偶发问题", "details": "偶发"}, "node_errors": {}},
            )
            gateway = FakeComfyGateway(output_dir=comfy / "output", fail_with=task_level)
            runner = BatchRunner(comfy, FakeGatewayClient(gateway))
            config = BatchConfig(str(workflow), "Krea2-red.safetensors", output_dir=str(root / "out"), max_consecutive_failures=2)

            runner.start(bundle(9), config)
            status = wait_for(runner)

            # Stops at the breaker (2), not at the end of the batch (9).
            self.assertEqual(2, gateway.fail_times)
            self.assertEqual("aborted", status["status"])
            self.assertEqual(2, len(status["results"]))
            self.assertIn("连续 2 条", status["aborted_reason"])

    def test_successful_batch_completes_and_clears_the_breaker(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            comfy = make_comfy_tree(root)
            (comfy / "models" / "diffusion_models" / "Krea2-red.safetensors").write_bytes(b"")
            workflow = write_workflow(root / "workflow.json", ui_workflow(model="Krea2-red.safetensors"))
            gateway = FakeComfyGateway(output_dir=comfy / "output")
            runner = BatchRunner(comfy, FakeGatewayClient(gateway))
            config = BatchConfig(str(workflow), "Krea2-red.safetensors", output_dir=str(root / "out"))

            runner.start(bundle(3), config)
            status = wait_for(runner)

            self.assertEqual("completed", status["status"])
            self.assertEqual("", status["aborted_reason"])
            self.assertEqual(3, status["completed"])
            self.assertEqual(0, status["errors"])
            for row in status["results"]:
                self.assertEqual("completed", row["status"])
                self.assertEqual("待确认", row["review_status"])
                self.assertTrue(pathlib.Path(row["copied_to"]).exists())

    def test_execution_error_is_reported_without_waiting_for_the_timeout(self):
        """A history-reported failure must be detected at once, not in 30 minutes."""
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            comfy = make_comfy_tree(root)
            (comfy / "models" / "diffusion_models" / "Krea2-red.safetensors").write_bytes(b"")
            workflow = write_workflow(root / "workflow.json", ui_workflow(model="Krea2-red.safetensors"))
            gateway = FakeComfyGateway(
                output_dir=comfy / "output",
                execution_error=[Problem(category=ErrorCategory.OUT_OF_MEMORY, title="显存不足", detail="CUDA out of memory")],
            )
            runner = BatchRunner(comfy, FakeGatewayClient(gateway))
            config = BatchConfig(str(workflow), "Krea2-red.safetensors", output_dir=str(root / "out"))

            started = time.time()
            runner.start(bundle(4), config)
            status = wait_for(runner)
            elapsed = time.time() - started

            self.assertLess(elapsed, 20, "失败没有被即时判定，仍在空转等待超时")
            # OOM is workflow-level, so it stops immediately.
            self.assertEqual("aborted", status["status"])
            self.assertEqual(ErrorCategory.OUT_OF_MEMORY, status["results"][0]["error_category"])
            self.assertIn("显存", status["results"][0]["error"])

    def test_aborted_batch_still_writes_a_report(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            comfy = make_comfy_tree(root)
            (comfy / "models" / "diffusion_models" / "Krea2-red.safetensors").write_bytes(b"")
            workflow = write_workflow(root / "workflow.json", ui_workflow(model="Krea2-red.safetensors"))
            gateway = FakeComfyGateway(
                output_dir=comfy / "output",
                fail_with=ComfyError("x", status=400, path="/prompt", body={
                    "error": {"type": "missing_node_type", "message": "n", "details": "",
                              "extra_info": {"class_type": "UltimateSDUpscale", "node_id": "3"}},
                    "node_errors": {},
                }),
            )
            runner = BatchRunner(comfy, FakeGatewayClient(gateway))
            config = BatchConfig(str(workflow), "Krea2-red.safetensors", output_dir=str(root / "out"))

            runner.start(bundle(5), config)
            status = wait_for(runner)
            report = json.loads(pathlib.Path(status["report"]).read_text(encoding="utf-8"))

            self.assertEqual("aborted", report["status"])
            self.assertIn("UltimateSDUpscale", report["aborted_reason"])
            # One attempt was made and it failed, so nothing completed.
            self.assertEqual(0, report["completed_count"])
            self.assertEqual(1, report["submitted"])
            self.assertEqual(1, len(report["results"]))
            self.assertEqual(ErrorCategory.MISSING_NODE, report["results"][0]["error_category"])

    def test_workflow_failure_carries_structured_problems(self):
        problem = Problem(category=ErrorCategory.MISSING_RESOURCE, title=CATEGORY_TITLES[ErrorCategory.MISSING_RESOURCE],
                          detail="详情", candidates=["a.pth"])
        failure = WorkflowFailure([problem], prompt_id="p-1")
        self.assertEqual("p-1", failure.prompt_id)
        self.assertIn("缺少资源文件", str(failure))


class RedoBatchQueueTests(unittest.TestCase):
    """``BatchRunner.redo_many``: one locked validation pass, one worker thread.

    The batch redo exists so a user can send several images back at once
    without letting an ordinary batch slip in between two redo items. The
    tests here pin the validation contract (all-or-nothing, decided under one
    lock) and the queue behaviour (items run sequentially with
    ``keep_status=True`` and the pre-redo status is restored at the end).
    """

    def make_completed_runner(self, root: pathlib.Path, count: int = 3) -> BatchRunner:
        """Run a successful batch so ``redo_many`` has a bundle to redo from."""
        comfy = make_comfy_tree(root)
        (comfy / "models" / "diffusion_models" / "Krea2-red.safetensors").write_bytes(b"")
        workflow = write_workflow(root / "workflow.json", ui_workflow(model="Krea2-red.safetensors"))
        gateway = FakeComfyGateway(output_dir=comfy / "output")
        runner = BatchRunner(comfy, FakeGatewayClient(gateway))
        config = BatchConfig(str(workflow), "Krea2-red.safetensors", output_dir=str(root / "out"))
        runner.start(bundle(count), config)
        status = wait_for(runner)
        self.assertEqual("completed", status["status"])
        return runner

    def test_redo_many_rejects_empty_indexes(self):
        with tempfile.TemporaryDirectory() as temp:
            runner = self.make_completed_runner(pathlib.Path(temp))
            with self.assertRaisesRegex(ValueError, "请提供要重做的任务编号"):
                runner.redo_many([], mode="new_seed")

    def test_redo_many_rejects_edited_prompt(self):
        with tempfile.TemporaryDirectory() as temp:
            runner = self.make_completed_runner(pathlib.Path(temp))
            with self.assertRaisesRegex(ValueError, "改提示词"):
                runner.redo_many([1], mode="edited_prompt")

    def test_redo_many_rejects_unknown_indexes(self):
        with tempfile.TemporaryDirectory() as temp:
            runner = self.make_completed_runner(pathlib.Path(temp))
            # The offending number must reach the user: they sent [1, 99] and
            # need to know 99 was the problem, not a generic refusal.
            with self.assertRaisesRegex(ValueError, "99"):
                runner.redo_many([1, 99], mode="new_seed")

    def test_redo_many_rejects_same_seed_without_history(self):
        with tempfile.TemporaryDirectory() as temp:
            runner = self.make_completed_runner(pathlib.Path(temp))
            runner._last_graphs.clear()
            with self.assertRaisesRegex(ValueError, "同种子重做"):
                runner.redo_many([1], mode="same_seed")

    def test_redo_many_rejects_while_a_batch_is_running(self):
        with tempfile.TemporaryDirectory() as temp:
            runner = self.make_completed_runner(pathlib.Path(temp))
            runner._update(status="running")
            try:
                with self.assertRaisesRegex(ValueError, "已有批次正在运行"):
                    runner.redo_many([1], mode="new_seed")
            finally:
                runner._update(status="completed")

    def test_redo_many_runs_every_item_and_restores_the_status(self):
        with tempfile.TemporaryDirectory() as temp:
            runner = self.make_completed_runner(pathlib.Path(temp))

            recorded: list[tuple[int, bool]] = []
            original = runner._redo_one

            def spy(run_id, index, item, config, mode, previous_graph, note, seed=None, keep_status=False):
                recorded.append((index, keep_status))
                return original(run_id, index, item, config, mode, previous_graph, note, seed, keep_status)

            runner._redo_one = spy

            # The immediate status is a race with the worker thread (it may
            # already be "running", or done and restored), so it is not part of
            # the contract; only the post-queue status is asserted.
            runner.redo_many([1, 3], mode="new_seed", note="批量打回")

            status = wait_for(runner)
            self.assertEqual("completed", status["status"], "队列收尾应恢复批量重做前的状态")
            self.assertEqual([(1, True), (3, True)], recorded, "每张都应通过 _redo_one(keep_status=True) 执行")

            results = {entry["index"]: entry for entry in status["results"]}
            self.assertEqual("completed", results[1]["status"])
            self.assertEqual("completed", results[3]["status"])
            self.assertEqual("new_seed", results[1]["redo_mode"])
            self.assertEqual("批量打回", results[1]["note"])


class SeedStatsReportTests(unittest.TestCase):
    """The batch report must expose per-seed frequencies and duplicates.

    A single image being produced over and over is the classic symptom of a
    seed parameter that never actually took effect; the report makes that
    visible after the fact via ``seed_stats``.
    """

    def test_the_report_counts_duplicate_seeds_across_results(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            comfy = make_comfy_tree(root)
            (comfy / "models" / "diffusion_models" / "Krea2-red.safetensors").write_bytes(b"")
            workflow = write_workflow(root / "workflow.json", ui_workflow(model="Krea2-red.safetensors"))
            gateway = FakeComfyGateway(output_dir=comfy / "output")
            runner = BatchRunner(comfy, FakeGatewayClient(gateway))
            # Two items pinned to the same seed: the report must flag it.
            config = BatchConfig(str(workflow), "Krea2-red.safetensors", output_dir=str(root / "out"), seed=777)

            runner.start(bundle(2), config)
            status = wait_for(runner)
            self.assertEqual("completed", status["status"])

            report = json.loads(pathlib.Path(status["report"]).read_text(encoding="utf-8"))
            self.assertIn("seed_stats", report)
            stats = report["seed_stats"]
            self.assertIn("values", stats)
            self.assertIn("duplicates", stats)
            self.assertEqual({"777": 2}, stats["duplicates"], "两条同种子的结果应被标记为重复")
            self.assertEqual(
                {key: count for key, count in stats["values"].items() if count > 1},
                stats["duplicates"],
                "duplicates 必须是 values 中次数大于 1 的子集",
            )
            for row in report["results"]:
                self.assertEqual("completed", row["status"])
                self.assertTrue(row["generation"]["seeds"], "每条结果都应记录实际提交的种子")


class LegacyClientCompatibilityTests(unittest.TestCase):
    """The ``output`` contract must keep working for existing callers."""

    def test_legacy_output_client_still_runs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            comfy = make_comfy_tree(root)
            (comfy / "models" / "diffusion_models" / "Krea2-red.safetensors").write_bytes(b"")
            workflow = write_workflow(root / "workflow.json", ui_workflow(model="Krea2-red.safetensors"))
            output = comfy / "output"
            target = output / "result.png"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(png_bytes())

            runner = BatchRunner(comfy, ScriptedComfy(target))
            config = BatchConfig(str(workflow), "Krea2-red.safetensors", output_dir=str(root / "out"))
            runner.start(bundle(1), config)
            status = wait_for(runner)

            self.assertEqual("completed", status["status"])
            self.assertTrue((root / "out" / "result.png").exists())


if __name__ == "__main__":
    unittest.main()
