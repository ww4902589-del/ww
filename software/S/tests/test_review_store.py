"""Per-image review: confirm, reject, note, redo, and survive a restart.

The plan requires confirmation state to persist across restarts and replaced
images to keep their history while only the current version stays visible. These
tests drive the store directly and the redo path through the runner.
"""

from __future__ import annotations

import copy
import json
import pathlib
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from fakes import (  # noqa: E402
    PrefixAwareComfy,
    ScriptedComfy,
    SequencedComfy,
    gradient,
    make_comfy_tree,
    png_bytes,
    triptych,
    ui_workflow,
    write_workflow,
)

from comfybatch_v2_core import (  # noqa: E402
    REDO_MODES,
    REVIEW_STATUSES,
    BatchConfig,
    BatchRunner,
    PromptBundleParser,
    ResultReviewStore,
)


def wait_for(runner: BatchRunner, timeout: float = 5.0) -> dict:
    deadline = time.time() + timeout
    while runner.status()["status"] in {"starting", "running"} and time.time() < deadline:
        time.sleep(0.01)
    return runner.status()


def one_bundle(title: str = "任务01", prompt: str = "提示词"):
    return PromptBundleParser.parse("one.json", json.dumps({"items": [{"title": title, "prompt": prompt}]}).encode())


class ReviewStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)
        self.store = ResultReviewStore(self.root / "reviews")
        self.image = self.root / "out.png"
        self.image.write_bytes(png_bytes())

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def results(*, count: int = 2, copied_to: str = "") -> list[dict]:
        return [
            {
                "index": index,
                "title": f"任务{index:02d}",
                "preset_name": "手游+数字",
                "status": "completed",
                "copied_to": copied_to,
                "image": {"filename": f"out{index}.png"},
                "generation": {"model": "m.safetensors", "workflow_variant": "save:1"},
                "prompt": "正面", "negative_prompt": "负面",
                "quality": {"repeated_panels": False},
                "elapsed": 12.5,
                "attempts": [{"attempt": 1, "prompt_id": f"p{index}", "quality": {}}],
            }
            for index in range(1, count + 1)
        ]

    def test_new_results_default_to_pending(self):
        document = self.store.ingest("run-1", self.results())
        self.assertEqual(2, len(document["items"]))
        for entry in document["items"].values():
            self.assertEqual("待确认", entry["review_status"])
        self.assertEqual({"total": 2, "by_status": {"待确认": 2, "已通过": 0, "需重做": 0, "已替换": 0}},
                         self.store.summary("run-1"))

    def test_confirm_single_and_batch(self):
        self.store.ingest("run-1", self.results(count=3))
        self.store.confirm("run-1", [1], "已通过")
        document = self.store.confirm("run-1", [2, 3], "已通过", note="构图合格")
        self.assertEqual("已通过", document["items"]["1"]["review_status"])
        self.assertEqual("构图合格", document["items"]["2"]["note"])
        self.assertEqual({"total": 3, "by_status": {"待确认": 0, "已通过": 3, "需重做": 0, "已替换": 0}},
                         self.store.summary("run-1"))

    def test_reject_records_a_note(self):
        self.store.ingest("run-1", self.results(count=1))
        document = self.store.confirm("run-1", [1], "需重做", "手部畸形，换种子重做")
        entry = document["items"]["1"]
        self.assertEqual("需重做", entry["review_status"])
        self.assertEqual("手部畸形，换种子重做", entry["note"])
        self.assertTrue(entry.get("reviewed_at"))

    def test_unknown_status_and_index_are_rejected(self):
        self.store.ingest("run-1", self.results(count=1))
        with self.assertRaisesRegex(ValueError, "不支持的确认状态"):
            self.store.confirm("run-1", [1], "随便什么状态")
        with self.assertRaisesRegex(ValueError, "未找到要确认的任务编号"):
            self.store.confirm("run-1", [99], "已通过")

    def test_confirming_an_empty_batch_is_an_error(self):
        with self.assertRaisesRegex(ValueError, "还没有可确认的成图"):
            self.store.confirm("run-empty", [1], "已通过")

    def test_state_survives_a_real_restart(self):
        """A fresh store instance on the same directory must see the decisions."""
        self.store.ingest("run-1", self.results(count=2))
        self.store.confirm("run-1", [1], "已通过", "很好")
        self.store.confirm("run-1", [2], "需重做", "重做")

        reopened = ResultReviewStore(self.root / "reviews")
        entries = {entry["index"]: entry for entry in reopened.list_for_run("run-1")}
        self.assertEqual("已通过", entries[1]["review_status"])
        self.assertEqual("很好", entries[1]["note"])
        self.assertEqual("需重做", entries[2]["review_status"])

    def test_a_redo_attempt_replaces_the_active_version_and_keeps_history(self):
        """``redo_mode`` is the signal, not a grown attempts list.

        A real redo result carries only its own single attempt, so relying on
        "the attempt count increased" silently failed to mark the item replaced.
        This fixture reproduces the real shape.
        """
        self.store.ingest("run-1", self.results(count=1, copied_to=str(self.image)))
        self.store.confirm("run-1", [1], "需重做", "换种子")

        redone = self.results(count=1, copied_to=str(self.image))
        redone[0]["redo_mode"] = "same_seed"
        redone[0]["attempts"] = [{"attempt": 1, "prompt_id": "p2"}]
        document = self.store.ingest("run-1", redone)

        entry = document["items"]["1"]
        self.assertEqual("已替换", entry["review_status"])
        # Attempts accumulate: one from the original run, one from the redo.
        self.assertEqual(2, len(entry["attempts"]))
        self.assertEqual(1, len(entry["history"]))
        self.assertEqual("需重做", entry["history"][0]["review_status"])

    def test_redo_marker_is_what_triggers_replacement(self):
        """Without ``redo_mode`` and without growth, the item is not replaced."""
        self.store.ingest("run-1", self.results(count=1, copied_to=str(self.image)))
        self.store.confirm("run-1", [1], "已通过", "很好")

        same = self.results(count=1, copied_to=str(self.image))
        document = self.store.ingest("run-1", same)
        entry = document["items"]["1"]
        # Re-ingesting the identical run must not invent a replacement.
        self.assertEqual("已通过", entry["review_status"])
        self.assertEqual([], entry.get("history") or [])

    def test_reingesting_the_same_result_adds_no_version(self):
        """``ingest`` runs on every poll, so it must be idempotent.

        Found on a real run: because a redo result carries ``redo_mode``, every
        poll counted it as a new version, and one redo inflated the record to
        attempts 162 / history 161.
        """
        self.store.ingest("run-1", self.results(count=1, copied_to=str(self.image)))
        self.store.confirm("run-1", [1], "需重做", "换种子")

        redone = self.results(count=1, copied_to=str(self.image))
        redone[0]["redo_mode"] = "new_seed"
        redone[0]["attempts"] = [{"attempt": 1, "prompt_id": "p-redo"}]

        for _ in range(8):
            document = self.store.ingest("run-1", redone)

        entry = document["items"]["1"]
        self.assertEqual("已替换", entry["review_status"])
        self.assertEqual(2, len(entry["attempts"]), "重复 ingest 不应累加尝试次数")
        self.assertEqual(1, len(entry["history"]), "重复 ingest 不应累加历史版本")

    def test_a_genuinely_new_attempt_still_registers(self):
        self.store.ingest("run-1", self.results(count=1, copied_to=str(self.image)))
        self.store.confirm("run-1", [1], "需重做", "换种子")

        first = self.results(count=1, copied_to=str(self.image))
        first[0]["redo_mode"] = "new_seed"
        first[0]["attempts"] = [{"attempt": 1, "prompt_id": "p-1"}]
        self.store.ingest("run-1", first)

        second = self.results(count=1, copied_to=str(self.image))
        second[0]["redo_mode"] = "new_seed"
        second[0]["attempts"] = [{"attempt": 1, "prompt_id": "p-2"}]
        document = self.store.ingest("run-1", second)

        entry = document["items"]["1"]
        self.assertEqual(3, len(entry["attempts"]), "每次真正的新尝试都应计入")
        self.assertEqual(2, len(entry["history"]))

    def test_available_flag_reflects_the_file_on_disk(self):
        missing = self.root / "gone.png"
        self.store.ingest("run-1", [{**self.results(count=1)[0], "copied_to": str(missing)}])
        self.assertFalse(self.store.list_for_run("run-1")[0]["available"])
        self.store.ingest("run-1", [{**self.results(count=1)[0], "copied_to": str(self.image)}])
        self.assertTrue(self.store.list_for_run("run-1")[0]["available"])

    def test_document_is_written_atomically(self):
        self.store.ingest("run-1", self.results(count=1))
        path = self.store.path_for("run-1")
        self.assertTrue(path.exists())
        self.assertFalse(path.with_suffix(".tmp").exists())

    def test_corrupt_document_degrades_to_empty(self):
        self.store.root.mkdir(parents=True, exist_ok=True)
        self.store.path_for("run-1").write_text("{not json", encoding="utf-8")
        self.assertEqual({"run_id": "run-1", "items": {}}, self.store.load("run-1"))

    def test_statuses_and_modes_are_declared(self):
        self.assertEqual(("待确认", "已通过", "需重做", "已替换"), REVIEW_STATUSES)
        self.assertIn("same_seed", REDO_MODES)
        self.assertIn("reupscale_only", REDO_MODES)


