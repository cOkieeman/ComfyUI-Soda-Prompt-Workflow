import inspect
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import test_suite as fixtures
import test_workflow as base

nodes = fixtures.nodes
API = fixtures.API
SOURCE = '一位蓝发蓝眼女孩站在浅蓝背景前，穿白色连衣裙。人物位于画面中央。'
ENGLISH = 'An anime girl with blue hair and blue eyes stands at the center of the composition, wearing a white dress. Soft light separates her silhouette from the pale blue background.'
CHINESE = '一名蓝发蓝眼的动漫女孩站在画面中央，身穿白色连衣裙。背景为浅蓝色，柔和的光线勾勒出人物轮廓，构图保持简洁。'


def reply(target):
    result = {'omissions': ['无法确认角色身份，未加入角色名。'], 'warnings': []}
    if target == 'Anima':
        slots = {name: [] for name in fixtures.local.SLOTS}
        slots.update(rating=['safe'], count=['1girl', 'solo'], appearance=['blue_hair', 'blue eyes'],
                     clothing_props=['white dress'], pose_expression=['standing'], scene=['blue background'])
        result.update(slots=slots, nl='The girl stands at the center of the composition. Soft light outlines her silhouette against the pale blue background.')
    else:
        result['prompt'] = ENGLISH if target == 'Krea2' else CHINESE
    return result


async def invoke(*args, **kwargs):
    # Also reproduces the previous synchronous implementation's pass-through bug.
    result = nodes.SodaPromptOutput().run(*args, **kwargs)
    return await result if inspect.isawaitable(result) else result


