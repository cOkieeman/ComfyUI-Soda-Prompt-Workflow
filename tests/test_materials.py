import json
import sys
import unittest
from unittest.mock import AsyncMock, patch

import test_workflow

m = sys.modules["soda_test.material_nodes"]
p = sys.modules["soda_test.prompt_stages"]


class MaterialTests(unittest.IsolatedAsyncioTestCase):
    def test_normalization_keeps_metadata_not_secrets(self):
        item={"id":"a","folder":"pending","prompt":"saved", "cacheStatus":"ready", "api_key":"secret", "model":"model.safetensors"}
        card=m.normalize_card(item,"favorite",4173)
        self.assertNotIn("secret",json.dumps(card))
        self.assertEqual(card["metadata"]["model"],"model.safetensors")
        self.assertEqual(card["image_path"],"/api/reverse/image/a")

    async def test_original_does_not_reverse(self):
        with patch.object(m.SodaReferenceSuite,"run",new_callable=AsyncMock) as ai:
            text,raw=await m.SodaMaterialPrompt().run("使用素材原提示词",m.suite.PIXEL,"",True,0,30,source_text="original")
            self.assertEqual(text,"original")
            ai.assert_not_awaited()
            self.assertEqual(json.loads(raw)["api_calls"],0)

    async def test_empty_original_never_falls_back_to_paid_reverse(self):
        with self.assertRaisesRegex(ValueError,"不会自动"):
            await m.SodaMaterialPrompt().run("使用素材原提示词",m.suite.PIXEL,"",True,0,30,source_text="")

    def test_lazy_selection_requests_only_chosen_input(self):
        node=m.SodaMaterialPrompt()
        self.assertEqual(node.check_lazy_status("使用素材原提示词"),["source_text"])
        self.assertEqual(node.check_lazy_status("重新反推"),["source_image"])

    async def test_disabled_writeback_never_contacts_dflow(self):
        with patch.object(m,"get_card",new_callable=AsyncMock) as request:
            await m.SodaDFlowWriteback().run("text","invalid",False)
            request.assert_not_awaited()

    async def test_writeback_skips_equal_and_refuses_processing(self):
        card={"prompt":"same","metadata":{},"id":"a","kind":"worded","port":4173}
        with patch.object(m,"get_card",AsyncMock(return_value=card)),patch.object(m,"dflow_request",new_callable=AsyncMock) as request:
            await m.SodaDFlowWriteback().run("same",'{"port":4173,"key":"worded:a"}',True)
            request.assert_not_awaited()
            card["metadata"]["reverseStatus"]="processing"
            with self.assertRaisesRegex(ValueError,"其他任务"):
                await m.SodaDFlowWriteback().run("new",'{"port":4173,"key":"worded:a"}',True)
            request.assert_not_awaited()

    async def test_prefix_suffix_without_oc_never_calls_api(self):
        with patch.object(p.suite,"cached_chat",new_callable=AsyncMock) as ai:
            text,raw=await p.SodaPromptStages().run("source","front","back",False,"","",0,30)
            self.assertEqual(text,"front, source, back")
            ai.assert_not_awaited()

    async def test_author_oc_rule_is_verbatim_and_cached_transport(self):
        expected='TEST ORIGINAL OC RULE'
        with patch.object(p, 'read_preset', return_value=expected), patch.object(p.suite,"cached_chat",AsyncMock(return_value=("edited",{"cache_hit":False}))) as ai:
            text,_=await p.SodaPromptStages().run("source","","",True,"red hair","hair",0,30)
            self.assertEqual(text,"edited")
            self.assertEqual(ai.await_args.args[3][0]["content"],expected)
            self.assertTrue(ai.await_args.kwargs["text_output"])


if __name__=="__main__": unittest.main()
