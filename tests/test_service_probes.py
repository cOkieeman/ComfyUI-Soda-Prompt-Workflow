import asyncio
import base64
import io
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
from aiohttp import web
from PIL import Image

import test_workflow as base
from soda_test import providers


class ServiceProbeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.key_path = Path(self.temp.name) / "secrets.toml"
        self.requests = []
        self.models_status = 200
        self.models_data = {"data": [{"id": "vision-model"}, {"id": "text-model"}, {"id": "text-model"}]}
        self.chat_status = 200
        self.returned_model = None
        self.chat_data = None
        remote = web.Application()
        remote.router.add_get("/v1/models", self.models)
        remote.router.add_post("/v1/chat/completions", self.chat)
        self.remote_runner = web.AppRunner(remote)
        await self.remote_runner.setup()
        self.addAsyncCleanup(self.remote_runner.cleanup)
        remote_site = web.TCPSite(self.remote_runner, "127.0.0.1", 0)
        await remote_site.start()
        self.remote_url = f"http://127.0.0.1:{remote_site._server.sockets[0].getsockname()[1]}/v1"

        routes = web.RouteTableDef()
        host = types.ModuleType("server")
        host.PromptServer = types.SimpleNamespace(instance=types.SimpleNamespace(routes=routes))
        with patch.dict("sys.modules", {"server": host}), patch.object(base.nodes, "key_path", return_value=self.key_path):
            providers.register_routes()
        local = web.Application()
        local.add_routes(routes)
        self.local_runner = web.AppRunner(local)
        await self.local_runner.setup()
        self.addAsyncCleanup(self.local_runner.cleanup)
        local_site = web.TCPSite(self.local_runner, "127.0.0.1", 0)
        await local_site.start()
        self.local_url = f"http://127.0.0.1:{local_site._server.sockets[0].getsockname()[1]}"
        self.session = aiohttp.ClientSession()
        self.addAsyncCleanup(self.session.close)

    async def models(self, request):
        self.requests.append((request.path, request.headers.get("Authorization"), None))
        if self.models_status == 302:
            return web.Response(status=302, headers={"Location": self.remote_url + "/models?redirected=1"})
        if isinstance(self.models_data, str):
            return web.Response(status=self.models_status, text=self.models_data)
        return web.json_response(self.models_data, status=self.models_status)

    async def chat(self, request):
        body = await request.json()
        self.requests.append((request.path, request.headers.get("Authorization"), body))
        return web.json_response(self.chat_data if self.chat_data is not None else {
            "model": self.returned_model or body["model"],
            "choices": [{"finish_reason": "stop", "message": {"content": "OK"}}],
            "usage": {"total_tokens": 4},
        }, status=self.chat_status)

    def draft(self, **updates):
        return {"service": "custom", "profile": {"base_url": self.remote_url,
            "text_model": "text-model", "vision_model": "vision-model", "strict_model": True,
            "api_key": "fake-probe-secret", **updates}}

    async def post(self, action, value, headers=None):
        async with self.session.post(self.local_url + "/soda/ai-services/" + action,
                json=value, headers=headers or {}) as response:
            return response.status, await response.json(), response.headers

    async def test_models_sorted_deduplicated_full_chat_url_and_no_persistence(self):
        draft = self.draft(base_url=self.remote_url + "/chat/completions/")
        result = await providers.fetch_models(self.key_path, draft)
        self.assertEqual(result, {"models": ["text-model", "vision-model"]})
        self.assertEqual(self.requests[0][:2], ("/v1/models", "Bearer fake-probe-secret"))
        self.assertFalse(providers.config_path(self.key_path).exists())
        self.assertNotIn("fake-probe-secret", json.dumps(result))

    async def test_routes_accept_unsaved_values_and_return_no_store_without_secrets(self):
        for action, value in (("models", self.draft()), ("test", {**self.draft(), "model_type": "vision"})):
            status, result, headers = await self.post(action, value, {"Origin": self.local_url})
            self.assertEqual(status, 200)
            self.assertEqual(headers["Cache-Control"], "no-store")
            self.assertNotIn("fake-probe-secret", json.dumps(result))
        self.assertFalse(providers.config_path(self.key_path).exists())

    async def test_routes_block_cross_site_before_calling_remote_and_report_validation_errors(self):
        for action in ("models", "test"):
            for headers in ({"Origin": "https://other.example"}, {"Sec-Fetch-Site": "cross-site"}):
                status, _, _ = await self.post(action, self.draft(), headers)
                self.assertEqual(status, 403)
            status, result, headers = await self.post(action, {"service": "unknown"})
            self.assertEqual(status, 400)
            self.assertIn("error", result)
            self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(self.requests, [])

    async def test_text_and_vision_use_selected_models_and_real_inline_png(self):
        for kind in ("text", "vision"):
            result = await providers.test_service(self.key_path, {**self.draft(), "model_type": kind})
            self.assertTrue(result["ok"])
            self.assertEqual(result["response_model"], kind + "-model")
            self.assertGreaterEqual(result["elapsed_ms"], 0)
            body = self.requests[-1][2]
            self.assertEqual(body["model"], kind + "-model")
            self.assertEqual(body["max_tokens"], 64)
            self.assertFalse(body["stream"])
            self.assertNotIn("thinking", body)
            if kind == "vision":
                url = body["messages"][0]["content"][1]["image_url"]["url"]
                image = Image.open(io.BytesIO(base64.b64decode(url.split(",")[1])))
                self.assertEqual(image.size, (32, 32))
            else:
                self.assertIsInstance(body["messages"][0]["content"], str)
        self.assertFalse(providers.config_path(self.key_path).exists())

    async def test_saved_key_reused_only_for_unchanged_address_without_activating_draft(self):
        providers.save(self.key_path, {"active": "deepseek", "profiles": {"custom": self.draft()["profile"]}})
        original = providers.config_path(self.key_path).read_bytes()
        await providers.fetch_models(self.key_path, self.draft(api_key=""))
        with self.assertRaisesRegex(ValueError, "重新填写"):
            await providers.fetch_models(self.key_path, self.draft(api_key="", base_url="https://different.example/v1"))
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(providers.config_path(self.key_path).read_bytes(), original)
        self.assertEqual(providers.load(self.key_path)["active"], "deepseek")

    async def test_default_deepseek_can_use_legacy_key_but_other_addresses_cannot(self):
        value = {"service": "deepseek", "profile": {}}
        with patch.object(base.core, "read_key", return_value="fake-legacy-key") as read:
            _, key = providers.draft_service(self.key_path, value)
            self.assertEqual(key, "fake-legacy-key")
            with self.assertRaises(ValueError):
                providers.draft_service(self.key_path, {"service": "deepseek", "profile": {"base_url": self.remote_url}})
            self.assertEqual(read.call_count, 1)

    async def test_models_failures_do_not_echo_response_bodies_or_follow_redirects(self):
        for status in (401, 403, 404, 429, 500, 302):
            self.models_status = status
            self.models_data = {"error": "fake-probe-secret"}
            with self.assertRaisesRegex(RuntimeError, f"HTTP {status}") as error:
                await providers.fetch_models(self.key_path, self.draft())
            self.assertNotIn("fake-probe-secret", str(error.exception))
        self.assertEqual(len(self.requests), 6)

    async def test_invalid_empty_and_non_json_model_lists_keep_manual_fallback(self):
        for data in ({"data": []}, {"data": [{"id": None}]}, {"data": "bad"}, [], "not-json",
                     {"data": [{"id": "fake-probe-secret"}, {"id": "bad\nmodel"}]}):
            self.models_data = data
            with self.assertRaisesRegex(RuntimeError, "手动填写"):
                await providers.fetch_models(self.key_path, self.draft())

    async def test_test_enforces_strict_model_and_can_warn_on_alias(self):
        self.returned_model = "model-alias"
        with self.assertRaisesRegex(RuntimeError, "严格核对"):
            await providers.test_service(self.key_path, {**self.draft(), "model_type": "text"})
        result = await providers.test_service(self.key_path, {**self.draft(strict_model=False), "model_type": "text"})
        self.assertTrue(result["warning"])

    async def test_failed_chat_empty_or_truncated_output_never_reports_success(self):
        self.chat_status = 401
        with self.assertRaisesRegex(RuntimeError, "401"):
            await providers.test_service(self.key_path, {**self.draft(), "model_type": "text"})
        self.chat_status = 200
        for finish, content in (("length", "OK"), ("stop", "")):
            self.chat_data = {"model": "text-model", "choices": [{"finish_reason": finish, "message": {"content": content}}]}
            with self.assertRaises(RuntimeError):
                await providers.test_service(self.key_path, {**self.draft(), "model_type": "text"})

    async def test_invalid_drafts_fail_before_outbound_request(self):
        values = [[], {"service": []}, self.draft(api_key="fake\nkey"),
            self.draft(base_url="https://example.com?key=fake-secret"),
            {**self.draft(vision_model=""), "model_type": "vision"},
            {**self.draft(), "model_type": "unknown"}]
        for value in values:
            with self.assertRaises(ValueError):
                await providers.test_service(self.key_path, value)
        self.assertEqual(self.requests, [])

    async def test_network_timeout_has_safe_message_and_no_retry(self):
        with patch.object(base.core, "chat_text", AsyncMock(side_effect=RuntimeError("网络失败或超时 fake-probe-secret"))) as chat:
            with self.assertRaises(RuntimeError) as error:
                await providers.test_service(self.key_path, {**self.draft(), "model_type": "text"})
            self.assertNotIn("fake-probe-secret", str(error.exception))
            self.assertEqual(chat.await_count, 1)

    async def test_models_network_timeout_is_safe_and_not_retried(self):
        session = MagicMock()
        session.__aenter__ = AsyncMock(side_effect=asyncio.TimeoutError())
        with patch.object(aiohttp, "ClientSession", return_value=session) as factory:
            with self.assertRaisesRegex(RuntimeError, "网络失败或超时"):
                await providers.fetch_models(self.key_path, self.draft())
            self.assertEqual(factory.call_count, 1)

