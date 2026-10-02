import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import test_workflow as base

tools = sys.modules['soda_test.tag_tools']
node = sys.modules['soda_test.tag_nodes']
u = sys.modules['soda_test.unified']
target = sys.modules['soda_test.target_prompt']


class TagToolsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.patch = patch.object(tools, 'user_root', return_value=Path(self.temp.name))
        self.patch.start(); self.addCleanup(self.patch.stop)

    def run_node(self, prompt, source, **kwargs):
        return node.SodaTagOrganizer().run(prompt, source_record=json.dumps(source), **kwargs)

    def gallery(self, tags):
        text = ', '.join(tags)
        return text, {'stage': 'material_original', 'faithful_prompt': text,
            'source_material': {'gallery': {'selected_tags': tags, 'raw_tags': tags,
                'category_hints': {'named_artist': 'artist', 'named_character': 'character'}}}}

    def test_empty_input_has_every_output(self):
        result = self.run_node('', {})
        self.assertEqual(len(result['result']), len(node.SodaTagOrganizer.RETURN_TYPES))
        self.assertEqual(result['result'][0], '')

    def test_prose_is_never_split_as_tags(self):
        text = 'A girl stands outside, wearing a blue coat.'
        result = self.run_node(text, {'faithful_prompt': text})
        self.assertEqual(result['result'][0], text)
        self.assertFalse(result['ui']['tag_preview'][0]['available'])

    def test_unknown_retained_and_website_hints_win(self):
        text, source = self.gallery(['1girl', 'silver_hair', 'blue_dress', 'named_artist', 'named_character', 'novel_tag'])
        result = self.run_node(text, source)
        self.assertEqual(result['result'][0], text)
        groups = result['ui']['tag_preview'][0]['groups']
        self.assertEqual(groups['artist'], ['named_artist'])
        self.assertEqual(groups['unknown'], ['novel_tag'])
        self.assertEqual(groups['clothing'], ['blue_dress'])
        self.assertEqual(groups['appearance'], ['silver_hair'])

    def test_remove_artist_and_edit_clothes_keeps_original(self):
        text, source = self.gallery(['1girl', 'blue_dress', 'named_artist', 'novel_tag'])
        keep = [c for c in tools.CATEGORIES if c != 'artist']
        result = self.run_node(text, source, include_categories=json.dumps(keep), category_edits='{"clothing":"red coat"}')
        self.assertEqual(result['result'][0], '1girl, novel_tag, red_coat')
        value = json.loads(result['result'][1])
        self.assertEqual(value['tag_organisation']['excluded_tags'], ['blue_dress', 'named_artist'])
        self.assertEqual(result['result'][2], text)
        self.assertEqual(value['api_calls'], 0)

    def test_changed_tags_remove_contradictory_caption(self):
        prompt = '1girl, blue dress\n\nA girl wears a blue dress. She stands outside.'
        result = self.run_node(prompt, {'tags': ['1girl', 'blue dress'], 'faithful_prompt': prompt}, category_edits='{"clothing":"red coat"}')
        self.assertEqual(result['result'][0], '1girl, red_coat')
        self.assertTrue(json.loads(result['result'][1])['validation']['warnings'])

    def test_unchanged_tags_preserve_caption_exactly(self):
        prompt = '1girl, blue dress\n\nA girl wears a blue dress. She stands outside.'
        result = self.run_node(prompt, {'tags': ['1girl', 'blue dress'], 'faithful_prompt': prompt})
        self.assertEqual(result['result'][0], prompt)

    def test_manual_prompt_does_not_reuse_stale_tags(self):
        result = self.run_node('A new tree.', {'tags': ['1girl'], 'faithful_prompt': '1girl'})
        self.assertEqual(result['result'][0], 'A new tree.')
        self.assertFalse(result['ui']['tag_preview'][0]['available'])

    def test_mapping_persists_and_invalidates_node_cache(self):
        before = tools.fingerprint()
        tools.save_settings(mappings={'novel tag': 'clothing'})
        self.assertNotEqual(before, node.SodaTagOrganizer.IS_CHANGED())
        self.assertEqual(tools.classify(['novel_tag'])['clothing'], ['novel_tag'])
        tools.save_settings(mappings={'novel_tag': None})
        self.assertEqual(tools.classify(['novel_tag'])['unknown'], ['novel_tag'])

    def test_invalid_category_cannot_corrupt_mapping(self):
        with self.assertRaises(ValueError): tools.save_settings(mappings={'tag': 'invented'})
        self.assertEqual(tools.settings()['mappings'], {})

    def test_empty_category_selection_cannot_silently_erase_prompt(self):
        text, source = self.gallery(['1girl'])
        with self.assertRaises(ValueError): self.run_node(text, source, include_categories='[]')

    def test_disabled_node_preserves_prompt_and_record(self):
        text, source = self.gallery(['1girl'])
        result = self.run_node(text, source, enabled=False, category_edits='{"count":"2girls"}')
        self.assertEqual(result['result'][0], text)
        self.assertEqual(json.loads(result['result'][1]), source)

    def test_target_profiles_receive_edits_through_nested_stages(self):
        source = {'source_record': {'tag_organisation': {'excluded_tags': ['blue_dress'], 'added_tags': ['red_coat']}}}
        for model in ('Anima', 'Krea2', 'Qwen2.1'):
            payload = json.loads(target.messages(model, '1girl, red_coat', source, 512)[1]['content'])
            self.assertEqual(payload['user_tag_changes']['excluded_tags'], ['blue_dress'])
        source['replace_oc'] = True
        self.assertEqual(tools.tag_intent(source), {})

    def test_old_unconnected_gallery_record_does_not_request_new_input(self):
        self.assertEqual(u.SodaUnifiedSource().check_lazy_status(u.GALLERY, gallery_image='image', gallery_text='tags'), [])
        self.assertEqual(u.SodaUnifiedSource().check_lazy_status(u.GALLERY, gallery_image='image', gallery_text='tags', gallery_record=None), ['gallery_record'])

    def test_cached_metadata_never_infers_artist_from_general_tag(self):
        with patch.object(tools, 'catalog', return_value={'odd_name': 'general'}):
            self.assertEqual(tools.classify(['odd_name'])['unknown'], ['odd_name'])

    def test_failed_gallery_download_does_not_attach_wrong_picture_metadata(self):
        import types
        gallery = types.SimpleNamespace(get_selected_data=lambda **kwargs: (['image'], ['second_prompt']))
        fake = types.SimpleNamespace(NODE_CLASS_MAPPINGS={'DanbooruGalleryNode':lambda:gallery})
        with patch.dict(sys.modules, {'nodes':fake}), patch.object(tools,'gallery_record',return_value={'gallery':{}}) as metadata:
            u.SodaGallerySource().run(json.dumps({'selections':[
                {'post_id':'1','source_site':'danbooru'}, {'post_id':'2','source_site':'danbooru'}]}))
            metadata.assert_called_once_with('danbooru','','second_prompt')

    def test_semantic_search_missing_resources_does_not_load_model(self):
        from soda_test.tag_search import search
        with self.assertRaisesRegex(ValueError, '资源目录'): search('蓝色头发')


