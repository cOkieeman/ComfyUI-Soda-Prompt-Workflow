import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import test_workflow as base
from soda_test import providers, suite


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.key_path = Path(self.temp.name) / 'secrets.toml'

    def configure(self, name='glm', **updates):
        return providers.save(self.key_path, {'active': name, 'profiles': {name: {
            'text_model': 'test-text', 'vision_model': 'test-vision', 'api_key': 'test-secret-marker', **updates}}})

    def test_defaults_preserve_flash_and_legacy_cache_identity(self):
        self.assertEqual(providers.resolve(self.key_path), providers.default_service())
        self.assertEqual(providers.fingerprint(self.key_path), providers.default_fingerprint())
        self.assertEqual(providers.resolve(self.key_path, True)['model'], 'deepseek-flash')

    def test_saved_profiles_keep_keys_private_and_blank_keeps_existing_key(self):
        data = self.configure()
        self.assertNotIn('test-secret-marker', json.dumps(data))
        self.assertTrue(data['profiles']['glm']['key_configured'])
        self.assertEqual(providers.read_key(self.key_path, providers.resolve(self.key_path)), 'test-secret-marker')
        data['profiles']['glm']['api_key'] = ''
        providers.save(self.key_path, data)
        self.assertEqual(providers.read_key(self.key_path, providers.resolve(self.key_path)), 'test-secret-marker')
        self.assertNotIn('api_key', providers.resolve(self.key_path))

    def test_custom_service_cannot_reuse_deepseek_key(self):
        self.configure('deepseek', base_url='https://example.com/v1', api_key='')
        with patch.object(base.core, 'read_key', side_effect=AssertionError('Must not use the official key')):
            with self.assertRaisesRegex(RuntimeError, '尚未配置'):
                providers.read_key(self.key_path, providers.resolve(self.key_path))

    def test_changing_endpoint_does_not_forward_saved_key_to_new_service(self):
        self.configure()
        data = providers.public_config(self.key_path)
        data['profiles']['glm']['base_url'] = 'https://different.example.com/v1'
        providers.save(self.key_path, data)
        with self.assertRaisesRegex(RuntimeError, '尚未配置'):
            providers.read_key(self.key_path, providers.resolve(self.key_path))

    def test_urls_models_and_key_format_are_validated_without_echoing_secrets(self):
        for url in ('https://user:secret-marker@example.com', 'https://example.com?key=secret-marker',
                    'http://remote.example.com', 'file:///secret-marker'):
            with self.assertRaises(ValueError) as error:
                providers.endpoint(url)
            self.assertNotIn('secret-marker', str(error.exception))
        self.assertEqual(providers.endpoint('http://127.0.0.1:1234/v1/'), 'http://127.0.0.1:1234/v1/chat/completions')
        self.assertEqual(providers.endpoint('https://example.com/v1/chat/completions'), 'https://example.com/v1/chat/completions')
        with self.assertRaises(ValueError):
            self.configure(api_key='secret-marker\ninvalid')
        self.assertFalse(providers.config_path(self.key_path).exists())

    def test_missing_vision_model_fails_before_reading_key(self):
        self.configure(vision_model='')
        self.assertEqual(providers.resolve(self.key_path)['model'], 'test-text')
        with self.assertRaisesRegex(ValueError, '支持图片输入'):
            providers.resolve(self.key_path, True)

    async def test_provider_endpoint_and_models_partition_cache_without_keys(self):
        self.configure()
        directory = Path(self.temp.name) / 'cache'
        async def response(key, messages, timeout, max_tokens, *, service):
            return {'text': service['model']}, {'response_model': service['model']}
        with patch.object(base.core, 'chat', AsyncMock(side_effect=response)) as transport:
            args = (directory, self.key_path, 'stage', [{'role': 'user', 'content': 'test'}], 0, 10, 20)
            a = await suite.cached_chat(*args)
            b = await suite.cached_chat(*args)
            self.assertTrue(b[1]['cache_hit'])
            self.configure(text_model='test-new-model')
            c = await suite.cached_chat(*args)
            self.assertNotEqual(a[1]['cache_id'], c[1]['cache_id'])
            self.configure(base_url='https://different.example.com/v1', text_model='test-new-model')
            d = await suite.cached_chat(*args)
            self.assertNotEqual(c[1]['cache_id'], d[1]['cache_id'])
            self.assertEqual(transport.await_count, 3)
        for path in directory.glob('*'):
            self.assertNotIn('test-secret-marker', path.read_text(encoding='utf-8'))

    async def test_vision_requests_select_vision_model_and_aliases_can_be_explicitly_accepted(self):
        self.configure('gemini')
        messages = [{'role': 'user', 'content': [{'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,test'}}]}]
        with patch.object(base.core, 'chat', AsyncMock(return_value=({'text': 'ok'}, {'response_model': 'test-vision-2026'}))) as api:
            _, meta = await suite.cached_chat(Path(self.temp.name) / 'cache', self.key_path, 'stage', messages, 0, 10, 10)
            self.assertEqual(api.call_args.kwargs['service']['model'], 'test-vision')
            self.assertIn('model_warning', meta)
        self.configure('gemini', strict_model=True)
        with patch.object(base.core, 'chat', AsyncMock(return_value=({}, {'response_model': 'other'}))):
            with self.assertRaisesRegex(RuntimeError, '不是 test-text'):
                await suite.cached_chat(Path(self.temp.name) / 'cache', self.key_path, 'stage', [], 1, 10, 10)

    async def test_compatible_transport_strips_vendor_only_arguments_and_keeps_original_image_message(self):
        self.configure('gemini')
        service = providers.resolve(self.key_path, True)
        response = MagicMock()
        response.status = 200
        response.json = AsyncMock(return_value={'choices': [{'finish_reason': 'stop', 'message': {'content': '{"ok":true}'}}],
            'model': 'test-vision'})
        response.__aenter__ = AsyncMock(return_value=response)
        response.__aexit__ = AsyncMock(return_value=False)
        session = MagicMock()
        session.post.return_value = response
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        messages = [{'role': 'user', 'content': [{'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,test', 'detail': 'original'}}]}]
        with patch.object(base.core.aiohttp, 'ClientSession', return_value=session):
            value, meta = await base.core.chat('test-secret-marker', messages, 10, 20, service=service)
        self.assertTrue(value['ok'])
        self.assertEqual(session.post.call_args.args[0], 'https://generativelanguage.googleapis.com/v1beta/openai/chat/completions')
        payload = session.post.call_args.kwargs['json']
        self.assertEqual(payload['model'], 'test-vision')
        self.assertNotIn('thinking', payload)
        self.assertNotIn('detail', payload['messages'][0]['content'][0]['image_url'])
        self.assertEqual(messages[0]['content'][0]['image_url']['detail'], 'original')
        self.assertNotIn('test-secret-marker', json.dumps(meta))

    def test_runtime_cache_signature_changes_when_active_model_changes(self):
        before = providers.fingerprint(self.key_path)
        self.configure()
        after = providers.fingerprint(self.key_path)
        self.assertNotEqual(before, after)
        self.configure(text_model='different')
        self.assertNotEqual(after, providers.fingerprint(self.key_path))
