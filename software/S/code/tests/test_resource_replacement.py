"""In-software resource replacement and seed control.

Two capabilities the user asked for directly:

1. A missing model must be fixable *inside the software*, without hunting through
   folders. Nothing is substituted silently -- the user picks a real candidate and
   the choice stays visible in the audit afterwards.
2. The seed must be visible and controllable, because it decides whether a redo
   can reproduce an image.
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
    SOURCE_ROOT,
    load_fixture_json,
    ui_workflow,
    write_workflow,
)

from comfybatch_nodeschema import (  # noqa: E402
    SOURCE_USER_REPLACED,
    NodeSchemaRegistry,
    apply_seed,
    convert_node,
    graph_seeds,
    structural_fingerprint,
    set_active_registry,
)
from comfybatch_v2_core import (  # noqa: E402
    BatchConfig,
    Krea2WorkflowAdapter,
    ResourceOverrideStore,
)

WORKFLOW = "workflow_Krea2-极清生图流_SeedVR2-int8图像放大.json"


def real_registry() -> NodeSchemaRegistry:
    return NodeSchemaRegistry.from_path(SOURCE_ROOT.parent / "tests" / "fixtures" / "object_info.json")


def config(**overrides) -> BatchConfig:
    values = {
        "workflow_path": str(SOURCE_ROOT.parent / "tests" / "fixtures" / WORKFLOW),
        "model": "redcraftHybridH3A2A_30Krea2.safetensors",
        "workflow_variant": "save:1",
        "aspect_ratio": "16:9 (Widescreen)",
        "megapixels": 0.6,
    }
    values.update(overrides)
    return BatchConfig(**values)


class ResourceOverrideStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = ResourceOverrideStore(pathlib.Path(self.temp.name) / "rules")

    def tearDown(self):
        self.temp.cleanup()

    def test_remembers_and_groups_by_fingerprint(self):
        self.store.remember("fp-a", "11", "model_name", "RealESRGAN.pth", node_type="UpscaleModelLoader")
        self.store.remember("fp-a", "277", "model_name", "Other.pth")
        self.store.remember("fp-b", "11", "model_name", "Different.pth")

        grouped = self.store.for_fingerprint("fp-a")
        self.assertEqual({"11": {"model_name": "RealESRGAN.pth"}, "277": {"model_name": "Other.pth"}}, grouped)
        self.assertEqual({"11": {"model_name": "Different.pth"}}, self.store.for_fingerprint("fp-b"))

    def test_rules_survive_a_restart(self):
        self.store.remember("fp-a", "11", "model_name", "RealESRGAN.pth")
        reopened = ResourceOverrideStore(pathlib.Path(self.temp.name) / "rules")
        self.assertEqual({"11": {"model_name": "RealESRGAN.pth"}}, reopened.for_fingerprint("fp-a"))

    def test_forget_removes_only_that_rule(self):
        self.store.remember("fp-a", "11", "model_name", "a.pth")
        self.store.remember("fp-a", "11", "vae_name", "b.safetensors")
        self.store.forget("fp-a", "11", "model_name")
        self.assertEqual({"11": {"vae_name": "b.safetensors"}}, self.store.for_fingerprint("fp-a"))

    def test_unknown_fingerprint_yields_nothing(self):
        self.store.remember("fp-a", "11", "model_name", "a.pth")
        self.assertEqual({}, self.store.for_fingerprint("fp-zzz"))

    def test_corrupt_document_degrades_to_empty(self):
        self.store.root.mkdir(parents=True, exist_ok=True)
        self.store.path.write_text("{not json", encoding="utf-8")
        self.assertEqual({"rules": {}}, self.store.load())

    def test_key_includes_the_fingerprint_so_a_changed_workflow_does_not_inherit(self):
        self.assertEqual("abc:11:model_name", ResourceOverrideStore.key("abc", "11", "model_name"))


class StructuralFingerprintTests(unittest.TestCase):
    """The rule key must survive cosmetic edits but not semantic ones.

    A whole-file hash changed when a node was merely dragged on the canvas, so a
    purely cosmetic edit silently disabled a saved rule.
    """

    @classmethod
    def setUpClass(cls):
        cls.workflow = load_fixture_json(WORKFLOW)
        cls.base = structural_fingerprint(cls.workflow)

    def fp(self, workflow) -> str:
        return structural_fingerprint(workflow)

    def test_dragging_a_node_keeps_the_fingerprint(self):
        moved = json.loads(json.dumps(self.workflow))
        moved["nodes"][0]["pos"] = [moved["nodes"][0]["pos"][0] + 1, moved["nodes"][0]["pos"][1]]
        self.assertEqual(self.base, self.fp(moved))

    def test_retitling_and_resizing_a_node_keeps_the_fingerprint(self):
        edited = json.loads(json.dumps(self.workflow))
        edited["nodes"][0]["title"] = "改名了"
        edited["nodes"][0]["size"] = [9, 9]
        edited["nodes"][0]["properties"] = {"extra": True}
        self.assertEqual(self.base, self.fp(edited))

    def test_reloading_the_same_file_keeps_the_fingerprint(self):
        self.assertEqual(self.base, self.fp(json.loads(json.dumps(self.workflow))))

    def test_changing_a_widget_value_changes_the_fingerprint(self):
        changed = json.loads(json.dumps(self.workflow))
        for node in changed["nodes"]:
            if node.get("type") == "KSamplerAdvanced" and node.get("widgets_values"):
                node["widgets_values"] = list(node["widgets_values"])
                node["widgets_values"][3] = 99
                break
        self.assertNotEqual(self.base, self.fp(changed), "改了参数就必须换指纹")

    def test_muting_an_active_node_changes_the_fingerprint(self):
        edited = json.loads(json.dumps(self.workflow))
        active = next(n for n in edited["nodes"] if int(n.get("mode", 0) or 0) == 0)
        active["mode"] = 4
        self.assertNotEqual(self.base, self.fp(edited), "静音节点会改变编译结果，必须换指纹")

    def test_adding_a_node_changes_the_fingerprint(self):
        edited = json.loads(json.dumps(self.workflow))
        edited["nodes"].append({"id": 99999, "type": "Note", "widgets_values": ["x"]})
        self.assertNotEqual(self.base, self.fp(edited))

    def test_rewiring_a_link_changes_the_fingerprint(self):
        edited = json.loads(json.dumps(self.workflow))
        edited["links"][0][3] = edited["nodes"][-1]["id"]
        self.assertNotEqual(self.base, self.fp(edited))

    def test_api_graphs_are_supported(self):
        graph = {"1": {"class_type": "UNETLoader", "inputs": {"unet_name": "a.safetensors"}},
                 "2": {"class_type": "KSampler", "inputs": {"model": ["1", 0], "steps": 8}}}
        first = structural_fingerprint(graph)
        self.assertEqual(12, len(first))
        same = json.loads(json.dumps(graph))
        self.assertEqual(first, structural_fingerprint(same))
        graph["2"]["inputs"]["steps"] = 9
        self.assertNotEqual(first, structural_fingerprint(graph))


class StaleRuleTests(unittest.TestCase):
    """A rule must never stop applying silently."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = ResourceOverrideStore(pathlib.Path(self.temp.name) / "rules")

    def tearDown(self):
        self.temp.cleanup()

    def test_rule_remembers_the_file_it_was_made_for(self):
        self.store.remember("fp-old", "11", "model_name", "a.pth", workflow_path="E:/w/f.json")
        rule = next(iter(self.store.load()["rules"].values()))
        self.assertEqual("E:/w/f.json", rule["workflow_path"])

    def test_a_changed_workflow_makes_the_rule_stale_not_silent(self):
        self.store.remember("fp-old", "11", "model_name", "a.pth", workflow_path="E:/w/f.json")
        self.assertEqual({}, self.store.for_fingerprint("fp-new"), "旧指纹下不应自动生效")
        stale = self.store.stale_for("E:/w/f.json", "fp-new")
        self.assertEqual(1, len(stale), "改动后必须能被识别出来并提示")

    def test_a_rule_for_a_different_file_is_not_stale_for_this_one(self):
        self.store.remember("fp-old", "11", "model_name", "a.pth", workflow_path="E:/w/other.json")
        self.assertEqual([], self.store.stale_for("E:/w/f.json", "fp-new"))

    def test_a_rule_at_the_current_fingerprint_is_not_stale(self):
        self.store.remember("fp-new", "11", "model_name", "a.pth", workflow_path="E:/w/f.json")
        self.assertEqual([], self.store.stale_for("E:/w/f.json", "fp-new"))

    def test_reapply_moves_rules_to_the_current_fingerprint(self):
        self.store.remember("fp-old", "11", "model_name", "a.pth", workflow_path="E:/w/f.json")
        self.store.reapply("E:/w/f.json", "fp-new")
        self.assertEqual({"11": {"model_name": "a.pth"}}, self.store.for_fingerprint("fp-new"))
        self.assertEqual([], self.store.stale_for("E:/w/f.json", "fp-new"), "重新启用后不应再提示")

    def test_case_change_survives_a_restart(self):
        """The exact scenario: save a rule, edit the workflow cosmetically, still works."""
        original = load_fixture_json(WORKFLOW)
        fingerprint = structural_fingerprint(original)
        self.store.remember(fingerprint, "11", "model_name", "RealESRGAN.pth", workflow_path="E:/w/f.json")

        moved = json.loads(json.dumps(original))
        moved["nodes"][0]["pos"] = [123.0, 456.0]
        reopened = ResourceOverrideStore(pathlib.Path(self.temp.name) / "rules")
        self.assertEqual(
            {"11": {"model_name": "RealESRGAN.pth"}},
            reopened.for_fingerprint(structural_fingerprint(moved)),
            "仅仅拖动节点不应让规则失效",
        )


