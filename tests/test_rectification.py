import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from PIL import Image, PngImagePlugin

import test_workflow as base

u = sys.modules['soda_test.unified']
stages = sys.modules['soda_test.prompt_stages']
tipo = sys.modules['soda_test.tipo_expansion']


class RectificationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        base.HOST.get_user_directory = lambda: str(self.root)

    def image(self, suffix='.png', graph=None, parameters=None):
        path = self.root / ('fixture' + suffix)
        metadata = PngImagePlugin.PngInfo()
        if graph is not None:
            metadata.add_text('prompt', json.dumps(graph))
        if parameters is not None:
            metadata.add_text('parameters', parameters)
        Image.new('RGB', (64, 64), 'red').save(path, pnginfo=metadata)
        return path

    async def test_plain_images_can_reach_reverse(self):
        for suffix in ('.jpg', '.png'):
            source = await u.SodaUnifiedSource().run(u.LOCAL, str(self.image(suffix)), '', 4173, '')
            self.assertEqual(source[1], '')
            self.assertEqual(tuple(source[0].shape), (1, 64, 64, 3))
            with patch.object(u.SodaMaterialPrompt, 'run', AsyncMock(return_value=('caption', '{}'))) as reverse:
                result = await u.SodaUnifiedProcess().run('重新反推', u.suite.PIXEL, '', True, 0, 180,
                    source_image=source[0], source_text=source[1], source_record=source[2])
                reverse.assert_awaited_once()
                self.assertEqual(result[0], 'caption')

    async def test_empty_original_still_requires_explicit_reverse(self):
        source = await u.SodaUnifiedSource().run(u.LOCAL, str(self.image()), '', 4173, '')
        with patch.object(sys.modules['soda_test.material_nodes'].SodaReferenceSuite, 'run', AsyncMock()) as paid:
            with self.assertRaisesRegex(ValueError, '不会自动'):
                await u.SodaUnifiedProcess().run('使用素材原提示词', u.suite.PIXEL, '', True, 0, 180,
                    source_image=source[0], source_text=source[1], source_record=source[2])
            paid.assert_not_awaited()

    async def test_sampler_separates_positive_and_negative(self):
        graph = {'3': {'class_type': 'CLIPTextEncode', 'inputs': {'text': 'A red flower.'}},
                 '4': {'class_type': 'CLIPTextEncode', 'inputs': {'text': 'blur, bad anatomy'}},
                 '5': {'class_type': 'KSampler', 'inputs': {'positive': ['3', 0], 'negative': ['4', 0]}}}
        source = await u.SodaUnifiedSource().run(u.LOCAL, str(self.image(graph=graph)), '', 4173, '')
        self.assertEqual(source[1], 'A red flower.')
        self.assertEqual(source[3], 'blur, bad anatomy')

    async def test_ambiguous_encoders_are_inventory_not_positive(self):
        graph = {'3': {'class_type': 'CLIPTextEncode', 'inputs': {'text': 'A red flower.'}},
                 '4': {'class_type': 'CLIPTextEncode', 'inputs': {'text': 'blur'}}}
        source = await u.SodaUnifiedSource().run(u.LOCAL, str(self.image(graph=graph)), '', 4173, '')
        self.assertEqual(source[1], '')
        self.assertEqual(len(json.loads(source[2])['encoder_texts']), 2)

    async def test_parameters_keep_existing_priority(self):
        source = await u.SodaUnifiedSource().run(u.LOCAL, str(self.image(parameters=
            'A red flower.\nNegative prompt: blur\nSteps: 20')), '', 4173, '')
        self.assertEqual(source[1], 'A red flower.')
        self.assertEqual(source[3].strip(), 'blur')

    async def test_prefix_suffix_preserve_tipo_anchors(self):
        original = '1girl, solo, red hair\n\nShe reads a book.'
        raw = json.dumps({'faithful_prompt': original, 'tags': ['1girl', 'solo', 'red hair']})
        prompt, detail = await stages.SodaPromptStages().run(original, 'watercolor', 'soft light',
            False, '', '', 0, 180, raw)
        tags, natural = tipo.split_input(prompt, json.loads(detail), tipo.AUTO)
        self.assertEqual(tags, '1girl, solo, red hair')
        self.assertIn('watercolor', natural)
        self.assertIn('soft light', natural)
        self.assertIn('She reads a book.', natural)

    async def test_oc_does_not_reuse_stale_source_tags(self):
        original = '1girl, red hair\n\nShe reads a book.'
        raw = json.dumps({'faithful_prompt': original, 'tags': ['1girl', 'red hair']})
        with patch.object(stages, 'read_preset', return_value='TEST OC RULE'), patch.object(stages.suite, 'cached_chat', AsyncMock(return_value=('A blue-haired woman.', {'cache_hit': True}))):
            prompt, detail = await stages.SodaPromptStages().run(original, '', '', True, 'blue hair',
                'hair', 0, 180, raw)
        self.assertEqual(tipo.split_input(prompt, json.loads(detail), tipo.AUTO), ('', prompt))
