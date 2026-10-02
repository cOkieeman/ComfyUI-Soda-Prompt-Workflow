import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import test_workflow as base

nodes = sys.modules["soda_test.suite_nodes"]


class OutputTests(unittest.TestCase):
    def test_off_preserves_source_and_validation(self):
        record = {"faithful_prompt": "source", "validation": {"passed": False, "problems": ["review"]}}
        text, raw = nodes.SodaPromptOutput().run("source", False, "ignored", "Krea2", json.dumps(record))
        self.assertEqual(text, "source")
        self.assertFalse(json.loads(raw)["validation"]["passed"])

    def test_edit_keeps_original_and_exports_selected(self):
        source = {"faithful_prompt": "original", "expanded_prompt": "expanded", "selected_prompt": "expanded"}
        text, raw = nodes.SodaPromptOutput().run("expanded", True, "corrected", "Krea2", json.dumps(source))
        result = json.loads(raw)
        self.assertEqual(text, "corrected")
        self.assertEqual(result["faithful_prompt"], "original")
        self.assertEqual(result["expanded_prompt"], "expanded")
        with tempfile.TemporaryDirectory() as directory, patch.object(base.HOST, "get_output_directory", return_value=directory, create=True):
            nodes.SodaSuiteRecord().run(raw, True)
            saved = list(Path(directory).rglob("selected_prompt.txt"))
            self.assertEqual(len(saved), 1)
            self.assertEqual(saved[0].read_text(encoding="utf-8"), "corrected")

    def test_empty_edit_does_not_silently_use_original(self):
        with self.assertRaises(ValueError):
            nodes.SodaPromptOutput().run("source", True, "  ", "Anima")

    def test_mismatched_record_does_not_claim_source_validation(self):
        source = {"faithful_prompt": "different", "validation": {"passed": True, "tokens": 5}}
        _, raw = nodes.SodaPromptOutput().run("source", False, "", "Krea2", json.dumps(source))
        result = json.loads(raw)
        self.assertEqual(result["faithful_prompt"], "source")
        self.assertNotIn("tokens", result["validation"])

    def test_anima_budget_is_checked_without_truncation(self):
        text = "A woman reads a book under the warm light. " * 200
        output, raw = nodes.SodaPromptOutput().run(text, False, "", "Anima")
        self.assertEqual(output, text)
        self.assertFalse(json.loads(raw)["validation"]["passed"])


if __name__ == "__main__":
    unittest.main()
