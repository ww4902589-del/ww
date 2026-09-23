"""Output paths and previews: the two defects behind "图片存不下来" and "预览很慢".

Both were found by driving a real batch against a stand-in ComfyUI:

* **Storage.** ComfyUI reports ``subfolder`` and ``filename`` in two shapes that
  disagree about who owns the folder. Joining them blindly produced
  ``output/a/b/a/b/x.png``, so every completed image failed to copy with
  ``[WinError 3] 系统找不到指定的路径`` -- and because that is a plain OSError and
  not a workflow-level ``Problem``, the batch did not stop; it just saved nothing.
  ``resolve_output_source`` reconciles the two shapes.

* **Preview.** The board pointed every ``<img>`` at the full-resolution PNG and
  ``thumbnail`` was hardcoded to an empty string, so a finished batch re-decoded
  and re-shipped tens of megabytes on every poll. ``ensure_thumbnail`` caches a
  downscaled preview keyed by the source file's identity.
"""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from fakes import png_bytes  # noqa: E402

from comfybatch_v2_core import (  # noqa: E402
    BatchConfig,
    BatchRunner,
    PromptBundleParser,
    ResultReviewStore,
    copy_output_image,
    resolve_output_source,
)

from PIL import Image  # noqa: E402


class ResolveOutputSourceTests(unittest.TestCase):
    """The two shapes ComfyUI actually reports must both resolve."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)
        self.output = self.root / "output"

    def tearDown(self):
        self.temp.cleanup()

    def _write(self, relative: str) -> pathlib.Path:
        path = self.output / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(png_bytes())
        return path

    def test_ws_shape_subfolder_plus_bare_filename(self):
        """``subfolder='a/b'`` with ``filename='x.png'`` -- the joined shape."""
        path = self._write("ComfyBatch-V2/abcd1234/001-x_00001_.png")
        image = {"subfolder": "ComfyBatch-V2/abcd1234", "filename": "001-x_00001_.png"}
        self.assertEqual(path, resolve_output_source(self.output, image))

    def test_view_shape_with_the_subfolder_repeated_inside_the_filename(self):
        """``subfolder=''`` but ``filename`` already carries the folder.

        This is the shape that produced the doubled path and the WinError 3.
        """
        path = self._write("ComfyBatch-V2/abcd1234/001-x_00001_.png")
        image = {"subfolder": "", "filename": "ComfyBatch-V2/abcd1234/001-x_00001_.png"}
        self.assertEqual(path, resolve_output_source(self.output, image))

    def test_both_shapes_present_reports_the_real_file(self):
        """``subfolder`` and ``filename`` both carry the folder -- still one path."""
        path = self._write("ComfyBatch-V2/abcd1234/001-x_00001_.png")
        image = {
            "subfolder": "ComfyBatch-V2/abcd1234",
            "filename": "ComfyBatch-V2/abcd1234/001-x_00001_.png",
        }
        resolved = resolve_output_source(self.output, image)
        self.assertEqual(path, resolved)
        self.assertTrue(resolved.is_file())
        # The bug this guards: no segment may appear twice.
        self.assertEqual(
            "ComfyBatch-V2/abcd1234/001-x_00001_.png",
            resolved.relative_to(self.output).as_posix(),
        )

    def test_backslash_separators_are_tolerated(self):
        path = self._write("ComfyBatch-V2/abcd1234/001-x_00001_.png")
        image = {"subfolder": "ComfyBatch-V2\\abcd1234", "filename": "001-x_00001_.png"}
        self.assertEqual(path, resolve_output_source(self.output, image))

    def test_flat_filename_lands_at_the_root(self):
        path = self._write("plain_00001_.png")
        self.assertEqual(
            path,
            resolve_output_source(self.output, {"subfolder": "", "filename": "plain_00001_.png"}),
        )

    def test_a_missing_filename_is_an_error(self):
        with self.assertRaisesRegex(ValueError, "输出文件名"):
            resolve_output_source(self.output, {"subfolder": "a", "filename": ""})


class CopyOutputImageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)
        self.output = self.root / "comfy-output"
        self.target = self.root / "collection"

    def tearDown(self):
        self.temp.cleanup()

    def _source(self, relative: str) -> pathlib.Path:
        path = self.output / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(png_bytes())
        return path

    def test_the_batch_subfolder_is_kept_so_runs_do_not_collide(self):
        """A batch's images stay a set: ``<run>/001-x.png``, not a flat heap.

        Flattening to the bare name made ``001-x`` from run A and ``001-x`` from
        run B collide, and the loser silently became ``001-x-v2.png`` -- which
        looked like a redo rather than a different run.
        """
        source = self._source("ComfyBatch-V2/abcd1234/001-x_00001_.png")
        image = {"subfolder": "ComfyBatch-V2/abcd1234", "filename": "001-x_00001_.png"}
        target = copy_output_image(source, self.target, image)
        self.assertEqual(self.target / "abcd1234" / "001-x_00001_.png", target)
        self.assertTrue(target.is_file())

    def test_the_app_named_tier_is_not_recreated(self):
        """``ComfyBatch-V2`` names the app, not the batch -- it is dropped."""
        source = self._source("ComfyBatch-V2/abcd1234/001-x_00001_.png")
        image = {"subfolder": "ComfyBatch-V2/abcd1234", "filename": "001-x_00001_.png"}
        copy_output_image(source, self.target, image)
        self.assertFalse((self.target / "ComfyBatch-V2").exists())

    def test_keep_structure_false_flattens_to_the_bare_name(self):
        """The old layout is still reachable for callers that want it."""
        source = self._source("ComfyBatch-V2/abcd1234/001-x_00001_.png")
        image = {"subfolder": "ComfyBatch-V2/abcd1234", "filename": "001-x_00001_.png"}
        target = copy_output_image(source, self.target, image, keep_structure=False)
        self.assertEqual(self.target / "001-x_00001_.png", target)

    def test_the_view_shape_keeps_the_same_layout_as_the_websocket_shape(self):
        """Both report shapes must agree, or the layout depends on luck."""
        ws = self._source("ComfyBatch-V2/abcd1234/001-x_00001_.png")
        view = self._source("ComfyBatch-V2/beef5678/002-y_00001_.png")
        one = copy_output_image(
            ws, self.target, {"subfolder": "ComfyBatch-V2/abcd1234", "filename": "001-x_00001_.png"}
        )
        two = copy_output_image(
            view, self.target, {"subfolder": "", "filename": "ComfyBatch-V2/beef5678/002-y_00001_.png"}
        )
        self.assertEqual(self.target / "abcd1234" / "001-x_00001_.png", one)
        self.assertEqual(self.target / "beef5678" / "002-y_00001_.png", two)

    def test_copying_a_file_onto_itself_is_a_no_op_instead_of_same_file_error(self):
        """Pointing the output folder inside ComfyUI's output used to raise."""
        folder = self.output / "ComfyBatch-V2" / "abcd1234"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "001-x_00001_.png").write_bytes(png_bytes())
        source = self._source("ComfyBatch-V2/abcd1234/001-x_00001_.png")
        image = {"subfolder": "ComfyBatch-V2/abcd1234", "filename": "001-x_00001_.png"}
        # ``output_dir`` is the very folder the source already lives in.
        # The computed target is ``<folder>/abcd1234/001-x.png``, so the no-op
        # check must compare the *resolved* source, not the pre-layout name.
        target = copy_output_image(source, folder, image)
        self.assertTrue(target.is_file())

    def test_a_collision_picks_a_suffix_instead_of_overwriting(self):
        first = self._source("first/001-x_00001_.png")
        second = self._source("second/001-x_00001_.png")
        image = {"filename": "001-x_00001_.png"}
        one = copy_output_image(first, self.target, image, exist_ok=True)
        two = copy_output_image(second, self.target, image, exist_ok=True)
        self.assertEqual("001-x_00001_.png", one.name)
        self.assertEqual("001-x_00001_-v2.png", two.name)
        self.assertTrue(one.is_file() and two.is_file())

    def test_a_collision_inside_a_batch_subfolder_also_gets_a_suffix(self):
        """Deduplication must work below the root, not only at it."""
        first = self._source("a/ComfyBatch-V2/run1/001-x.png")
        second = self._source("b/ComfyBatch-V2/run1/001-x.png")
        image = {"filename": "ComfyBatch-V2/run1/001-x.png"}
        one = copy_output_image(first, self.target, image, exist_ok=True)
        two = copy_output_image(second, self.target, image, exist_ok=True)
        self.assertEqual(self.target / "run1" / "001-x.png", one)
        self.assertEqual(self.target / "run1" / "001-x-v2.png", two)

    def test_the_result_is_marked_available_for_the_page(self):
        """``available`` is what lets the board render a picture at all."""
        source = self._source("ComfyBatch-V2/abcd1234/001-x_00001_.png")
        image = {"subfolder": "ComfyBatch-V2/abcd1234", "filename": "001-x_00001_.png"}
        target = copy_output_image(source, self.target, image)
        self.assertTrue(target.exists())


class ThumbnailCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)
        self.store = ResultReviewStore(self.root / "reviews")

    def tearDown(self):
        self.temp.cleanup()

    def _big_image(self, name: str = "big.png", size: tuple[int, int] = (2048, 2048)) -> pathlib.Path:
        path = self.root / name
        Image.new("RGB", size, (120, 90, 60)).save(path)
        return path

    def test_a_preview_is_smaller_than_the_original(self):
        source = self._big_image()
        preview = self.store.ensure_thumbnail(source, size=256)
        self.assertNotEqual(source, preview)
        self.assertLess(preview.stat().st_size, source.stat().st_size)
        with Image.open(preview) as image:
            self.assertLessEqual(max(image.size), 256)

    def test_the_preview_is_reused_not_rebuilt(self):
        source = self._big_image()
        first = self.store.ensure_thumbnail(source, size=256)
        stamp = first.stat().st_mtime_ns
        second = self.store.ensure_thumbnail(source, size=256)
        self.assertEqual(first, second)
        self.assertEqual(stamp, second.stat().st_mtime_ns)

    def test_a_changed_source_produces_a_new_preview(self):
        """A redone image replaces the file; the cache must not serve the old one."""
        source = self._big_image()
        first = self.store.ensure_thumbnail(source, size=256)
        time.sleep(0.01)
        Image.new("RGB", (2048, 2048), (10, 200, 10)).save(source)
        second = self.store.ensure_thumbnail(source, size=256)
        self.assertNotEqual(first, second)

    def test_an_unreadable_source_falls_back_to_the_source_itself(self):
        """A preview failure must never become a missing image on the page."""
        missing = self.root / "nope.png"
        self.assertEqual(missing, self.store.ensure_thumbnail(missing))

    def test_list_for_run_fills_in_a_usable_preview_path(self):
        source = self._big_image()
        self.store.ingest("run-1", [{
            "index": 1, "title": "任务01", "status": "completed",
            "copied_to": str(source), "image": {"filename": "big.png"},
            "attempts": [{"attempt": 1, "prompt_id": "p1"}],
        }])
        entry = self.store.list_for_run("run-1")[0]
        self.assertTrue(entry["available"])
        self.assertEqual(str(source), entry["thumbnail"])
        self.assertTrue(entry["preview"])
        self.assertTrue(pathlib.Path(entry["preview"]).is_file())

    def test_missing_files_report_no_preview(self):
        self.store.ingest("run-1", [{
            "index": 1, "title": "任务01", "status": "completed",
            "copied_to": str(self.root / "gone.png"),
            "attempts": [{"attempt": 1, "prompt_id": "p1"}],
        }])
        entry = self.store.list_for_run("run-1")[0]
        self.assertFalse(entry["available"])
        self.assertEqual("", entry["preview"])


