import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from PIL import Image, PngImagePlugin

import test_workflow as base

suite = sys.modules["soda_test.suite"]
local = sys.modules["soda_test.local_pipeline"]
nodes = sys.modules["soda_test.suite_nodes"]


def slots():
    value = {name: [] for name in local.SLOTS}
    value.update(rating=["general"], count=["1girl", "solo"], appearance=["hair bun"],
                 clothing_props=["long sleeves", "white shirt"])
    return value


def tagged():
    return {"general": {"1girl": .99, "solo": .95, "white_shirt": .92,
                        "hair_bun": .68, "short_hair": .39, "long_sleeves": .65},
            "character": {}, "rating": {"general": .9}}


def reverse():
    return {"slots": slots(), "nl": "The woman holds a book at the center of the scene. Warm light illuminates the pale fabric from below.",
            "description": "A woman with a hair bun and long sleeves reads a book.", "uncertainties": [],
            "decisions": [{"tag": "hair bun", "keep": True, "reason": "Coiled braid at rear of head."},
                          {"tag": "short hair", "keep": False, "reason": "Pinned up rather than cut short."},
                          {"tag": "long sleeves", "keep": True, "reason": "Cuffs reach wrists."}]}


def expansion():
    sentence = "Soft amber light follows the folds of the white fabric, while cool reflected light separates the figure from the blue background."
    return {"slots": slots(), "atmosphere": sentence,
            "layers": {k: sentence + " " + sentence for k in local.LAYERS},
            "subject": " ".join([sentence] * 3), "master": " ".join([sentence] * 6), "changes": []}


API = {"requested_model": "deepseek-flash", "response_model": "deepseek-flash", "usage": {"total_tokens": 1}}


class SuiteTests(base.WorkflowTests):
    def test_reverse_complete_decisions_and_exact_tokens(self):
        result = suite.validate_reverse(tagged(), reverse())
        self.assertTrue(result["passed"], result)
        self.assertIn("hair bun", result["tags"])
        self.assertNotIn("short hair", result["tags"])
        self.assertEqual(result["tokens"], len(local.tokenizer().encode(result["text"], add_special_tokens=False).ids))

    def test_missing_decision_or_tag_invention_rejected(self):
        response = reverse()
        response["decisions"].pop()
        with self.assertRaises(ValueError):
            suite.validate_reverse(tagged(), response)

    def test_only_provable_parent_folding_is_accepted(self):
        sample = tagged()
        sample["general"]["shirt"] = .96
        result = suite.validate_reverse(sample, reverse())
        self.assertTrue(result["passed"])
        self.assertIn(("shirt", "white shirt"), result["source_folded"])
        broken = reverse()
        broken["slots"]["clothing_props"].remove("long sleeves")
        with self.assertRaises(ValueError):
            suite.validate_reverse(sample, broken)
        response = reverse()
        response["slots"]["appearance"].append("red eyes")
        with self.assertRaises(ValueError):
            suite.validate_reverse(tagged(), response)

    def test_long_standard_fails_without_tag_deletion(self):
        result = local.validate_anima(slots(), "The light shines on the woman. " * 200)
        self.assertFalse(result["passed"])
        self.assertGreater(result["tokens"], 512)
        self.assertIn("hair bun", result["tags"])

    def test_layers_required_and_long_expansion_not_512_capped(self):
        response = expansion()
        result = suite.validate_expansion(suite.K2, response, ["hair bun", "white shirt"])
        self.assertTrue(result["passed"], result["problems"])
        self.assertGreater(result["tokens"], 512)
        response["layers"].pop("Background")
        with self.assertRaises(ValueError):
            suite.validate_expansion(suite.K2, response)

    def test_dflow_requires_sections_and_omits_optional(self):
        response = {"sections": {k: "人物位于画面中央，远处蓝色背景与近处暖色衣服相互衬托，光线沿着衣物褶皱形成清楚的明暗层次。" for k in ("风格", "背景", "主体", "姿势与身体", "约束")}}
        result = suite.validate_expansion(suite.DFLOW_EXPAND, response)
        self.assertTrue(result["passed"])
        self.assertNotIn("头发", result["text"])
        response["sections"].pop("约束")
        with self.assertRaises(ValueError):
            suite.validate_expansion(suite.DFLOW_EXPAND, response)

    async def test_disabled_expansion_never_calls(self):
        with patch.object(nodes, "call", new_callable=AsyncMock) as api:
            text, record = await nodes.SodaExpandSuite().run("source", False, suite.K2, "", 0, 20)
            self.assertEqual(text, "source")
            self.assertEqual(json.loads(record)["api_calls"], 0)
            api.assert_not_awaited()

    async def test_disk_cache_and_refresh(self):
        with patch.object(suite.core, "chat", AsyncMock(return_value=({"result": "ok"}, API))) as api:
            args = (Path(self.temp.name) / "cache", Path("unused"), "stage", [{"role": "user", "content": "test"}])
            first = await suite.cached_chat(*args, 0, 10, 10)
            second = await suite.cached_chat(*args, 0, 10, 10)
            self.assertFalse(first[1]["cache_hit"])
            self.assertTrue(second[1]["cache_hit"])
            await suite.cached_chat(*args, 1, 10, 10)
            self.assertEqual(api.await_count, 2)

    async def test_uncertain_request_is_not_repeated(self):
        with patch.object(suite.core, "chat", AsyncMock(side_effect=RuntimeError("timeout"))) as api:
            args = (Path(self.temp.name) / "cache", Path("unused"), "stage", [], 0, 10, 10)
            with self.assertRaises(RuntimeError):
                await suite.cached_chat(*args)
            with self.assertRaisesRegex(RuntimeError, "等待"):
                await suite.cached_chat(*args)
            self.assertEqual(api.await_count, 1)

    async def test_other_model_response_rejected(self):
        with patch.object(suite.core, "chat", AsyncMock(return_value=({}, {"response_model": "other"}))):
            with self.assertRaisesRegex(RuntimeError, "不是 deepseek-flash"):
                await suite.cached_chat(Path(self.temp.name) / "cache", Path("unused"), "stage", [], 0, 10, 10)

    def test_metadata_survives_malformed_graph(self):
        path = Path(self.temp.name) / "sample.png"
        info = PngImagePlugin.PngInfo()
        info.add_text("parameters", "A blue crystal cave.\nNegative prompt: bad\nSteps: 8, Seed: 9")
        info.add_text("prompt", "[broken]")
        Image.new("RGB", (4, 4)).save(path, pnginfo=info)
        result = local.metadata(path)
        self.assertEqual(result["positive"], "A blue crystal cave.")
        self.assertIn("graph_warning", result)

    def test_creation_tier_variants(self):
        with self.assertRaises(ValueError):
            suite.validate_creation({"tier": "C", "variants": [reverse()]})
        self.assertEqual(len(suite.validate_creation({"tier": "A", "variants": [reverse()]})), 1)


if __name__ == "__main__":
    unittest.main()
