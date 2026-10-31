"""Sign an exact release manifest using a repository secret, never a source key."""
import base64
import hashlib
import json
import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from monitor_switch.updates import verified_manifest
from monitor_switch.version import VERSION, REPOSITORY


def main():
    directory = Path(sys.argv[1])
    assets = {}
    for platform in ('macos-arm64', 'windows-x64'):
        path = directory/f'MonitorSwitch-{VERSION}-{platform}.zip'
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        assets[platform] = dict(name=path.name, size=path.stat().st_size, sha256=digest)
    payload = (json.dumps(dict(schema=1, repository=REPOSITORY, version=VERSION, assets=assets),
                          sort_keys=True, separators=(',', ':'))+'\n').encode()
    key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(os.environ['UPDATE_SIGNING_KEY'].strip(), validate=True))
    signature = key.sign(payload)
    verified_manifest(payload, signature)
    (directory/'update.json').write_bytes(payload)
    (directory/'update.sig').write_bytes(signature)


if __name__ == '__main__':
    main()
