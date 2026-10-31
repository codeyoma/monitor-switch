import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import windows_lg_switch_test as runner


class HungDriver:
    def __init__(self):
        self.deadlines = []
        self.killed = False

    def communicate(self, timeout):
        self.deadlines.append(timeout)
        raise subprocess.TimeoutExpired('native-driver', timeout)

    def kill(self):
        self.killed = True

    def poll(self):
        return None


class RunnerTests(unittest.TestCase):
    def test_unresponsive_driver_has_bounded_wait_and_saved_uncertain_report(self):
        process = HungDriver()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            native_directory = root / 'native'
            native_directory.mkdir()
            with patch.object(runner, 'ROOT', root), \
                 patch.object(runner.platform, 'platform', return_value='test-platform'), \
                 patch.object(runner.tempfile, 'mkdtemp', return_value=str(native_directory)), \
                 patch.object(runner.subprocess, 'Popen', return_value=process):
                report = runner.run_test({'monitor_id': 'lg-id'}, 'windows', timeout=0.01)
            self.assertTrue(process.killed)
            self.assertEqual(process.deadlines, [0.01, 2])
            self.assertEqual(report['status'], 'uncertain')
            self.assertFalse(report['physical_switch_confirmed'])
            saved = list((root / 'diagnostics').glob('*.json'))
            self.assertEqual(len(saved), 1)
            self.assertEqual(json.loads(saved[0].read_text())['status'], 'uncertain')
            self.assertTrue(native_directory.exists())


if __name__ == '__main__':
    unittest.main()
