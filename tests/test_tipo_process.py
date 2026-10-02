import asyncio
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import test_workflow

impl = sys.modules['soda_test.tipo_process']


class ProcessTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.worker = Path(self.temp.name) / 'fixture_worker.py'
        self.worker.write_text('import json,sys,time\nfrom pathlib import Path\n'
            'request=json.loads(Path(sys.argv[1]).read_text())\n'
            'time.sleep(request["delay"])\n'
            'Path(sys.argv[2]).write_text(json.dumps({"result":{"description":"done"}}))\n', encoding='utf-8')
        self.processes = []
        self.popen = subprocess.Popen

    def spawn(self, *args, **kwargs):
        process = self.popen(*args, **kwargs)
        self.processes.append(process)
        return process

    async def test_success_waits_for_process_exit(self):
        with patch.object(impl.subprocess, 'Popen', self.spawn):
            result = await impl.execute_worker({'delay': 0}, 10, check=lambda: None, worker_path=self.worker)
        self.assertEqual(result, {'description': 'done'})
        self.assertEqual(self.processes[0].returncode, 0)

    async def test_timeout_kills_and_reaps_worker(self):
        with patch.object(impl.subprocess, 'Popen', self.spawn):
            with self.assertRaises(TimeoutError):
                await impl.execute_worker({'delay': 30}, .03, check=lambda: None, worker_path=self.worker)
        self.assertIsNotNone(self.processes[0].poll())

    async def test_task_cancellation_kills_and_reaps_worker(self):
        with patch.object(impl.subprocess, 'Popen', self.spawn):
            task = asyncio.create_task(impl.execute_worker({'delay': 30}, 60, check=lambda: None, worker_path=self.worker))
            await asyncio.sleep(.03)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertIsNotNone(self.processes[0].poll())

    async def test_comfy_interrupt_kills_worker(self):
        class Interrupted(Exception):
            pass
        def check():
            if self.processes:
                raise Interrupted()
        with patch.object(impl.subprocess, 'Popen', self.spawn):
            with self.assertRaises(Interrupted):
                await impl.execute_worker({'delay': 30}, 60, check=check, worker_path=self.worker)
        self.assertIsNotNone(self.processes[0].poll())
