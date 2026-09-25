"""Offline child process for the hard-restart segment regression test."""

from __future__ import annotations

import json
import pathlib
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "src"))

from comfybatch_gateway import FakeComfyGateway
from comfybatch_v2_core import BatchConfig, BatchRunner, PromptBundleParser, RunProgressStore


class Client:
    def __init__(self, output_dir: pathlib.Path):
        self.gateway = FakeComfyGateway(output_dir=output_dir)

    def submit(self, graph, client_id):
        return self.gateway.submit(graph, client_id)

    def poll(self, prompt_id):
        return self.gateway.poll(prompt_id)

    def interrupt(self):
        self.gateway.interrupt()


def main(root: pathlib.Path, stage: str) -> int:
    comfy = root / "ComfyUI"
    store = RunProgressStore(root / "runs")
    client = Client(comfy / "output")
    runner = BatchRunner(comfy, client, progress_store=store)
    if stage == "first":
        payload = {"items": [{"title": f"任务{i}", "prompt": f"提示词 {i}"} for i in range(1, 5)]}
        bundle = PromptBundleParser.parse("items.json", json.dumps(payload).encode())
        config = BatchConfig(
            str(root / "workflow.json"), "Krea2-red.safetensors",
            output_dir=str(root / "out"), segment_size=2,
        )
        runner.start(bundle, config)
        while runner.status()["status"] != "segment":
            (root / "worker-state.json").write_text(json.dumps({
                "status": runner.status(), "submitted": len(client.gateway.submitted),
            }, ensure_ascii=False), encoding="utf-8")
            time.sleep(0.05)
        (root / "worker-state.json").write_text(json.dumps({
            "status": runner.status(), "submitted": len(client.gateway.submitted),
        }, ensure_ascii=False), encoding="utf-8")
        # Parent terminates this process at the boundary to simulate a crash.
        while True:
            time.sleep(1)
    else:
        runner.resume_interrupted()
        deadline = time.monotonic() + 15
        while runner.status()["status"] not in {"completed", "cancelled", "aborted", "error"}:
            if time.monotonic() > deadline:
                return 2
            time.sleep(0.01)
        return 0 if runner.status()["status"] == "completed" else 3


if __name__ == "__main__":
    raise SystemExit(main(pathlib.Path(sys.argv[1]), sys.argv[2]))
