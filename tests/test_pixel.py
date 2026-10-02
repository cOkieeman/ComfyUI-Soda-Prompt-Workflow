import json
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, Mock, patch

import numpy as np
import test_workflow as base

nodes = sys.modules["soda_test.suite_nodes"]
suite = sys.modules["soda_test.suite"]
API = {"response_model": "deepseek-flash", "usage": {}}


class PixelTests(base.WorkflowTests):
    def setUp(self):
        super().setUp()
        self.rule = 'TEST ORIGINAL PIXEL RULE'
        loader = patch.object(nodes, 'read_preset', return_value=self.rule)
        loader.start()
        self.addCleanup(loader.stop)

    async def test_original_verbatim_and_plain_text_cached_without_wd(self):
        image = [base.Tensor(np.zeros((8, 8, 3), dtype=np.float32))]
        rule = self.rule
        with patch.object(base.core, "chat_text", AsyncMock(return_value=("A woman reads a book.", API))) as api, patch.object(nodes.local, "tag_image", side_effect=AssertionError("WD must not run")):
            first = await nodes.SodaReferenceSuite().run(image, suite.PIXEL, 0, 1600, 0, 30)
            second = await nodes.SodaReferenceSuite().run(image, suite.PIXEL, 0, 1600, 0, 30)
            self.assertEqual(api.await_count, 1)
            self.assertEqual(api.await_args.args[1][0]["content"], rule)
            self.assertEqual(first[0], "A woman reads a book.")
            self.assertTrue(json.loads(second[1])["api"]["cache_hit"])

    async def test_fusion_instruction_is_sent_and_changes_cache(self):
        image = [base.Tensor(np.zeros((8, 8, 3), dtype=np.float32))]
        with patch.object(base.core, "chat_text", AsyncMock(return_value=("A portrait.", API))) as api:
            await nodes.SodaReferenceSuite().run(image, suite.PIXEL, 0, 1600, 0, 30)
            _, record = await nodes.SodaReferenceSuite().run(image, suite.PIXEL, 0, 1600, 0, 30, "将背景改成雨天")
            self.assertEqual(api.await_count, 2)
            self.assertEqual(api.await_args.args[1][1]["content"][0]["text"], "将背景改成雨天")
            self.assertEqual(json.loads(record)["mode"], "fusion")

    async def test_plain_transport_does_not_require_json(self):
        response = base.FakeResponse()
        response.json = AsyncMock(return_value={"choices": [{"finish_reason": "stop", "message": {"content": "A plain caption."}}], "model": "deepseek-flash"})
        session = AsyncMock()
        session.__aenter__.return_value = session
        session.post = Mock(return_value=response)
        with patch.object(base.core.aiohttp, "ClientSession", return_value=session):
            text, _ = await base.core.chat_text("test-secret", [], 30, 6000)
        self.assertEqual(text, "A plain caption.")
        self.assertEqual(session.post.call_args.kwargs["json"]["model"], "deepseek-flash")

    async def test_text_transport_rejects_other_model(self):
        with patch.object(base.core, "chat_text", AsyncMock(return_value=("text", {"response_model": "other"}))):
            with self.assertRaises(RuntimeError):
                await suite.cached_chat(Path(self.temp.name) / "cache", Path("unused"), "pixel", [], 0, 30, 6000, text_output=True)


if __name__ == "__main__":
    unittest.main()
