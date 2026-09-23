from __future__ import annotations

import pathlib
import sys
import threading
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from comfybatch_gateway import ProductionGateway  # noqa: E402
from comfybatch_v2_core import ComfyClient  # noqa: E402
from comfybatch_interrogate import (  # noqa: E402
    BLIP_NODE,
    EASY_NODE,
    InterrogationUnavailable,
    ImageInterrogator,
    available_backends,
    build_graph,
    capability_payload,
)
from comfybatch_v2_app import is_loopback_url, loopback_host  # noqa: E402


def schema(*nodes: str) -> dict:
    return {name: {"input": {"required": {}}} for name in nodes}


class ScriptedClient:
    def __init__(self, fail_easy: bool = False):
        self.fail_easy = fail_easy
        self.graphs: list[dict] = []

    def submit(self, graph, client_id, *, timeout=None):
        self.graphs.append(graph)
        return f"prompt-{len(self.graphs)}"

    def poll(self, prompt_id, *, timeout=None):
        backend = self.graphs[int(prompt_id.rsplit("-", 1)[1]) - 1]["2"]["class_type"]
        if backend == EASY_NODE and self.fail_easy:
            return {"state": "error", "problems": ["easy model unavailable"], "entry": {}}
        return {
            "state": "done", "problems": [],
            "entry": {"outputs": {"3": {"text": [f"caption from {backend}"]}}},
        }


