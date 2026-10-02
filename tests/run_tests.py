from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
suite = unittest.defaultTestLoader.loadTestsFromNames([
    'test_workflow', 'test_suite', 'test_pixel', 'test_prompt_output',
    'test_materials', 'test_unified', 'test_rectification', 'test_tipo', 'test_tipo_process',
])
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(not result.wasSuccessful())