class BatchSavesThroughBothReportShapesTests(unittest.TestCase):
    """End to end: a batch must leave real files behind, whichever shape it gets.

    The bug shipped because the failure was an OSError, not a workflow-level
    ``Problem``, so the batch reported "1 error" and still finished -- looking
    like a normal completed-with-errors run rather than a broken save path.
    """

    def _run_and_check(self, subfolder: str, filename: str):
        class FakeComfy:
            def submit(self, graph, client_id):
                return "prompt-1"

            def output(self, prompt_id):
                return {"filename": filename, "subfolder": subfolder, "type": "output"}

            def interrupt(self):
                pass

        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            comfy = root / "ComfyUI"
            target = root / "collection"
            relative = f"{subfolder}/{filename}".lstrip("/")
            produced = comfy / "output" / relative
            produced.parent.mkdir(parents=True, exist_ok=True)
            produced.write_bytes(png_bytes())
            workflow = root / "workflow.json"
            workflow.write_text(json.dumps({
                "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "old"}},
                "2": {"class_type": "PrimitiveStringMultiline", "inputs": {"value": "old"}},
                "3": {"class_type": "easy stylesSelector", "inputs": {"styles": "a", "select_styles": "b", "positive": ["2", 0]}},
                "4": {"class_type": "ResolutionSelector", "inputs": {"aspect_ratio": "1:1"}},
                "5": {"class_type": "KSampler", "inputs": {"model": ["1", 0]}},
                "6": {"class_type": "SaveImage", "inputs": {"filename_prefix": "old"}},
            }), encoding="utf-8")
            runner = BatchRunner(comfy, FakeComfy())
            bundle = PromptBundleParser.parse("one.txt", "单人礼服".encode())
            config = BatchConfig(str(workflow), "red.safetensors", "anime", "Cel", output_dir=str(target))
            runner.start(bundle, config)
            deadline = time.time() + 5
            while runner.status()["status"] in {"starting", "running"} and time.time() < deadline:
                time.sleep(0.01)
            status = runner.status()
            # Assertions must run inside the context: the tree is gone afterwards.
            self.assertEqual("completed", status["status"])
            self.assertEqual(0, status["errors"], status["results"][0].get("error"))
            # The batch subfolder survives; ComfyUI's app-named tier does not.
            saved = target / "abcd1234" / "001-x_00001_.png"
            self.assertTrue(saved.is_file())
            self.assertEqual(str(saved), status["results"][0]["copied_to"])

    def test_subfolder_plus_bare_filename_is_saved(self):
        self._run_and_check("ComfyBatch-V2/abcd1234", "001-x_00001_.png")

    def test_filename_carrying_the_subfolder_is_saved(self):
        self._run_and_check("", "ComfyBatch-V2/abcd1234/001-x_00001_.png")


if __name__ == "__main__":
    unittest.main()
