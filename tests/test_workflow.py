import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, patch

import numpy as np

PACKAGE = Path(__file__).resolve().parents[1]
HOST = types.ModuleType("folder_paths")
sys.modules["folder_paths"] = HOST
SPEC = importlib.util.spec_from_file_location("soda_test", PACKAGE / "__init__.py",
                                            submodule_search_locations=[str(PACKAGE)])
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
core = sys.modules["soda_test.core"]
nodes = sys.modules["soda_test.nodes"]


class Tensor:
    def __init__(self, pixels):
        self.pixels = pixels

    def detach(self):
        return self

    def cpu(self):
        return self

    def numpy(self):
        return self.pixels


class WorkflowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        HOST.get_user_directory = lambda: self.temp.name
        HOST.get_output_directory = lambda: self.temp.name
        self.env = patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-only-not-a-real-key"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_image_encoding_and_requested_resize(self):
        image = np.zeros((120, 240, 3), dtype=np.float32)
        encoded, info = core.encode_image(image, 100)
        self.assertTrue(encoded.startswith("data:image/png;base64,"))
        self.assertEqual(info["original_size"], [240, 120])
        self.assertEqual(info["sent_size"], [100, 50])
        self.assertEqual(core.encode_image(image, 0)[1]["sent_size"], [240, 120])

    def test_bad_image_stops_before_request(self):
        with self.assertRaises(ValueError):
            core.encode_image(np.full((4, 4, 3), np.nan), 100)

    def test_empty_description_and_invalid_json_fail(self):
        with self.assertRaises(RuntimeError):
            core.validate_observation({"description": "", "uncertainties": []})
        with self.assertRaises(RuntimeError):
            core.parse_json("not JSON")

    def test_unknown_target_does_not_guess(self):
        with self.assertRaises(ValueError):
            core.compose_messages({"description": "A red box.", "uncertainties": []}, "Qwen2.1", False, "")

    def test_expansion_disabled_is_enforced(self):
        result = core.validate_prompts({"faithful_prompt": "A box.", "expanded_prompt": "Invented city.",
                                        "changes": ["invented"]}, False)
        self.assertEqual(result["expanded_prompt"], "")
        self.assertEqual(result["changes"], [])

    def test_secret_errors_do_not_echo_content(self):
        path = Path(self.temp.name) / "bad.toml"
        path.write_text('deepseek_api_key = "SENSITIVE_MARKER', encoding="utf-8")
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}):
            with self.assertRaises(RuntimeError) as caught:
                core.read_key(path)
        self.assertNotIn("SENSITIVE_MARKER", str(caught.exception))

    def test_missing_key_stops_before_network(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}):
            with self.assertRaisesRegex(RuntimeError, "尚未配置"):
                core.read_key(Path(self.temp.name) / "missing.toml")

    def test_enabled_expansion_cannot_silently_fall_back(self):
        with self.assertRaisesRegex(RuntimeError, "没有返回扩写稿"):
            core.validate_prompts({"faithful_prompt": "A box.", "expanded_prompt": "", "changes": []}, True)

    async def test_real_node_boundary_observe_adapt_save(self):
        meta = {"requested_model": "deepseek-flash", "usage": {"total_tokens": 10}}
        observation = {"description": "A red box on a blue floor.", "uncertainties": []}
        prompts = {"faithful_prompt": "red box, blue floor", "expanded_prompt": "", "changes": []}
        with patch.object(core, "chat", new=AsyncMock(side_effect=[(observation, meta), (prompts, meta)])) as api:
            observed = await nodes.SodaDeepSeekObserve().observe(
                [Tensor(np.zeros((24, 24, 3), dtype=np.float32))], 0, 1600, 0, 120)
            output = await nodes.SodaDeepSeekPrompt().compose(observed[0], "Anima", False, "", 0, 3500, 120)
            self.assertEqual(api.await_count, 2)
            messages = api.await_args_list[0].args[1]
            self.assertEqual(messages[1]["content"][1]["type"], "image_url")
        self.assertEqual(output[0], output[1])
        self.assertEqual(output[2], "")
        saved = nodes.SodaSavePromptRecord().save_record(output[3], True)[0]
        self.assertEqual((Path(saved) / "faithful.txt").read_text(encoding="utf-8"), output[1])
        record = (Path(saved) / "record.json").read_text(encoding="utf-8")
        self.assertNotIn("test-only-not-a-real-key", record)
        self.assertNotIn("base64", record)
        second = nodes.SodaSavePromptRecord().save_record(output[3], True)[0]
        self.assertNotEqual(saved, second)

    async def test_expanded_output_preserves_faithful(self):
        observation = json.dumps({"description": "A box.", "uncertainties": []})
        response = {"faithful_prompt": "A box.", "expanded_prompt": "A box with subtle shading.",
                    "changes": ["增加明暗层次"]}
        with patch.object(core, "chat", new=AsyncMock(return_value=(response, {}))):
            result = await nodes.SodaDeepSeekPrompt().compose(observation, "Krea2", True, "", 0, 3500, 120)
        self.assertEqual(result[0], response["expanded_prompt"])
        self.assertEqual(result[1], response["faithful_prompt"])

    def test_disabled_save_does_not_create_files(self):
        nodes.SodaSavePromptRecord().save_record("unused", False)
        self.assertEqual(list(Path(self.temp.name).iterdir()), [])

    def test_archive_rejects_path_in_target(self):
        record = {"target_model": "../../outside", "expand": False,
                  "prompts": {"faithful_prompt": "A box.", "expanded_prompt": "", "changes": []}}
        with self.assertRaises(ValueError):
            nodes.SodaSavePromptRecord().save_record(json.dumps(record), True)

    def test_workflow_links_and_no_key_widget(self):
        workflow = json.loads((PACKAGE / "workflows" / "Soda-Prompt-Workbench.json").read_text(encoding="utf-8"))
        by_id = {n["id"]: n for n in workflow["nodes"]}
        for link_id, source, slot, dest, dest_slot, kind in workflow["links"]:
            self.assertIn(link_id, by_id[source]["outputs"][slot]["links"])
            self.assertEqual(by_id[dest]["inputs"][dest_slot]["link"], link_id)
            self.assertEqual(by_id[source]["outputs"][slot]["type"], kind)
        for cls in MODULE.NODE_CLASS_MAPPINGS.values():
            self.assertNotIn("api_key", cls.INPUT_TYPES()["required"])