class OverrideReachesTheCompiledGraphTests(unittest.TestCase):
    """The audit must be able to prove a replacement was the user's choice."""

    @classmethod
    def setUpClass(cls):
        cls.registry = real_registry()
        set_active_registry(cls.registry)

    def build(self, **overrides):
        cfg = config(**overrides)
        adapter = Krea2WorkflowAdapter.from_path(cfg.workflow_path, self.registry)
        graph = adapter.build("提示词", cfg, "ComfyBatch-V2/replace")
        return cfg, adapter, graph

    def test_without_a_replacement_the_missing_model_is_blocking(self):
        cfg, adapter, _graph = self.build()
        blocking = adapter.audit(cfg, branch="save:1")["blocking"]
        targets = [p for p in blocking if p["input_name"] == "model_name"]
        self.assertEqual(1, len(targets))
        self.assertEqual("fixture-resource-001.safetensors", targets[0]["received_value"])
        self.assertEqual(["RealESRGAN_x4plus_anime_6B.pth"], targets[0]["candidates"])

    def test_a_replacement_clears_it_and_is_tagged_as_the_users_choice(self):
        cfg, adapter, graph = self.build(resource_overrides={"11": {"model_name": "RealESRGAN_x4plus_anime_6B.pth"}})
        self.assertEqual("RealESRGAN_x4plus_anime_6B.pth", graph["11"]["inputs"]["model_name"])

        audit = adapter.audit(cfg, branch="save:1")
        self.assertEqual([], audit["blocking"])
        trace = audit["nodes"]["11"]["inputs"]["model_name"]
        self.assertEqual(SOURCE_USER_REPLACED, trace["source"])
        self.assertEqual("用户在软件内替换", trace["source_cn"])
        # The replacement is therefore discoverable, not silent.
        self.assertEqual(1, len([o for o in audit["overrides"] if o["source"] == SOURCE_USER_REPLACED]))

    def test_a_replacement_does_not_leak_onto_other_nodes(self):
        cfg, adapter, graph = self.build(resource_overrides={"11": {"model_name": "RealESRGAN_x4plus_anime_6B.pth"}})
        # Only node 11's model_name changed.
        for node_id, node in graph.items():
            if node_id == "11":
                continue
            for name, value in node["inputs"].items():
                if name == "model_name":
                    self.assertNotEqual("RealESRGAN_x4plus_anime_6B.pth", value,
                                        f"替换意外影响到了节点 {node_id}")

    def test_convert_node_applies_and_tags_the_override(self):
        node = {"id": 11, "type": "UpscaleModelLoader", "inputs": [
            {"name": "model_name", "widget": {"name": "model_name"}, "link": None},
        ], "widgets_values": ["Original.pth"]}
        inputs, sources, _problems = convert_node(
            "11", node, {}, config=config(), target_size=(1024, 576), output_prefix="p",
            prompt_text="p", source_image="", registry=self.registry,
            resource_overrides={"model_name": "Replacement.pth"},
        )
        self.assertEqual("Replacement.pth", inputs["model_name"])
        self.assertEqual(SOURCE_USER_REPLACED, sources["model_name"])


