import asyncio
import importlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch

import test_workflow as base

vlm = importlib.import_module('soda_test.local_vlm')
suite = sys.modules['soda_test.suite']
reference = sys.modules['soda_test.suite_nodes']
material = sys.modules['soda_test.material_nodes']
worker = importlib.import_module('soda_test.vlm_worker')
RESPONSE = {'tags': ['1girl', 'blue_hair'], 'nl': 'A woman reads a book. Soft light falls on the page.', 'uncertainties': []}


class LocalVisionTests(unittest.IsolatedAsyncioTestCase):
    setUp = base.WorkflowTests.setUp
    def setup_config(self):
        directory = Path(self.temp.name)
        model, projector = directory/'model.gguf', directory/'mmproj.gguf'
        model.write_bytes(b'GGUFmodel'); projector.write_bytes(b'GGUFprojector')
        return vlm.save_config({'model_path': str(model), 'mmproj_path': str(projector)})

    def result(self):
        return {'text': json.dumps(RESPONSE), 'device': 'cpu', 'model': 'model.gguf', 'mmproj': 'mmproj.gguf',
                'runtime_version': 'test', 'usage': {}, 'elapsed_seconds': 1, 'warnings': []}

    def test_invalid_model_and_parameters_fail_before_save(self):
        for update in [{'device': 'gpu'}, {'max_tokens': True}, {'context_size': 2048, 'max_tokens': 2048}, {'unexpected': 1}]:
            with self.assertRaises(ValueError): vlm.save_config(update)
        self.assertFalse((Path(self.temp.name)/'soda_prompt_workflow/local_vlm.json').exists())
        invalid = Path(self.temp.name)/'bad.gguf'; invalid.write_bytes(b'HTML')
        with self.assertRaises(ValueError): vlm.gguf_file(str(invalid), 'model')

    def test_config_changes_and_model_file_changes_invalidate_node(self):
        config = self.setup_config()
        before = reference.SodaReferenceSuite.IS_CHANGED(route=vlm.ROUTE)
        vlm.save_config({'seed': 10})
        self.assertNotEqual(before, reference.SodaReferenceSuite.IS_CHANGED(route=vlm.ROUTE))
        before = vlm.identity()
        Path(config['model_path']).write_bytes(b'GGUFchanged')
        self.assertNotEqual(before, vlm.identity())

    def test_route_preserves_old_options_and_widget_slots(self):
        required = material.SodaMaterialPrompt.INPUT_TYPES()['required']
        self.assertEqual(required['route'][0], [suite.PIXEL, suite.ANIMA, suite.DFLOW, vlm.ROUTE])
        self.assertEqual(list(required), ['action', 'route', 'user_prompt', 'use_card_instruction', 'refresh', 'timeout_seconds'])

    def test_schema_limits_reject_report_or_truncated_shapes(self):
        for value in [RESPONSE | {'tags': []}, RESPONSE | {'tags': ['x']*33}, RESPONSE | {'nl': '<think>thought'},
                      RESPONSE | {'uncertainties': ''}, RESPONSE | {'nl': 'x'*701}, RESPONSE | {'tags': ['a,b']}]:
            with self.assertRaises(ValueError): vlm.validate_response(value)
        self.assertEqual(worker.SCHEMA['properties']['tags']['maxItems'], 32)
        self.assertFalse(worker.SCHEMA['additionalProperties'])

    async def test_actual_transport_cached_without_image_in_saved_record(self):
        self.setup_config()
        run = AsyncMock(return_value=self.result())
        with patch.object(vlm, 'execute_worker', run):
            response, first = await vlm.observe('data:image/png;base64,fixture', 'blue hair', 0, 10)
            _, second = await vlm.observe('data:image/png;base64,fixture', 'blue hair', 0, 10)
        self.assertEqual(response, RESPONSE); self.assertFalse(first['cache_hit']); self.assertTrue(second['cache_hit'])
        run.assert_awaited_once()
        self.assertEqual(run.call_args.kwargs['worker_path'].name, 'vlm_worker.py')
        cache = list((Path(self.temp.name)/'soda_prompt_workflow/cache-local-vlm').glob('*.json'))
        self.assertNotIn('base64', cache[0].read_text())

    async def test_failures_and_cancellation_leave_no_cache_or_pending_marker(self):
        self.setup_config()
        for failure in [RuntimeError('fixture failure'), asyncio.CancelledError()]:
            with patch.object(vlm, 'execute_worker', AsyncMock(side_effect=failure)):
                with self.assertRaises(type(failure)):
                    await vlm.observe('data:image/png;base64,fixture', '', 0, 10)
            self.assertEqual(list((Path(self.temp.name)/'soda_prompt_workflow/cache-local-vlm').glob('*')), [])

    async def test_rejects_urls_and_missing_config_without_worker_or_remote_calls(self):
        run = AsyncMock()
        with patch.object(vlm, 'execute_worker', run), patch.object(reference, 'call', AsyncMock()) as remote:
            for image in ['https://example.com/image.png', 'data:image/png;base64,fixture']:
                with self.assertRaises(ValueError): await vlm.observe(image, '', 0, 10)
        run.assert_not_called(); remote.assert_not_called()

    async def test_local_reference_preserves_parts_and_explicit_zero_api_calls(self):
        import numpy as np
        image = [base.Tensor(np.zeros((4, 4, 3), dtype=np.float32))]
        with patch.object(vlm, 'observe', AsyncMock(return_value=(RESPONSE, {'warnings': [], 'device': 'cpu'}))), \
             patch.object(reference, 'call', AsyncMock()) as remote:
            prompt, raw = await reference.SodaReferenceSuite().run(image, vlm.ROUTE, 0, 100, 0, 10, 'blue hair')
        record = json.loads(raw)
        self.assertEqual(record['api_calls'], 0); self.assertEqual(record['prompt_parts']['text'], prompt)
        self.assertEqual(record['prompt_parts']['tags'], '1girl, blue_hair')
        self.assertEqual(record['observation']['description'], RESPONSE['nl']); remote.assert_not_called()

    async def test_two_inputs_do_not_load_two_models_concurrently(self):
        self.setup_config(); active = 0; maximum = 0
        async def run(*args, **kwargs):
            nonlocal active, maximum
            active += 1; maximum = max(maximum, active)
            await asyncio.sleep(.02)
            active -= 1
            return self.result()
        with patch.object(vlm, 'execute_worker', run):
            await asyncio.gather(*(vlm.observe('data:image/png;base64,' + str(i), '', 0, 10) for i in range(2)))
        self.assertEqual(maximum, 1)

    async def test_local_parts_flow_through_tag_stage_and_each_target_adapter(self):
        import test_target_prompt as fixture
        tag_nodes = sys.modules['soda_test.tag_nodes']
        source = {'stage': 'local_qwen35_reverse', 'selected_prompt': '1girl, blue_hair\n\n'+RESPONSE['nl'],
                  'tags': RESPONSE['tags'], 'prompt_parts': {'tags':'1girl, blue_hair', 'nl':RESPONSE['nl']},
                  'validation': {'passed': True, 'warnings': ['Local visual candidates require review.']}}
        source['prompt_parts']['text'] = source['selected_prompt']
        result = tag_nodes.SodaTagOrganizer().run(source['selected_prompt'], source_record=json.dumps(source))['result']
        self.assertEqual(json.loads(result[1])['validation']['warnings'], source['validation']['warnings'])
        for target in ('Anima', 'Krea2', 'Qwen2.1'):
            with patch.object(reference, 'call', AsyncMock(return_value=(fixture.reply(target), fixture.API))) as remote:
                text, raw = await reference.SodaPromptOutput().run(result[0], False, '', target, result[1])
            self.assertTrue(text); self.assertTrue(json.loads(raw)['adapted'])
            self.assertEqual(json.loads(raw)['target'], target)
            remote.assert_awaited_once()

    def test_local_template_preserves_connections_and_has_no_local_paths(self):
        path = base.PACKAGE/'workflows/Soda-Prompt-Workbench-本地Qwen反推版.json'
        graph = json.loads(path.read_text(encoding='utf-8'))
        nodes = {n['id']:n for n in graph['nodes']}
        for identifier, source, slot, target, input_slot, kind in graph['links']:
            self.assertIn(identifier, nodes[source]['outputs'][slot]['links'])
            self.assertEqual(nodes[target]['inputs'][input_slot]['link'], identifier)
        self.assertEqual(nodes[2]['widgets_values'][1], vlm.ROUTE)
        self.assertEqual(nodes[2]['widgets_values'][5], 600)
        self.assertEqual(nodes[1]['widgets_values'][1], '')
        self.assertNotIn('model_path', path.read_text(encoding='utf-8'))