class ImageInterrogatorTests(unittest.TestCase):
    def test_internal_service_and_comfy_url_are_loopback_only(self):
        self.assertEqual("127.0.0.1", loopback_host("localhost"))
        self.assertTrue(is_loopback_url("http://127.0.0.1:8188"))
        self.assertTrue(is_loopback_url("http://[::1]:8188"))
        for value in ("0.0.0.0", "192.168.1.10"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "仅允许监听本机"):
                loopback_host(value)
        self.assertFalse(is_loopback_url("https://example.com/comfy"))

    def test_prefers_easy_interrogator_and_builds_local_graph(self):
        info = schema("LoadImage", "H3ShowText", EASY_NODE, BLIP_NODE)
        client = ScriptedClient()
        result = ImageInterrogator(client, timeout=1, poll_interval=0).run("imports/example.png", info)
        self.assertEqual(EASY_NODE, result["backend"])
        self.assertTrue(result["local_only"])
        graph = client.graphs[0]
        self.assertEqual("imports/example.png", graph["1"]["inputs"]["image"])
        self.assertEqual(["2", 0], graph["3"]["inputs"]["text"])
        self.assertEqual("fast", graph["2"]["inputs"]["mode"])

    def test_falls_back_to_blip_when_easy_is_missing(self):
        info = schema("LoadImage", "H3ShowText", BLIP_NODE)
        result = ImageInterrogator(ScriptedClient(), timeout=1, poll_interval=0).run("image.png", info)
        self.assertEqual(BLIP_NODE, result["backend"])
        self.assertEqual([BLIP_NODE], available_backends(info))

    def test_falls_back_to_blip_when_easy_execution_fails(self):
        info = schema("LoadImage", "H3ShowText", EASY_NODE, BLIP_NODE)
        client = ScriptedClient(fail_easy=True)
        result = ImageInterrogator(client, timeout=1, poll_interval=0).run("image.png", info)
        self.assertEqual(BLIP_NODE, result["backend"])
        self.assertEqual([EASY_NODE, BLIP_NODE], [graph["2"]["class_type"] for graph in client.graphs])

    def test_missing_nodes_return_actionable_installation_message(self):
        details = capability_payload({})
        self.assertFalse(details["available"])
        with self.assertRaisesRegex(InterrogationUnavailable, "安装或启用"):
            ImageInterrogator(ScriptedClient(), timeout=1, poll_interval=0).run("image.png", {})

    def test_blip_graph_uses_bounded_caption_lengths(self):
        graph = build_graph("image.png", BLIP_NODE)
        self.assertEqual(24, graph["2"]["inputs"]["min_length"])
        self.assertEqual(80, graph["2"]["inputs"]["max_length"])

    def test_text_only_success_is_done_in_gateway(self):
        gateway = ProductionGateway()
        gateway.request = lambda *args, **kwargs: {"p": {
            "status": {"status_str": "success", "completed": True},
            "outputs": {"3": {"text": ["local caption"]}},
        }}
        result = gateway.poll("p")
        self.assertEqual("done", result["state"])
        self.assertEqual("local caption", result["entry"]["outputs"]["3"]["text"][0])

    def test_gateway_uses_caller_deadline_for_submit_and_poll(self):
        gateway = ProductionGateway()
        calls = []

        def request(path, method="GET", payload=None, timeout=30):
            calls.append((path, timeout))
            if path == "/prompt":
                return {"prompt_id": "p"}
            return {"p": {"status": {"status_str": "success"}, "outputs": {"3": {"text": ["ok"]}}}}

        gateway.request = request
        self.assertEqual("p", gateway.submit({}, "client", timeout=0.4))
        self.assertEqual("done", gateway.poll("p", timeout=0.2)["state"])
        self.assertEqual([("/prompt", 0.4), ("/history/p", 0.2)], calls)

    def test_comfy_client_preserves_the_interrogation_deadline(self):
        gateway = ProductionGateway()
        calls = []

        def request(path, method="GET", payload=None, timeout=30):
            calls.append((path, timeout))
            if path == "/prompt":
                return {"prompt_id": "p"}
            return {"p": {"status": {"status_str": "success"}, "outputs": {"3": {"text": ["ok"]}}}}

        gateway.request = request
        result = ImageInterrogator(
            ComfyClient(gateway=gateway), timeout=0.2, poll_interval=0
        ).run("image.png", schema("LoadImage", "H3ShowText", BLIP_NODE))
        self.assertEqual("ok", result["prompt"])
        self.assertGreater(calls[0][1], calls[1][1])

    def test_queue_wait_counts_toward_single_operation_timeout(self):
        interrogator = ImageInterrogator(ScriptedClient(), timeout=0.02, poll_interval=0)
        entered = threading.Event()

        def hold_lock():
            with interrogator._lock:
                entered.set()
                time.sleep(0.08)

        worker = threading.Thread(target=hold_lock)
        worker.start()
        entered.wait(1)
        started = time.monotonic()
        with self.assertRaisesRegex(TimeoutError, "队列超时"):
            interrogator.run("image.png", schema("LoadImage", "H3ShowText", BLIP_NODE))
        self.assertLess(time.monotonic() - started, 0.07)
        worker.join()

    def test_submit_and_poll_receive_only_the_remaining_total_timeout(self):
        class DeadlineClient:
            def __init__(self):
                self.timeouts = []

            def submit(self, _graph, _client_id, *, timeout=None):
                self.timeouts.append(("submit", timeout))
                time.sleep(0.01)
                return "prompt-1"

            def poll(self, _prompt_id, *, timeout=None):
                self.timeouts.append(("poll", timeout))
                return {
                    "state": "done", "problems": [],
                    "entry": {"outputs": {"3": {"text": ["bounded"]}}},
                }

        client = DeadlineClient()
        result = ImageInterrogator(client, timeout=0.2, poll_interval=0).run(
            "image.png", schema("LoadImage", "H3ShowText", BLIP_NODE)
        )

        self.assertEqual("bounded", result["prompt"])
        submit_timeout = client.timeouts[0][1]
        poll_timeout = client.timeouts[1][1]
        self.assertGreater(submit_timeout, 0)
        self.assertLessEqual(submit_timeout, 0.2)
        self.assertGreater(poll_timeout, 0)
        self.assertLess(poll_timeout, submit_timeout)


if __name__ == "__main__":
    unittest.main()
