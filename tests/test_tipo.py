import json
from pathlib import Path
import sys
import asyncio
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, patch

import test_workflow as base
host = base.HOST
impl = sys.modules["soda_test.tipo_expansion"]


class TipoTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        host.models_dir = self.temp.name
        host.get_user_directory = lambda: self.temp.name
        self.model = Path(self.temp.name) / "kgen" / impl.MODELS["TIPO v2.1 · FP16"]
        self.model.parent.mkdir()
        self.model.write_bytes(b"GGUF-test")
        self.args = dict(source_text="A girl reads a book.", method=impl.TIPO,
            requirements="Flash only", tipo_input=impl.AUTO, tipo_output=impl.KEEP,
            tipo_model="TIPO v2.1 · FP16", tipo_device="cpu", tipo_seed=1234,
            tipo_length="short", tipo_temperature=0.5, tipo_width=896,
            tipo_height=1152, tipo_ban_tags="", refresh=0, timeout_seconds=180)
        self.result = {"description": "A girl reads beneath a lamp.",
            "generated_tags": "1girl, reading, lamp", "generated_nl": "Expanded.",
            "unformatted": "raw result"}

    async def test_off_works_without_models_or_tipo(self):
        self.model.unlink()
        with patch.object(impl, "run_tipo") as backend, patch.object(impl.SodaExpandSuite,
                "run", AsyncMock(return_value=("kept", "{}"))) as flash:
            result = await impl.SodaExpansionChoice().run(**(self.args | {"method": impl.OFF}))
        self.assertEqual(result[0], "kept")
        backend.assert_not_called()
        self.assertFalse(flash.call_args.args[1])

    async def test_flash_preserves_each_preset_and_never_uses_tipo(self):
        for preset in (impl.suite.K2, impl.suite.DFLOW_EXPAND):
            with patch.object(impl, "run_tipo") as backend, patch.object(impl.SodaExpandSuite,
                    "run", AsyncMock(return_value=("flash", "{}"))) as flash:
                await impl.SodaExpansionChoice().run(**(self.args | {"method": preset}))
                self.assertEqual(flash.call_args.args[1:4], (True, preset, "Flash only"))
                backend.assert_not_called()

    def test_pure_tags_are_not_sent_as_natural_language(self):
        self.assertEqual(impl.split_input("1girl, reading", {}, impl.TAGS), ("1girl, reading", ""))

    def test_auto_does_not_guess_unstructured_comma_text(self):
        text = "A girl, sitting beside a window."
        self.assertEqual(impl.split_input(text, {}, impl.AUTO), ("", text))

    def test_matching_record_splits_tags_and_description(self):
        text = "1girl, reading\n\nShe holds a book."
        source = {"faithful_prompt": text, "tags": ["1girl", "reading"]}
        self.assertEqual(impl.split_input(text, source, impl.AUTO), ("1girl, reading", "She holds a book."))
        source["faithful_prompt"] = "different input"
        self.assertEqual(impl.split_input(text, source, impl.AUTO), ("", text))

    def test_explicit_mixed_supports_paragraph_and_single_newline(self):
        for separator in ("\n", "\n\n", "\r\n\r\n"):
            self.assertEqual(impl.split_input("1girl, reading," + separator + "She holds a book.", {}, impl.MIXED),
                ("1girl, reading", "She holds a book."))

    async def test_tipo_caches_and_keeps_provenance(self):
        args = self.args | {"tipo_input": impl.MIXED,
            "source_text": "1girl, reading\n\nShe holds a book.",
            "source_record": json.dumps({"source": "DFlow", "key": "kept"})}
        with patch.object(impl, "run_tipo", return_value=self.result) as backend:
            first = await impl.SodaExpansionChoice().run(**args)
            second = await impl.SodaExpansionChoice().run(**args)
            backend.assert_called_once()
            self.assertEqual(backend.call_args.args[:2], ("1girl, reading", "She holds a book."))
        value = json.loads(first[1])
        self.assertTrue(first[0].startswith("1girl, reading\n\n"))
        self.assertEqual(value["source_record"]["key"], "kept")
        self.assertEqual(value["tipo"]["parameters"]["seed"], 1234)
        self.assertEqual(value["api_calls"], 0)
        self.assertTrue(json.loads(second[1])["tipo"]["cache_hit"])
        self.assertEqual(value["faithful_prompt"], args["source_text"])

    async def test_output_choices_use_correct_text(self):
        with patch.object(impl, "run_tipo", return_value=self.result):
            generated = await impl.SodaExpansionChoice().run(**(self.args | {"tipo_output": impl.GENERATED}))
            natural = await impl.SodaExpansionChoice().run(**(self.args | {"tipo_output": impl.NL_ONLY}))
        self.assertEqual(generated[0], "1girl, reading, lamp\n\n" + self.result["description"])
        self.assertEqual(natural[0], self.result["description"])

    async def test_seed_and_model_file_change_invalidate_cache(self):
        with patch.object(impl, "run_tipo", return_value=self.result) as backend:
            await impl.SodaExpansionChoice().run(**self.args)
            await impl.SodaExpansionChoice().run(**(self.args | {"tipo_seed": 8}))
            self.model.write_bytes(b"GGUF-changed-model")
            await impl.SodaExpansionChoice().run(**self.args)
            self.assertEqual(backend.call_count, 3)

    async def test_empty_failure_is_not_cached(self):
        with patch.object(impl, "run_tipo", return_value=self.result | {"description": ""}) as backend:
            for _ in range(2):
                with self.assertRaisesRegex(RuntimeError, "为空"):
                    await impl.SodaExpansionChoice().run(**self.args)
            self.assertEqual(backend.call_count, 2)

    def test_model_selection_cannot_access_arbitrary_paths(self):
        with self.assertRaisesRegex(ValueError, "未知"):
            impl.model_path("../../secret.gguf")

    async def test_missing_model_does_not_start_inference(self):
        self.model.unlink()
        with patch.object(impl, "run_tipo") as backend:
            with self.assertRaisesRegex(FileNotFoundError, "kgen"):
                await impl.SodaExpansionChoice().run(**self.args)
            backend.assert_not_called()

    async def test_worker_request_contract_and_parameters(self):
        class Native:
            pass
        registry = types.ModuleType("nodes")
        registry.NODE_CLASS_MAPPINGS = {"TIPO": Native}
        cli = types.ModuleType('comfy.cli_args')
        cli.args = types.SimpleNamespace(cuda_device=0)
        host.__file__ = str(Path(self.temp.name) / 'folder_paths.py')
        with patch.dict(sys.modules, {"nodes": registry, 'comfy': types.ModuleType('comfy'), 'comfy.cli_args': cli}), \
                patch.object(impl, 'execute_worker', AsyncMock(return_value=self.result)) as worker:
            result = await impl.run_tipo("tags", "natural", self.model, {"ban_tags": "", "width": 896,
                "height": 1152, "temperature": .5, "length": "short", "seed": 1234, "device": "cpu"}, 30)
        self.assertEqual(result, self.result)
        self.assertEqual(worker.call_args.args[0]['parameters']['seed'], 1234)
        self.assertEqual(worker.call_args.args[0]['tags'], 'tags')
        self.assertEqual(worker.call_args.args[1], 30)

    async def test_timeout_does_not_cache_or_complete_inference(self):
        async def slow(*args):
            await asyncio.sleep(30)
            return self.result
        with patch.object(impl, 'run_tipo', side_effect=slow):
            with self.assertRaises(TimeoutError):
                await impl.SodaExpansionChoice().run(**(self.args | {'timeout_seconds': .02}))
        self.assertEqual(list(Path(self.temp.name).rglob('*.json')), [])

    async def test_cancel_does_not_cache(self):
        async def slow(*args):
            await asyncio.sleep(30)
            return self.result
        with patch.object(impl, 'run_tipo', side_effect=slow):
            task = asyncio.create_task(impl.SodaExpansionChoice().run(**self.args))
            await asyncio.sleep(.02)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(list(Path(self.temp.name).rglob('*.json')), [])


if __name__ == "__main__":
    unittest.main()
