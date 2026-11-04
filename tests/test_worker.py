import sys
import unittest
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch
from monitor_switch.worker import invoke
from test_core import configured


class WorkerTests(unittest.TestCase):
    def test_demo_worker_round_trip_has_no_hardware_dependency(self):
        reply = invoke({'action':'probe', 'demo':True})
        self.assertEqual([r['current'] for r in reply['monitors']], [15,15])
        self.assertTrue(all(r['read_mode'] == 'standard' for r in reply['monitors']))

    def test_demo_switch_and_invalid_request_report_results(self):
        c = configured()
        for row, identity in zip(c['monitors'], ['demo-dell','demo-lg']):
            row['id'] = identity
        reply = invoke({'action':'switch','demo':True,'config':c,'target':'windows'})
        self.assertEqual([r['status'] for r in reply['results']], ['sent','sent'])
        self.assertIn('error', invoke({'action':'unknown','demo':True}))

    def test_stuck_child_is_terminated_at_deadline(self):
        with self.assertRaisesRegex(RuntimeError, 'timed out'):
            invoke({}, timeout=0.1, command=[sys.executable, '-c', 'import time; time.sleep(60)'])

    def test_failed_termination_does_not_wait_without_another_deadline(self):
        class Unresponsive:
            pid = 2147483646
            def wait(self, timeout=None):
                if timeout is None:raise AssertionError('Unbounded wait after termination.')
                raise subprocess.TimeoutExpired('driver', timeout)
            def communicate(self, timeout):
                raise subprocess.TimeoutExpired('driver', timeout)
            def kill(self):pass
            def poll(self):return None
        with tempfile.TemporaryDirectory() as directory:
            native = Path(directory)/'native';native.mkdir()
            with patch('tempfile.mkdtemp', return_value=str(native)), \
                    patch('subprocess.Popen', return_value=Unresponsive()), \
                    patch('os.killpg', create=True):
                with self.assertRaisesRegex(RuntimeError, '2-second deadline'):
                    invoke({}, timeout=0.01)