class SeedControlTests(unittest.TestCase):
    """Seeds must be visible and pinnable, because they decide reproducibility."""

    @classmethod
    def setUpClass(cls):
        cls.registry = real_registry()
        set_active_registry(cls.registry)

    def test_graph_seeds_reports_only_concrete_seeds(self):
        graph = {
            "5": {"class_type": "KSampler", "inputs": {"seed": 111, "model": ["1", 0]}},
            "19": {"class_type": "Seed (rgthree)", "inputs": {"seed": 222}},
            "21": {"class_type": "KSamplerAdvanced", "inputs": {"noise_seed": ["19", 0]}},
        }
        self.assertEqual({"5.seed": 111, "19.seed": 222}, graph_seeds(graph))

    def test_apply_seed_pins_every_concrete_seed_with_distinct_offsets(self):
        graph = {
            "5": {"class_type": "KSampler", "inputs": {"seed": 1}},
            "13": {"class_type": "KSamplerAdvanced", "inputs": {"noise_seed": 2}},
            "19": {"class_type": "Seed (rgthree)", "inputs": {"seed": 3}},
            "21": {"class_type": "KSamplerAdvanced", "inputs": {"noise_seed": ["19", 0]}},
        }
        apply_seed(graph, 5000)
        self.assertEqual(5000, graph["5"]["inputs"]["seed"])
        self.assertEqual(5001, graph["13"]["inputs"]["noise_seed"])
        self.assertEqual(5002, graph["19"]["inputs"]["seed"])
        # A linked seed is left linked: its value belongs to the producing node.
        self.assertEqual(["19", 0], graph["21"]["inputs"]["noise_seed"])

    def test_batch_config_pins_the_seed_reproducibly(self):
        cfg = config(seed=777)
        adapter = Krea2WorkflowAdapter.from_path(cfg.workflow_path, self.registry)
        first = adapter.build("提示词", cfg, "a")
        second = Krea2WorkflowAdapter.from_path(cfg.workflow_path, self.registry).build("提示词", cfg, "b")

        seeds_first, seeds_second = graph_seeds(first), graph_seeds(second)
        self.assertTrue(seeds_first)
        self.assertEqual(seeds_first, seeds_second, "指定种子后两次编译的种子必须一致")

    def test_without_a_pinned_seed_consecutive_builds_differ(self):
        """This is what makes ``same_seed`` a meaningful choice."""
        adapter = Krea2WorkflowAdapter.from_path(config().workflow_path, self.registry)
        first = graph_seeds(adapter.build("提示词", config(), "a"))
        second = graph_seeds(Krea2WorkflowAdapter.from_path(config().workflow_path, self.registry).build("提示词", config(), "b"))
        self.assertTrue(first and second)
        self.assertNotEqual(first, second, "未指定种子时每次都应是新随机种子")

    def test_config_round_trips_seed_and_overrides_from_json(self):
        cfg = BatchConfig.from_dict({
            "workflow_path": "w.json", "model": "m.safetensors",
            "seed": "12345", "resource_overrides": {"11": {"model_name": "x.pth"}},
        })
        self.assertEqual(12345, cfg.seed)
        self.assertEqual({"11": {"model_name": "x.pth"}}, cfg.resource_overrides)

    def test_empty_seed_string_is_treated_as_unset(self):
        cfg = BatchConfig.from_dict({"workflow_path": "w.json", "model": "m", "seed": ""})
        self.assertIsNone(cfg.seed)