class RedoTests(unittest.TestCase):
    """Redo must re-run one item and append an attempt, not restart the batch."""

    def _runner(self, root: pathlib.Path, client) -> tuple[BatchRunner, BatchConfig]:
        comfy = make_comfy_tree(root)
        (comfy / "models" / "diffusion_models" / "Krea2-red.safetensors").write_bytes(b"")
        workflow = write_workflow(root / "workflow.json", ui_workflow(model="Krea2-red.safetensors"))
        runner = BatchRunner(comfy, client)
        config = BatchConfig(str(workflow), "Krea2-red.safetensors", output_dir=str(root / "out"))
        return runner, config

    def _prepared(self, root: pathlib.Path):
        comfy = make_comfy_tree(root)
        (comfy / "models" / "diffusion_models" / "Krea2-red.safetensors").write_bytes(b"")
        workflow = write_workflow(root / "workflow.json", ui_workflow(model="Krea2-red.safetensors"))
        client = PrefixAwareComfy(comfy)
        runner = BatchRunner(comfy, client)
        config = BatchConfig(str(workflow), "Krea2-red.safetensors", output_dir=str(root / "out"))
        return runner, client, config

    def test_redo_new_seed_appends_an_attempt_and_copies_a_new_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            runner, client, config = self._prepared(root)

            runner.start(one_bundle(), config)
            wait_for(runner)
            self.assertEqual(1, len(runner.status()["results"]))
            original_revision = runner.status()["results"][0]["completed_revision"]

            runner.redo(1, "new_seed", note="换种子")
            status = wait_for(runner)

            self.assertEqual(2, client.calls)
            self.assertEqual(1, len(status["results"]))
            row = status["results"][0]
            self.assertEqual("completed", row["status"])
            self.assertEqual("new_seed", row["redo_mode"])
            self.assertGreater(row["completed_revision"], original_revision)
            name = pathlib.Path(row["copied_to"]).name
            self.assertIn("redo-new_seed", name)
            # The redo prefix must appear exactly once; a doubled prefix was a
            # real defect found by a live run.
            self.assertEqual(1, name.count("redo-new_seed"))
            self.assertTrue(pathlib.Path(row["copied_to"]).exists())

    def test_same_seed_redo_resubmits_the_identical_graph(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            runner, client, config = self._prepared(root)

            runner.start(one_bundle(), config)
            wait_for(runner)
            first_graph = json.loads(json.dumps(client.graphs[0]))

            runner.redo(1, "same_seed")
            wait_for(runner)
            second_graph = client.graphs[1]

            # Everything except the filename prefix must be identical, so the
            # seed and every parameter are held constant.
            for node_id, node in first_graph.items():
                for name, value in node["inputs"].items():
                    if name == "filename_prefix":
                        continue
                    self.assertEqual(value, second_graph[node_id]["inputs"][name],
                                     f"same_seed 重做改动了节点 {node_id}.{name}")

    def test_edited_prompt_replaces_only_that_items_prompt(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            runner, client, config = self._prepared(root)

            runner.start(one_bundle(), config)
            wait_for(runner)

            runner.redo(1, "edited_prompt", prompt="改写后的提示词")
            status = wait_for(runner)

            self.assertIn("改写后的提示词", json.dumps(client.graphs[-1], ensure_ascii=False))
            self.assertEqual("改写后的提示词", status["results"][0]["prompt"])

    def test_reupscale_only_explains_itself_when_unsupported(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            runner, client, config = self._prepared(root)

            runner.start(one_bundle(), config)
            wait_for(runner)
            runner.redo(1, "reupscale_only")
            status = wait_for(runner)

            row = status["results"][0]
            self.assertEqual("error", row["status"])
            self.assertIn("输入图片", row["error"])

    def test_redo_does_not_clobber_a_running_batch(self):
        """A redo finishing must not make a live batch look finished.

        Found on a real run: ``_redo_one`` unconditionally wrote
        ``status="completed"``, so a redo that overlapped the batch rewrote the
        live batch's status.
        """
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            runner, client, config = self._prepared(root)

            runner.start(one_bundle(), config)
            wait_for(runner)
            self.assertEqual("completed", runner.status()["status"])

            # Simulate the overlap: the batch owns "running" while the redo ends.
            with runner._lock:
                runner._state["status"] = "running"
                runner._redo_previous_status = "completed"
            runner._redo_one(runner.status()["run_id"], 1, one_bundle().items[0],
                             copy.deepcopy(config), "new_seed", None, "")

            self.assertEqual("running", runner.status()["status"],
                             "重做结束时不应把正在运行的批次改成已完成")

    def test_redo_restores_the_pre_redo_status(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            runner, client, config = self._prepared(root)

            runner.start(one_bundle(), config)
            wait_for(runner)
            runner.redo(1, "new_seed")
            status = wait_for(runner)
            # The batch had completed, so the redo must leave it completed rather
            # than inventing a different terminal status.
            self.assertEqual("completed", status["status"])

    def test_redo_requires_a_previous_run(self):
        with tempfile.TemporaryDirectory() as temp:
            runner, _config = self._runner(pathlib.Path(temp), ScriptedComfy(None))
            with self.assertRaisesRegex(ValueError, "还没有可重做的批次"):
                runner.redo(1, "new_seed")

    def test_redo_rejects_an_unknown_index_and_mode(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            runner, client, config = self._prepared(root)
            runner.start(one_bundle(), config)
            wait_for(runner)

            with self.assertRaisesRegex(ValueError, "不支持的重做方式"):
                runner.redo(1, "teleport")
            with self.assertRaisesRegex(ValueError, "没有第 7 条任务"):
                runner.redo(7, "new_seed")


class RetryLoopStillWorksTests(unittest.TestCase):
    """The existing triptych retry must keep working through the new poll path."""

    def test_retries_when_the_subject_guard_finds_repeated_panels(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            comfy = make_comfy_tree(root)
            output = comfy / "output"
            triptych(output / "first.png")
            gradient(output / "second.png")
            workflow = write_workflow(root / "workflow.json", ui_workflow(model="Krea2-red.safetensors"))
            (comfy / "models" / "diffusion_models" / "Krea2-red.safetensors").write_bytes(b"")

            runner = BatchRunner(comfy, SequencedComfy([output / "first.png", output / "second.png"]))
            config = BatchConfig(str(workflow), "Krea2-red.safetensors", output_dir=str(root / "out"),
                                 single_subject_guard=True, max_retries=1)
            runner.start(one_bundle("单人礼服", "单人"), config)
            status = wait_for(runner)

            self.assertEqual("completed", status["status"])
            self.assertEqual(2, len(status["results"][0]["attempts"]))
            self.assertEqual("second.png", pathlib.Path(status["results"][0]["copied_to"]).name)


if __name__ == "__main__":
    unittest.main()
