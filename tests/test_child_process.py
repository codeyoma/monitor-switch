"""Exercise child lifetime using local Python children and retained journals."""
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from monitor_switch.child_process import supervise


class ChildProcessTests(unittest.TestCase):
    def test_complete_utf8_report_survives_cleanup(self):
        program = ("import json,pathlib,sys; p=pathlib.Path(sys.argv[-1]); "
                   "p.write_text(json.dumps({'label':'LG 모니터','workspace':str(p.parent)},ensure_ascii=False),encoding='utf-8')")
        with supervise({}, kind='ddc', timeout=3, command=[sys.executable,'-c',program]) as child:
            self.assertIsNone(child.failure)
            self.assertEqual(child.payload['label'], 'LG 모니터')
            directory = Path(child.payload['workspace'])
            self.assertTrue(directory.is_dir())
        self.assertFalse(directory.exists())

    def test_malformed_report_is_an_explicit_failure(self):
        program = "import pathlib,sys;pathlib.Path(sys.argv[-1]).write_text('{',encoding='utf-8')"
        with supervise({}, kind='ddc', timeout=3, command=[sys.executable,'-c',program]) as child:
            self.assertIsNone(child.payload)
            self.assertIn('valid report', child.failure)

    def test_nonzero_exit_preserves_the_partial_journal(self):
        program = ("import json,pathlib,sys;pathlib.Path(sys.argv[-1]).write_text("
                   "json.dumps({'status':'uncertain','write_attempted':True}),encoding='utf-8');sys.exit(7)")
        with supervise({}, kind='nvidia', timeout=3, command=[sys.executable,'-c',program]) as child:
            self.assertEqual(child.payload, {'status':'uncertain','write_attempted':True})
            self.assertIn('exited 7', child.failure)

    def test_frozen_execution_selects_the_native_worker_dispatch(self):
        commands = []
        class Completed:
            returncode = 0
            def communicate(self, timeout):return '', ''
            def poll(self):return 0
        def launch(command, **kwargs):
            commands.append(command)
            Path(command[-1]).write_text(json.dumps({'status':'sent'}),encoding='utf-8')
            return Completed()
        with patch.object(sys,'frozen',True,create=True), patch('subprocess.Popen',side_effect=launch):
            with supervise({},kind='nvidia',timeout=3) as child:
                self.assertIsNone(child.failure)
        self.assertEqual(commands[0][:2], [sys.executable,'--nvidia-worker'])