class BatchSeedPinAndRedoTests(unittest.TestCase):
    """A batch seed pin must not defeat a seed-changing redo.

    Found on a real run: the batch's pinned seed lived on the stored config, so
    ``new_seed`` rebuilt with the same pin and ComfyUI served the cached image --
    the "new seed" redo produced a byte-identical result.
    """

    def _runner(self, root: pathlib.Path):
        from fakes import PrefixAwareComfy, make_comfy_tree
        from comfybatch_v2_core import BatchRunner, PromptBundleParser

        comfy = make_comfy_tree(root)
        (comfy / "models" / "diffusion_models" / "Krea2-red.safetensors").write_bytes(b"")
        workflow = write_workflow(root / "workflow.json", ui_workflow(model="Krea2-red.safetensors"))
        client = PrefixAwareComfy(comfy)
        runner = BatchRunner(comfy, client)
        config = BatchConfig(str(workflow), "Krea2-red.safetensors", output_dir=str(root / "out"))
        bundle = PromptBundleParser.parse("one.json", json.dumps({"items": [{"title": "t", "prompt": "p"}]}).encode())
        return runner, client, config, bundle

    def test_new_seed_redo_releases_the_batch_seed_pin(self):
        import time

        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            runner, client, config, bundle = self._runner(root)
            config.seed = 111

            def wait():
                deadline = time.time() + 5
                while runner.status()["status"] in {"starting", "running"} and time.time() < deadline:
                    time.sleep(0.01)
                return runner.status()

            runner.start(bundle, config)
            wait()
            first_seeds = graph_seeds(client.graphs[0])
            # Offset by one per node, so derive the expectation from the graph.
            self.assertEqual({111 + offset for offset in range(len(first_seeds))}, set(first_seeds.values()),
                             "批次种子应写入提交图")

            runner.redo(1, "new_seed")
            wait()
            second_seed = set(graph_seeds(client.graphs[1]).values())
            self.assertNotEqual(set(first_seeds.values()), set(graph_seeds(client.graphs[1]).values()),
                                "new_seed 重做必须换种子，不能被批次种子钉住")

    def test_same_seed_redo_keeps_the_seed(self):
        import time

        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            runner, client, config, bundle = self._runner(root)
            config.seed = 222

            def wait():
                deadline = time.time() + 5
                while runner.status()["status"] in {"starting", "running"} and time.time() < deadline:
                    time.sleep(0.01)
                return runner.status()

            runner.start(bundle, config)
            wait()
            first_seed = set(graph_seeds(client.graphs[0]).values())

            runner.redo(1, "same_seed")
            wait()
            self.assertEqual(first_seed, set(graph_seeds(client.graphs[1]).values()))

    def test_explicit_seed_pins_the_redo(self):
        import time

        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            runner, client, config, bundle = self._runner(root)

            def wait():
                deadline = time.time() + 5
                while runner.status()["status"] in {"starting", "running"} and time.time() < deadline:
                    time.sleep(0.01)
                return runner.status()

            runner.start(bundle, config)
            wait()

            runner.redo(1, "new_seed", seed=777)
            status = wait()
            pinned = graph_seeds(client.graphs[1])
            self.assertEqual({777 + offset for offset in range(len(pinned))}, set(pinned.values()))
            self.assertEqual(777, status["results"][0]["redo_seed"])


