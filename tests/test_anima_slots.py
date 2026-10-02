import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch

import numpy as np

import test_suite as fixtures
import test_workflow as base

local = fixtures.local
nodes = fixtures.nodes
suite = fixtures.suite


class SlotRepairTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        root = patch.object(nodes, 'user_root', return_value=Path('unused-test-root'))
        root.start()
        self.addCleanup(root.stop)

    def sample(self):
        tagged = fixtures.tagged()
        response = fixtures.reverse()
        # A real response retained these five tags in decisions but omitted
        # them from both its initial slots and its subsequent repair response.
        for tag in ('cat girl', 'cleavage', 'paw print', 'signature', 'artist name'):
            tagged['general'][tag.replace(' ', '_')] = .6
            response['decisions'].append({'tag': tag, 'keep': True, 'reason': 'Visible in the image.'})
        tagged['general']['looking_at_viewer'] = .5
        response['decisions'].append({'tag': 'looking at viewer', 'keep': False, 'reason': 'Gaze is directed elsewhere.'})
        response['slots']['pose_expression'] = ['looking at viewer']
        return tagged, response

    def test_real_failure_pattern_repaired_without_changing_review(self):
        tagged, response = self.sample()
        original = copy.deepcopy(response)
        kept, scores = local.reviewed_tags(tagged, response)
        with self.assertRaisesRegex(ValueError, '新增或遗漏'):
            suite.validate_reverse(tagged, response)
        slots, audit = local.reconcile_reverse_slots(response['slots'], kept, tagged)
        report = local.validate_anima(slots, response['nl'], kept, scores)
        self.assertTrue(report['passed'], report)
        self.assertEqual(response, original)
        self.assertEqual(set(audit['restored_tags']), {'cat girl', 'cleavage', 'paw print', 'signature', 'artist name'})
        self.assertEqual(audit['removed_tags'], ['looking at viewer'])
        self.assertEqual(audit['unassigned_tags'], [])
        self.assertIn('cat girl', slots['appearance'])
        self.assertIn('cleavage', slots['appearance'])
        self.assertIn('paw print', slots['clothing_props'])
        self.assertIn('signature', slots['scene'])

    def test_normalize_deduplicate_and_restore_immutable_high_tags(self):
        tagged = fixtures.tagged()
        response = fixtures.reverse()
        response['slots']['count'] = ['1Girl', '1girl']
        response['slots']['clothing_props'].append('white_shirt')
        kept, _ = local.reviewed_tags(tagged, response)
        slots, audit = local.reconcile_reverse_slots(response['slots'], kept, tagged)
        self.assertEqual(slots['count'], ['1girl', 'solo'])
        self.assertEqual(slots['clothing_props'].count('white shirt'), 1)
        self.assertIn('solo', audit['restored_tags'])
        self.assertEqual(set(audit['deduplicated_tags']), {'1girl', 'white shirt'})

    def test_unfamiliar_missing_tag_reported_for_manual_slot_review(self):
        tagged = fixtures.tagged()
        slots, audit = local.reconcile_reverse_slots(fixtures.slots(), ['general', '1girl', 'unfamiliar tag'], tagged)
        self.assertIn('unfamiliar tag', slots['scene'])
        self.assertEqual(audit['unassigned_tags'], ['unfamiliar tag'])

    def test_malformed_slots_fail_instead_of_guessing(self):
        for broken in (None, [], {'appearance': 'hair bun'}, {'appearance': ['two, tags']}, {'made_up_slot': []}):
            with self.subTest(broken=broken), self.assertRaises(ValueError):
                local.reconcile_reverse_slots(broken, ['hair bun'], fixtures.tagged())

    async def test_node_uses_one_response_and_preserves_audit(self):
        tagged, response = self.sample()
        call = AsyncMock(return_value=(response, fixtures.API))
        with patch.object(local, 'tag_image', return_value=tagged), patch.object(nodes, 'call', call):
            output = await nodes.SodaReferenceSuite().run(
                [base.Tensor(np.zeros((8, 8, 3), dtype=np.float32))], suite.ANIMA, 0, 1600, 0, 180)
        call.assert_awaited_once()
        record = json.loads(output[1])
        self.assertEqual(record['original_review'], response)
        self.assertEqual(record['repairs'][0]['kind'], 'local_slot_reconciliation')
        self.assertTrue(record['validation']['passed'])
        self.assertIn('cat girl', record['tags'])
        self.assertNotIn('looking at viewer', record['tags'])

    async def test_invalid_visual_decisions_still_rejected(self):
        tagged, response = self.sample()
        response['decisions'].pop()
        with patch.object(local, 'tag_image', return_value=tagged), patch.object(nodes, 'call', AsyncMock(return_value=(response, fixtures.API))) as call:
            with self.assertRaisesRegex(ValueError, '全部低分'):
                await nodes.SodaReferenceSuite().run(
                    [base.Tensor(np.zeros((8, 8, 3), dtype=np.float32))], suite.ANIMA, 0, 1600, 0, 180)
        call.assert_awaited_once()

    async def test_unknown_slot_is_not_silently_marked_passed(self):
        tagged, response = self.sample()
        tagged['general']['unfamiliar_tag'] = .6
        response['decisions'].append({'tag': 'unfamiliar tag', 'keep': True, 'reason': 'Visible.'})
        with patch.object(local, 'tag_image', return_value=tagged), patch.object(nodes, 'call', AsyncMock(return_value=(response, fixtures.API))) as call:
            output = await nodes.SodaReferenceSuite().run(
                [base.Tensor(np.zeros((8, 8, 3), dtype=np.float32))], suite.ANIMA, 0, 1600, 0, 180)
        record = json.loads(output[1])
        self.assertFalse(record['validation']['passed'])
        self.assertIn('unfamiliar tag', record['tags'])
        self.assertTrue(any('人工确认' in problem for problem in record['validation']['problems']))
        call.assert_awaited_once()
