import pathlib
import tempfile
import unittest
import subprocess
import shutil

from comfybatch_v2_app import Application
from comfybatch_v2_core import BatchConfig, Krea2WorkflowAdapter, PromptBundleParser, PromptCompiler


class PlainCombinationTests(unittest.TestCase):
    def test_actual_api_build_has_raw_prompt_and_no_style_or_lora_nodes(self):
        graph = {
            '1': {'class_type': 'CheckpointLoaderSimple', 'inputs': {'ckpt_name': 'old'}},
            '2': {'class_type': 'PrimitiveStringMultiline', 'inputs': {'value': 'old'}},
            '3': {'class_type': 'easy stylesSelector', 'inputs': {'positive': ['2', 0], 'negative': '', 'styles': 'old', 'select_styles': 'old'}},
            '4': {'class_type': 'LoraLoader', 'inputs': {'model': ['1', 0], 'clip': ['1', 1], 'lora_name': 'old'}},
            '5': {'class_type': 'CLIPTextEncode', 'inputs': {'text': ['3', 0], 'clip': ['4', 1]}},
            '6': {'class_type': 'CLIPTextEncode', 'inputs': {'text': '', 'clip': ['4', 1]}},
            '7': {'class_type': 'EmptyLatentImage', 'inputs': {'width': 512, 'height': 512, 'batch_size': 1}},
            '8': {'class_type': 'KSampler', 'inputs': {'model': ['4', 0], 'positive': ['5', 0], 'negative': ['6', 0], 'latent_image': ['7', 0]}},
            '9': {'class_type': 'VAEDecode', 'inputs': {'samples': ['8', 0], 'vae': ['1', 2]}},
            '10': {'class_type': 'SaveImage', 'inputs': {'images': ['9', 0], 'filename_prefix': 'old'}},
        }
        config = BatchConfig('flow.json', 'model', style_application='none', single_subject_guard=False)
        result = Krea2WorkflowAdapter(graph).build('a cat', config, 'test')
        self.assertEqual('a cat', result['2']['inputs']['value'])
        self.assertNotIn('3', result)
        self.assertNotIn('4', result)
        self.assertEqual(['1', 0], result['8']['inputs']['model'])

    @unittest.skipUnless(shutil.which('node'), 'Node required for page behavior')
    def test_page_submits_plain_combination_even_with_selected_dropdown_style(self):
        root = pathlib.Path(__file__).resolve().parent.parent
        subprocess.run(['node', str(root / 'tests/plain_combination_behavior.js'), str(root / 'src/app.js')], check=True, capture_output=True)

    def test_plain_mode_ignores_stale_style_templates_and_negative(self):
        config = BatchConfig('flow.json', 'model', style_application='none', single_subject_guard=False,
                             styles=[{'prompt': '{prompt}, old-style', 'negative_prompt': 'old-negative'}])
        self.assertEqual('a cat', PromptCompiler.compile('a cat', config))
        self.assertNotIn('old-negative', PromptCompiler.compile_negative('', config))

    def test_empty_preset_persists_and_explicitly_overrides_global_style(self):
        with tempfile.TemporaryDirectory() as temp:
            path = pathlib.Path(temp) / 'settings.json'
            app = Application(settings_path=path)
            preset = app.save_style_lora_preset({'name': 'Plain', 'styles': [], 'loras': []})
            self.assertEqual([], Application(settings_path=path).style_lora_presets()[preset['id']]['styles'])
            app.bundle = PromptBundleParser.parse('prompts.txt', b'a cat')
            app.assign_style_lora_preset(preset['id'], [1])
            generation = app.resolve_bundle_presets().items[0].metadata['generation']
            self.assertEqual('none', generation['style_application'])
            self.assertEqual('', generation['style_name'])
            self.assertEqual([], generation['loras'])

    def test_plain_graph_bypasses_author_style_and_lora_without_dangling_links(self):
        graph = {
            '1': {'class_type': 'PrimitiveStringMultiline', 'inputs': {'value': 'raw'}},
            '2': {'class_type': 'easy stylesSelector', 'inputs': {'positive': ['1', 0]}},
            '3': {'class_type': 'CheckpointLoaderSimple', 'inputs': {}},
            '4': {'class_type': 'LoraLoader', 'inputs': {'model': ['3', 0], 'clip': ['3', 1], 'lora_name': 'old'}},
            '5': {'class_type': 'CLIPTextEncode', 'inputs': {'text': ['2', 0], 'clip': ['4', 1]}},
            '6': {'class_type': 'KSampler', 'inputs': {'model': ['4', 0]}},
        }
        Krea2WorkflowAdapter._bypass_plain_resources(graph)
        self.assertEqual(['1', 0], graph['5']['inputs']['text'])
        self.assertEqual(['3', 1], graph['5']['inputs']['clip'])
        self.assertEqual(['3', 0], graph['6']['inputs']['model'])
        self.assertNotIn('2', graph)
        self.assertNotIn('4', graph)
        Krea2WorkflowAdapter._validate_graph_links(graph)
