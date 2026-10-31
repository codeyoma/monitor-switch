"""Run hardware-free tests with a diagnostic deadline on hosted runners."""
import faulthandler
import unittest
import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
os.chdir(root)
sys.path.insert(0, str(root))
faulthandler.dump_traceback_later(300, exit=True)
unittest.main(module=None, argv=['unittest', 'discover', '-s', 'tests', '-v'])
