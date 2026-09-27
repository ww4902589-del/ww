"""Stable, accessible DOM seam for the first local Agent operation milestone."""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys
import unittest
from html.parser import HTMLParser

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from fakes import html_source, js_source  # noqa: E402


class Elements(HTMLParser):
    def __init__(self):
        super().__init__()
        self.by_id: dict[str, dict[str, str]] = {}
        self.body: dict[str, str | None] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        values = dict(attrs)
        if tag == "body":
            self.body = values
        if element_id := values.get("id"):
            if element_id in self.by_id:
                raise AssertionError(f"duplicate DOM id: {element_id}")
            self.by_id[element_id] = {"tag": tag, **values}


class AgentDomContractTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is optional")
    def test_confirm_all_reports_lease_consent_and_result(self):
        script = pathlib.Path(__file__).with_name("agent_confirm_feedback_behavior.js")
        source = pathlib.Path(__file__).resolve().parents[1] / "src" / "app.js"
        result = subprocess.run(
            ["node", str(script), str(source)], capture_output=True,
            text=True, encoding="utf-8", timeout=15, check=False,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    @unittest.skipUnless(shutil.which("node"), "Node.js is optional")
    def test_visible_action_status_tracks_lease_preflight_and_start(self):
        script = pathlib.Path(__file__).with_name("agent_contract_behavior.js")
        source = pathlib.Path(__file__).resolve().parents[1] / "src" / "app.js"
        result = subprocess.run(
            ["node", str(script), str(source)], capture_output=True,
            text=True, encoding="utf-8", timeout=15, check=False,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_primary_actions_have_stable_machine_and_accessible_names(self):
        page = Elements()
        page.feed(html_source())
        actions = {
            "bundleFile": "import-prompts",
            "imageFiles": "import-images",
            "extractImageUrl": "extract-images",
            "drawingImport": "import-drawing",
            "workflow": "select-workflow",
            "purposeSelect": "select-purpose",
            "model": "select-model",
            "preflightButton": "preflight",
            "preflightQuickButton": "preflight",
            "start": "start-batch",
            "confirmAllButton": "confirm-all",
        }
        for element_id, action in actions.items():
            with self.subTest(element_id=element_id):
                element = page.by_id[element_id]
                self.assertEqual(action, element.get("data-agent-action"))
                self.assertTrue(element.get("aria-label"), "Agent 和屏幕阅读器应共享可访问名称")
        self.assertEqual("preflightAction(this)", page.by_id["preflightButton"].get("onclick"))
        self.assertEqual("goStep(3);preflightAction($('preflightButton'))", page.by_id["preflightQuickButton"].get("onclick"))
        self.assertEqual("startBatch(this)", page.by_id["start"].get("onclick"))

    def test_persistent_status_exposes_action_result_and_lease_state(self):
        page = Elements()
        page.feed(html_source())
        status = page.by_id["agentActionStatus"]
        self.assertEqual("status", status.get("role"))
        self.assertEqual("polite", status.get("aria-live"))
        self.assertEqual("idle", status.get("data-agent-state"))
        self.assertTrue(status.get("aria-label"))
        self.assertEqual("unknown", page.body.get("data-agent-lease"))

        script = js_source()
        self.assertIn("function publishAgentStatus(", script)
        self.assertIn("document.body.dataset.agentLease", script)
        self.assertIn('data-agent-action="take-over-edit"', script)
        self.assertTrue('id="takeOverLeaseButton"' in script)


if __name__ == "__main__":
    unittest.main()
