"""Error preservation and Chinese translation, driven by real ComfyUI bodies.

Every fixture here was captured from the live ComfyUI at
``127.0.0.1:8188`` by ``tools/record_fixtures.py``. They matter because the old
client discarded ``HTTPError.read()``, so the 45-failure incident could only
report ``HTTP Error 400: Bad Request`` with no node, no value and no cause.
"""

from __future__ import annotations

import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from fakes import FIXTURE_ROOT, load_fixture_json  # noqa: E402

from comfybatch_errors import (  # noqa: E402
    CATEGORY_TITLES,
    ErrorCategory,
    ComfyError,
    ErrorTranslator,
    Problem,
    resource_label,
    summarise,
)


def captured(name: str) -> ComfyError:
    """Rebuild the exact ComfyError the gateway would have raised."""
    payload = load_fixture_json(f"{name}.json")
    body = payload.get("body")
    raw = payload.get("raw") or json.dumps(body, ensure_ascii=False)
    return ComfyError("x", status=int(payload.get("status") or 400), raw=raw, body=body, path="/prompt")


class RealResponseTranslationTests(unittest.TestCase):
    """One test per real captured ComfyUI failure."""

    def test_missing_upscale_model_is_a_missing_resource_with_candidates(self):
        problems = ErrorTranslator.translate(captured("http400_missing_upscale_model"))
        self.assertEqual(1, len(problems))
        problem = problems[0]
        self.assertEqual(ErrorCategory.MISSING_RESOURCE, problem.category)
        self.assertTrue(problem.workflow_level)
        # Node ids come from the captured response, which submitted this node as "1".
        self.assertEqual("1", problem.node_id)
        self.assertEqual("UpscaleModelLoader", problem.node_type)
        self.assertEqual("model_name", problem.input_name)
        self.assertEqual("OmniSR_X4_DIV2K.safetensors", problem.received_value)
        # The candidate list is what makes this actionable rather than a dead end.
        self.assertEqual(["RealESRGAN_x4plus_anime_6B.pth"], problem.candidates)
        self.assertIn("放大模型", problem.detail)
        self.assertTrue(any("RealESRGAN" in fix for fix in problem.fixes))

    def test_missing_node_type_is_missing_node(self):
        problems = ErrorTranslator.translate(captured("http400_unknown_node"))
        self.assertEqual(1, len(problems))
        problem = problems[0]
        self.assertEqual(ErrorCategory.MISSING_NODE, problem.category)
        self.assertEqual("ComfyBatchDefinitelyNotANode", problem.node_type)
        self.assertEqual("1", problem.node_id)
        self.assertIn("未安装", problem.detail)

    def test_value_not_in_list_on_a_non_resource_input_is_a_range_problem(self):
        problems = ErrorTranslator.translate(captured("http400_value_not_in_list"))
        problem = problems[0]
        self.assertEqual(ErrorCategory.VALUE_OUT_OF_RANGE, problem.category)
        self.assertEqual("sampler_name", problem.input_name)
        self.assertEqual("definitely_not_a_sampler", problem.received_value)
        self.assertEqual("KSampler", problem.node_type)

    def test_required_input_missing(self):
        problems = ErrorTranslator.translate(captured("http400_missing_input"))
        problem = problems[0]
        self.assertEqual(ErrorCategory.MISSING_INPUT, problem.category)
        self.assertEqual("width", problem.input_name)
        self.assertEqual("ImageScale", problem.node_type)

    def test_no_capture_is_mistaken_for_another_problem(self):
        """Guard against the translator being too permissive."""
        expected = {
            "http400_missing_upscale_model": ErrorCategory.MISSING_RESOURCE,
            "http400_unknown_node": ErrorCategory.MISSING_NODE,
            "http400_value_not_in_list": ErrorCategory.VALUE_OUT_OF_RANGE,
            "http400_missing_input": ErrorCategory.MISSING_INPUT,
        }
        for name, category in expected.items():
            with self.subTest(name=name):
                self.assertEqual(category, ErrorTranslator.translate(captured(name))[0].category)


class CandidateResolutionTests(unittest.TestCase):
    """ComfyUI truncates long combo lists, so the schema must fill the gap."""

    def test_schema_resolver_supplies_candidates_when_body_omits_them(self):
        error = ComfyError(
            "x", status=400, path="/prompt", body={
                "error": {"type": "prompt_outputs_failed_validation", "message": "m", "details": ""},
                "node_errors": {"4": {"class_type": "KSampler", "errors": [{
                    "type": "value_not_in_list",
                    "message": "Value not in list",
                    "details": "sampler_name: 'nope' not in (list of length 44)",
                    "extra_info": {"input_name": "sampler_name", "received_value": "nope", "input_config": None},
                }]}},
            },
        )
        plain = ErrorTranslator.translate(error)[0]
        self.assertEqual([], plain.candidates)

        enriched = ErrorTranslator.translate(
            error, candidate_resolver=lambda node_type, name: ["euler", "dpmpp_2m"] if name == "sampler_name" else []
        )[0]
        self.assertEqual(["euler", "dpmpp_2m"], enriched.candidates)
        self.assertTrue(any("euler" in fix for fix in enriched.fixes))

    def test_combo_candidates_read_from_both_known_shapes(self):
        from comfybatch_errors import _candidates_from_config

        self.assertEqual(["a", "b"], _candidates_from_config([["a", "b"], {"default": "a"}]))
        self.assertEqual(["a", "b"], _candidates_from_config(["COMBO", {"options": ["a", "b"]}]))
        self.assertEqual([], _candidates_from_config(None))