class TargetPromptTests(unittest.IsolatedAsyncioTestCase):
    def test_three_targets_are_available(self):
        options = nodes.SodaPromptOutput.INPUT_TYPES()['required']['target'][0]
        self.assertEqual(options, ['Anima', 'Krea2', 'Qwen2.1'])

    async def test_selected_target_actually_converts_source(self):
        for target in ('Krea2', 'Anima', 'Qwen2.1'):
            with self.subTest(target=target), patch.object(nodes, 'call', AsyncMock(return_value=(reply(target), API))) as call:
                text, raw = await invoke(SOURCE, False, '', target)
                self.assertNotEqual(text, SOURCE)
                call.assert_awaited_once()
                self.assertEqual(call.await_args.args[0], 'target_prompt_v1:' + target)
                record = json.loads(raw)
                self.assertEqual(record['target'], target)
                self.assertTrue(record['adapted'])
                self.assertEqual(record['source_prompt'], SOURCE)
                self.assertTrue(record['validation']['passed'])
                self.assertNotIn('无法确认', text)
                if target == 'Anima':
                    self.assertTrue(text.startswith('safe, 1girl, solo,'))
                    self.assertIn('blue hair', text)
                    self.assertLessEqual(record['validation']['tokens'], 512)
                else:
                    self.assertEqual(text, reply(target)['prompt'])

    async def test_manual_input_does_not_require_upstream_execution(self):
        schema = nodes.SodaPromptOutput.INPUT_TYPES()
        self.assertTrue(schema['required']['source_prompt'][1]['lazy'])
        self.assertEqual(nodes.SodaPromptOutput().check_lazy_status(True), [])
        with patch.object(nodes, 'call', AsyncMock(return_value=(reply('Krea2'), API))) as call:
            text, raw = await invoke(None, True, SOURCE, 'Krea2')
        self.assertEqual(text, ENGLISH)
        self.assertEqual(json.loads(raw)['adaptation_input'], SOURCE)
        call.assert_awaited_once()

    async def test_disabled_adapter_preserves_text_without_call(self):
        with patch.object(nodes, 'call', AsyncMock()) as call:
            text, raw = await invoke(SOURCE, False, '', 'Krea2', adapt_to_target=False)
        self.assertEqual(text, SOURCE)
        self.assertFalse(json.loads(raw)['adapted'])
        call.assert_not_awaited()

    async def test_adapted_record_for_other_target_is_not_reused(self):
        source = {'selected_prompt': ENGLISH, 'stage': 'prompt_output', 'target': 'Krea2', 'adapted': True}
        with patch.object(nodes, 'call', AsyncMock(return_value=(reply('Anima'), API))) as call:
            text, raw = await invoke(ENGLISH, False, '', 'Anima', json.dumps(source))
        call.assert_awaited_once()
        self.assertTrue(text.startswith('safe,'))

    async def test_same_final_target_record_needs_no_second_adaptation(self):
        source = {'selected_prompt': ENGLISH, 'stage': 'prompt_output', 'target': 'Krea2',
                  'adapted': True, 'target_profile_version': 1, 'validation': {'passed': True, 'problems': []}}
        with patch.object(nodes, 'call', AsyncMock()) as call:
            text, raw = await invoke(ENGLISH, False, '', 'Krea2', json.dumps(source))
        self.assertEqual(text, ENGLISH)
        self.assertTrue(json.loads(raw)['adapted'])
        call.assert_not_awaited()

    async def test_cache_is_per_target_and_refresh(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(nodes, 'user_root', return_value=Path(directory)), \
                patch.object(nodes, 'key_path', return_value=Path(directory) / 'unused.toml'), \
                patch.object(fixtures.suite.core, 'read_key', return_value='TEST-NOT-A-KEY'), \
                patch.object(fixtures.suite.core, 'chat', AsyncMock(side_effect=[(reply('Krea2'), API), (reply('Qwen2.1'), API), (reply('Krea2'), API)])) as transport:
            first = await invoke(SOURCE, False, '', 'Krea2')
            second = await invoke(SOURCE, False, '', 'Krea2')
            await invoke(SOURCE, False, '', 'Qwen2.1')
            await invoke(SOURCE, False, '', 'Krea2', refresh=1)
        self.assertFalse(json.loads(first[1])['api']['cache_hit'])
        self.assertTrue(json.loads(second[1])['api']['cache_hit'])
        self.assertEqual(transport.await_count, 3)

    async def test_bad_transport_format_fails_without_auto_retry(self):
        for bad in ({'prompt': SOURCE, 'omissions': [], 'warnings': []},
                    {'prompt': '# Analysis\n' + ENGLISH, 'omissions': [], 'warnings': []},
                    {'prompt': ENGLISH, 'omissions': 'bad', 'warnings': []}):
            with self.subTest(bad=bad), patch.object(nodes, 'call', AsyncMock(return_value=(bad, API))) as call:
                with self.assertRaises(ValueError):
                    await invoke(SOURCE, False, '', 'Krea2')
                call.assert_awaited_once()

    async def test_ambiguous_caption_is_flagged_without_discarding_executable_text(self):
        response = {'prompt': 'The girl holds a cup or a bowl. Soft blue light fills the background.', 'omissions': [], 'warnings': []}
        with patch.object(nodes, 'call', AsyncMock(return_value=(response, API))) as call:
            text, raw = await invoke(SOURCE, False, '', 'Krea2')
        self.assertEqual(text, response['prompt'])
        self.assertTrue(any('含糊描述' in warning for warning in json.loads(raw)['validation']['warnings']))
        call.assert_awaited_once()

    async def test_manual_passthrough_never_calls_and_keeps_original(self):
        source = {'faithful_prompt': 'original', 'selected_prompt': 'expanded', 'expanded_prompt': 'expanded'}
        with patch.object(nodes, 'call', AsyncMock()) as call:
            text, raw = await invoke('expanded', True, ENGLISH, 'Krea2', json.dumps(source), adapt_to_target=False)
        self.assertEqual(text, ENGLISH)
        self.assertEqual(json.loads(raw)['faithful_prompt'], 'original')
        call.assert_not_awaited()