class TagStageTests(unittest.IsolatedAsyncioTestCase):
    async def test_normalised_tags_survive_prefix_without_losing_caption(self):
        stages=sys.modules['soda_test.prompt_stages']
        prompt='1girl, blue dress\n\nA girl wears a blue dress. She stands outside.'
        with tempfile.TemporaryDirectory() as temp, patch.object(tools,'user_root',return_value=Path(temp)):
            value=node.SodaTagOrganizer().run(prompt,source_record=json.dumps({'tags':['1girl','blue dress'],'faithful_prompt':prompt}))
            result=await stages.SodaPromptStages().run(prompt,'soft lighting','',False,'','',0,180,source_record=value['result'][1])
        record=json.loads(result[1])
        self.assertEqual(record['tags'],['1girl','blue_dress'])
        self.assertIn('She stands outside.',record['prompt_parts']['nl'])

    async def test_anima_cannot_restore_explicitly_removed_tag(self):
        import test_target_prompt as fixtures
        implementation=sys.modules['soda_test.suite_nodes']
        response=fixtures.reply('Anima')
        source={'selected_prompt':'1girl, red_coat','tag_organisation':{'excluded_tags':['white_dress'],'added_tags':['red_coat']}}
        with patch.object(implementation,'call',AsyncMock(return_value=(response,fixtures.API))):
            with self.assertRaisesRegex(ValueError,'补回了已舍弃'):
                await implementation.SodaPromptOutput().run('1girl, red_coat',False,'','Anima',source_record=json.dumps(source))


if __name__ == '__main__': unittest.main()
