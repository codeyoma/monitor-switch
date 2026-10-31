"""Run the frozen worker path with simulated monitors, never real hardware."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
executable = ROOT/('dist/Monitor Switch.app/Contents/MacOS/Monitor Switch' if sys.platform == 'darwin'
                   else 'dist/Monitor Switch/Monitor Switch.exe')
subprocess.run([str(executable), '--list', '--demo'], check=True, timeout=45)