class FakeResponse:
    def __init__(self, status=200, finish="stop"):
        self.status = status
        self.finish = finish

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def json(self, **kwargs):
        return {"choices": [{"finish_reason": self.finish, "message": {
            "content": '{"description":"A box.","uncertainties":[]}'}}],
                "usage": {"total_tokens": 22}, "model": "deepseek-flash"}


class HTTPTests(unittest.IsolatedAsyncioTestCase):
    async def test_official_request_shape_and_truncation(self):
        session = AsyncMock()
        session.__aenter__.return_value = session
        from unittest.mock import Mock
        session.post = Mock(return_value=FakeResponse())
        with patch.object(core.aiohttp, "ClientSession", return_value=session):
            result, metadata = await core.chat("secret-marker", [{"role": "user", "content": "hi"}], 120, 2500)
        kwargs = session.post.call_args.kwargs
        self.assertFalse(kwargs["allow_redirects"])
        self.assertEqual(kwargs["json"]["model"], "deepseek-flash")
        self.assertEqual(kwargs["json"]["thinking"], {"type": "disabled"})
        self.assertEqual(metadata["usage"]["total_tokens"], 22)
        self.assertEqual(result["description"], "A box.")

    async def test_http_error_and_truncation_fail_without_secret(self):
        from unittest.mock import Mock
        for response in [FakeResponse(401), FakeResponse(429), FakeResponse(200, "length")]:
            session = AsyncMock()
            session.__aenter__.return_value = session
            session.post = Mock(return_value=response)
            with patch.object(core.aiohttp, "ClientSession", return_value=session):
                with self.assertRaises(RuntimeError) as caught:
                    await core.chat("secret-marker", [], 120, 2500)
            self.assertNotIn("secret-marker", str(caught.exception))
            self.assertEqual(session.post.call_count, 1)


if __name__ == "__main__":
    unittest.main()