class RealWorkflowReplacementTests(unittest.TestCase):
    """The exact acceptance scenario: blocked, replaced in software, then passed."""

    @classmethod
    def setUpClass(cls):
        cls.registry = real_registry()
        set_active_registry(cls.registry)

    def test_replace_then_preflight_passes_and_seeds_are_reported(self):
        real = load_fixture_json("models_upscale_models.json")
        candidate = real[0]

        cfg = config()
        blocked = Krea2WorkflowAdapter.from_path(cfg.workflow_path, self.registry)
        blocked.build("提示词", cfg, "p")
        self.assertTrue(blocked.audit(cfg, branch="save:1")["blocking"])

        cfg.resource_overrides = {"11": {"model_name": candidate}}
        cfg.seed = 424242
        adapter = Krea2WorkflowAdapter.from_path(cfg.workflow_path, self.registry)
        graph = adapter.build("提示词", cfg, "p")
        audit = adapter.audit(cfg, branch="save:1")

        self.assertEqual([], audit["blocking"])
        self.assertEqual(candidate, graph["11"]["inputs"]["model_name"])
        seeds = graph_seeds(graph)
        self.assertTrue(seeds, "提交图必须能报出真实种子")
        self.assertIn(424242, seeds.values(), "指定种子应写入提交图")


if __name__ == "__main__":
    unittest.main()