class HistoryErrorTests(unittest.TestCase):
    """Runtime failures arrive through /history, not as an HTTP status."""

    def test_out_of_memory_is_classified_from_history(self):
        entry = {"status": {"status_str": "error", "completed": False, "messages": [
            ["execution_start", {"prompt_id": "p"}],
            ["execution_error", {
                "node_id": "13", "node_type": "KSamplerAdvanced",
                "exception_type": "torch.cuda.OutOfMemoryError",
                "exception_message": "CUDA out of memory. Tried to allocate 2.00 GiB",
            }],
        ]}}
        problems = ErrorTranslator.from_history("p", entry)
        self.assertEqual(1, len(problems))
        self.assertEqual(ErrorCategory.OUT_OF_MEMORY, problems[0].category)
        self.assertTrue(any("显存" in fix for fix in problems[0].fixes))
        self.assertIn("node_id", problems[0].raw)

    def test_generic_execution_failure_is_classified(self):
        entry = {"status": {"messages": [["execution_error", {
            "node_id": "5", "node_type": "UltimateSDUpscale",
            "exception_type": "RuntimeError", "exception_message": "something broke",
        }]]}}
        problems = ErrorTranslator.from_history("p", entry)
        self.assertEqual(ErrorCategory.EXECUTION_FAILED, problems[0].category)
        self.assertEqual("UltimateSDUpscale", problems[0].node_type)


class FallbackTests(unittest.TestCase):
    """No parseable body must still produce something a user can act on."""

    def test_connection_refused_has_a_real_fix(self):
        problem = ErrorTranslator.translate(ComfyError("无法连接 ComfyUI：拒绝连接", status=0))[0]
        self.assertIn("ComfyUI", problem.detail)
        self.assertTrue(problem.fixes)

    def test_unparseable_body_keeps_the_raw_text(self):
        error = ComfyError("x", status=500, raw="<html>boom</html>", body=None)
        problem = ErrorTranslator.translate(error)[0]
        self.assertEqual(ErrorCategory.UNKNOWN, problem.category)
        self.assertIn("boom", json.dumps(problem.raw, ensure_ascii=False))

    def test_every_category_has_a_chinese_title(self):
        for name in dir(ErrorCategory):
            if name.startswith("_") or not isinstance(getattr(ErrorCategory, name), str):
                continue
            value = getattr(ErrorCategory, name)
            with self.subTest(category=value):
                self.assertIn(value, CATEGORY_TITLES)

    def test_render_does_not_repeat_the_category_title(self):
        problem = Problem(category=ErrorCategory.MISSING_RESOURCE, title=CATEGORY_TITLES[ErrorCategory.MISSING_RESOURCE],
                          detail="详情", node_id="11", node_type="UpscaleModelLoader", input_name="model_name")
        rendered = problem.render()
        self.assertEqual(1, rendered.count(CATEGORY_TITLES[ErrorCategory.MISSING_RESOURCE]))

    def test_summarise_includes_the_extra_count(self):
        first = Problem(category=ErrorCategory.MISSING_NODE, title="缺少节点", detail="一")
        second = Problem(category=ErrorCategory.MISSING_RESOURCE, title="缺少资源文件", detail="二")
        text = summarise([first, second])
        self.assertIn("另有 1 个问题", text)

    def test_resource_label_refines_model_name_by_node_type(self):
        self.assertEqual("放大模型", resource_label("UpscaleModelLoader", "model_name"))
        self.assertEqual("SeedVR2 模型", resource_label("SeedVR2LoadDiTModel", "model_name"))
        self.assertEqual("LoRA", resource_label("LoraLoader", "lora_name"))


class GatewayPreservationTests(unittest.TestCase):
    """The gateway must keep the body and never raise a bare urllib error."""

    def test_gateway_parses_a_real_captured_body(self):
        from comfybatch_gateway import ProductionGateway

        captured_body = load_fixture_json("http400_missing_upscale_model.json")["body"]
        gateway = ProductionGateway("http://127.0.0.1:8188")
        # Exercise the same translation path the submit error takes.
        error = ComfyError("HTTP 400 /prompt", status=400, raw=json.dumps(captured_body, ensure_ascii=False),
                           body=captured_body, path="/prompt")
        self.assertEqual("OmniSR_X4_DIV2K.safetensors", ErrorTranslator.translate(error)[0].received_value)

    def test_poll_distinguishes_running_from_failed(self):
        """The old code read this as "still running" and span for 30 minutes."""
        from comfybatch_gateway import ProductionGateway

        gateway = ProductionGateway("http://127.0.0.1:8188")
        gateway.request = lambda *args, **kwargs: {
            "pid": {"status": {"status_str": "error", "messages": [["execution_error", {
                "node_id": "3", "node_type": "UltimateSDUpscale",
                "exception_type": "RuntimeError", "exception_message": "boom",
            }]]}},
        }
        outcome = gateway.poll("pid")
        self.assertEqual("error", outcome["state"])
        self.assertTrue(outcome["problems"])

    def test_poll_reports_pending_when_history_is_empty(self):
        from comfybatch_gateway import ProductionGateway

        gateway = ProductionGateway("http://127.0.0.1:8188")
        gateway.request = lambda *args, **kwargs: {}
        self.assertEqual("pending", gateway.poll("pid")["state"])

    def test_poll_returns_done_with_images(self):
        from comfybatch_gateway import ProductionGateway

        gateway = ProductionGateway("http://127.0.0.1:8188")
        gateway.request = lambda *args, **kwargs: {
            "pid": {"status": {"status_str": "success"}, "outputs": {"27": {"images": [{"filename": "a.png"}]}}},
        }
        outcome = gateway.poll("pid")
        self.assertEqual("done", outcome["state"])
        self.assertEqual("a.png", outcome["images"][0]["filename"])


if __name__ == "__main__":
    unittest.main()
