import plistlib
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from monitor_switch.startup import LoginStartup, windows_command, AGENT_LABEL


class Registry:
    def __init__(self):
        self.value = None
        self.unrelated = 'preserve'
    def read(self): return self.value
    def write(self,value): self.value = value
    def delete(self): self.value = None


class StartupTests(unittest.TestCase):
    def setUp(self):
        user = patch('monitor_switch.startup.os.getuid', return_value=501, create=True)
        user.start()
        self.addCleanup(user.stop)

    def test_windows_quoted_command_and_toggle_preserve_other_entries(self):
        with tempfile.TemporaryDirectory() as folder:
            registry=Registry()
            command=[r'C:\Program Files\Python\pythonw.exe',r'C:\App Folder\app.py','--background']
            startup=LoginStartup(command,platform='win32',home=folder,appdata=folder,registry=registry)
            startup.ensure_first_run()
            self.assertEqual(registry.value,subprocess.list2cmdline(command))
            self.assertTrue(startup.enabled())
            startup.set_enabled(False)
            startup.ensure_first_run()
            self.assertIsNone(registry.value)
            self.assertEqual(registry.unrelated,'preserve')

    def test_mac_owned_plist_and_load_toggle(self):
        with tempfile.TemporaryDirectory() as folder:
            loaded=[False]
            def run(command,**kwargs):
                action=command[1]
                if action=='print': return subprocess.CompletedProcess(command,0 if loaded[0] else 3,'','')
                loaded[0] = action=='bootstrap'
                return subprocess.CompletedProcess(command,0,'','')
            argv=['/usr/bin/open','-g','-a',str(Path(folder)/'App With Spaces.app'),'--args','--background']
            startup=LoginStartup(argv,platform='darwin',home=folder,runner=run)
            startup.set_enabled(True)
            document=plistlib.loads(startup.agent.read_bytes())
            self.assertEqual(document['ProgramArguments'],argv)
            self.assertEqual(document['Label'],AGENT_LABEL)
            self.assertNotIn('KeepAlive',document)
            self.assertTrue(loaded[0])
            startup.set_enabled(False)
            self.assertFalse(startup.agent.exists())
            self.assertFalse(loaded[0])

    def test_unowned_startup_entries_are_not_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            registry=Registry();registry.value='other command'
            startup=LoginStartup(['pythonw.exe','app.py'],platform='win32',home=folder,appdata=folder,registry=registry)
            with self.assertRaises(ValueError): startup.set_enabled(True)
            self.assertEqual(registry.value,'other command')
            startup=LoginStartup(['open','app'],platform='darwin',home=folder)
            startup.agent.parent.mkdir(parents=True)
            startup.agent.write_bytes(plistlib.dumps({'Label':'other'}))
            with self.assertRaises(ValueError): startup.set_enabled(True)

    def test_long_windows_command_is_rejected(self):
        with self.assertRaises(ValueError): windows_command(['x'*261])

    def test_failed_mac_bootstrap_does_not_report_enabled(self):
        with tempfile.TemporaryDirectory() as folder:
            def run(command,**kwargs):
                return subprocess.CompletedProcess(command,3,'','Registration failed')
            startup=LoginStartup(['open','app'],platform='darwin',home=folder,runner=run)
            with self.assertRaisesRegex(RuntimeError,'Registration failed'):
                startup.ensure_first_run()
            self.assertFalse(startup.enabled())
            self.assertIsNone(startup.preference())
