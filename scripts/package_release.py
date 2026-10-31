"""Create one portable release ZIP while retaining macOS framework symlinks."""
import json
import os
import stat
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from monitor_switch.updates import platform_key
from monitor_switch.version import VERSION


def package(source, destination, root_name):
    source, destination = Path(source), Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for folder, directories, files in os.walk(source, followlinks=False):
            for name in sorted(directories+files):
                path = Path(folder)/name
                relative = path.relative_to(source)
                archive_name = str(Path(root_name)/relative).replace('\\', '/')
                if path.is_symlink():
                    entry = zipfile.ZipInfo(archive_name)
                    entry.create_system = 3
                    entry.external_attr = (stat.S_IFLNK | 0o777) << 16
                    archive.writestr(entry, os.readlink(path))
                elif path.is_file():
                    archive.write(path, archive_name)
    return destination


if __name__ == '__main__':
    key = platform_key()
    folder = 'Monitor Switch.app' if sys.platform == 'darwin' else 'Monitor Switch'
    destination = ROOT/'release-assets'/f'MonitorSwitch-{VERSION}-{key}.zip'
    print(package(ROOT/'dist'/folder, destination, 'Monitor Switch.app' if sys.platform == 'darwin' else 'app'))
