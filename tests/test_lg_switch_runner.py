import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from monitor_switch import nvidia_worker as runner


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
            with patch.object(runner.platform, 'platform', return_value='test-platform'), \
                 patch('tempfile.mkdtemp', return_value=str(native_directory)), \
                 patch('subprocess.Popen', return_value=process):
                report = runner.run_switch({'monitor_id': 'lg-id'}, 'windows', timeout=0.01, diagnostics=root/'diagnostics')
            self.assertTrue(process.killed)
            self.assertEqual(process.deadlines, [0.01, 2])
            self.assertEqual(report['status'], 'uncertain')
            self.assertFalse(report['physical_switch_confirmed'])
            saved = list((root / 'diagnostics').glob('*.json'))
            self.assertEqual(len(saved), 1)
            self.assertEqual(json.loads(saved[0].read_text())['status'], 'uncertain')
            self.assertTrue(native_directory.exists())

    def test_partial_journal_is_saved_even_when_termination_fails(self):
        process = HungDriver()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            native_directory = root/'native';native_directory.mkdir()
            def launch(command, **kwargs):
                Path(command[-1]).write_text(json.dumps({'results':[
                    dict(id='lg-id',status='uncertain',write_attempted=True)]}), encoding='utf-8')
                return process
            with patch.object(runner.platform, 'platform', return_value='test-platform'), \
                    patch('tempfile.mkdtemp',return_value=str(native_directory)), \
                    patch('subprocess.Popen',side_effect=launch):
                report = runner.run_switch({'monitors':[dict(id='lg-id')]}, 'mac',
                                           timeout=0.01, diagnostics=root/'diagnostics')
            self.assertTrue(report['results'][0]['write_attempted'])
            self.assertEqual(report['results'][0]['status'], 'uncertain')
            self.assertIn('2-second deadline', report['error'])
            self.assertFalse(report['physical_switch_confirmed'])
            saved = json.loads(Path(report['report_path']).read_text(encoding='utf-8'))
            self.assertEqual(saved['results'], report['results'])
            self.assertTrue((native_directory/'result.json').is_file())

    def test_console_presents_partial_results_and_worker_failure(self):
        from scripts import windows_relay
        with patch('builtins.print') as output:
            windows_relay.observed(dict(kind='result', target='mac', result={
                'results':[dict(id='lg-id',status='uncertain',error='Native reply missing')],
                'error':'Worker did not exit within the additional 2-second deadline.'}))
        text = ' '.join(str(value) for call in output.call_args_list for value in call.args)
        self.assertIn('lg-id: uncertain', text)
        self.assertIn('Native reply missing', text)
        self.assertIn('2-second deadline', text)


if __name__ == '__main__':
    unittest.main()
