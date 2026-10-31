import sys
import unittest
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
