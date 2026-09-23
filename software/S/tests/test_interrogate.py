from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from comfybatch_gateway import ProductionGateway  # noqa: E402
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

    def submit(self, graph, client_id):
        self.graphs.append(graph)
        return f"prompt-{len(self.graphs)}"

    def poll(self, prompt_id):
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


if __name__ == "__main__":
    unittest.main()
