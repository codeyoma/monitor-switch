"""Signed GitHub releases, bounded downloads, and safe update staging.

This module never changes the installation or controls hardware. The independent
installer runs only after the resident has stopped its native workers.
"""
import base64
import hashlib
import json
import os
import platform
import re
import shutil
import ssl
import stat
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import certifi
from pathlib import Path, PurePosixPath

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .startup import data_directory, write_json
from .version import REPOSITORY, UPDATE_PUBLIC_KEY, VERSION

API = f'https://api.github.com/repos/{REPOSITORY}/releases/latest'
MAX_ARCHIVE = 512 * 1024 * 1024
MAX_EXPANDED = 2 * 1024 * 1024 * 1024
CHECK_INTERVAL_MS = 6 * 60 * 60 * 1000


def version_tuple(value):
    if not isinstance(value, str) or not re.fullmatch(r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)', value):
        raise ValueError('Expected a stable major.minor.patch version.')
    return tuple(map(int, value.split('.')))


def platform_key():
    machine = platform.machine().lower()
    if sys.platform == 'darwin' and machine in ('arm64', 'aarch64'):
        return 'macos-arm64'
    if sys.platform == 'win32' and machine in ('amd64', 'x86_64'):
        return 'windows-x64'
    raise RuntimeError('Automatic updates support Apple Silicon Macs and 64-bit Windows.')


def release_url(version, name):
    version_tuple(version)
    if not re.fullmatch(r'[A-Za-z0-9._-]{1,120}', name):
        raise ValueError('Invalid release asset name.')
    return f'https://github.com/{REPOSITORY}/releases/download/v{version}/{name}'


def allowed_url(url):
    parsed = urllib.parse.urlsplit(url)
    return (parsed.scheme == 'https' and parsed.port in (None, 443)
            and not parsed.username and not parsed.password
            and parsed.hostname in ('api.github.com', 'github.com',
                                    'release-assets.githubusercontent.com', 'objects.githubusercontent.com'))


class ReleaseRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        if not allowed_url(newurl):
            raise ValueError('The release redirected outside GitHub.')
        return super().redirect_request(request, fp, code, message, headers, newurl)


def download(url, limit, destination=None, *, opener=None, cancelled=None):
    """Use the system trust store, a per-read timeout, and a total deadline."""
    if not allowed_url(url):
        raise ValueError('Invalid release URL.')
    opener = opener or urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=certifi.where())), ReleaseRedirect())
    request = urllib.request.Request(url, headers={
        'User-Agent': f'MonitorSwitch/{VERSION}', 'Accept': 'application/vnd.github+json' if url == API else 'application/octet-stream'})
    started, count = time.monotonic(), 0
    blocks = []
    try:
        with opener.open(request, timeout=20) as response:
            if not allowed_url(response.geturl()):
                raise ValueError('Invalid release download host.')
            length = response.headers.get('Content-Length')
            if length and int(length) > limit:
                raise ValueError('The release download is too large.')
            stream = Path(destination).open('wb') if destination else None
            try:
                while True:
                    if cancelled and cancelled.is_set():
                        raise InterruptedError('Update download cancelled.')
                    if time.monotonic() - started > 600:
                        raise TimeoutError('The update download timed out.')
                    block = response.read1(min(256 * 1024, limit - count + 1))
                    if not block:
                        break
                    count += len(block)
                    if count > limit:
                        raise ValueError('The release download is too large.')
                    stream.write(block) if stream else blocks.append(block)
            finally:
                if stream:
                    stream.close()
    except Exception:
        if destination:
            Path(destination).unlink(missing_ok=True)
        raise
    return count if destination else b''.join(blocks)


def verified_manifest(payload, signature, *, public_key=None):
    if len(payload) > 64 * 1024 or len(signature) != 64:
        raise ValueError('Invalid update manifest size.')
    Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key or UPDATE_PUBLIC_KEY, validate=True)).verify(signature, payload)
    value = json.loads(payload)
    if value.get('schema') != 1 or value.get('repository') != REPOSITORY:
        raise ValueError('Unexpected update publisher.')
    version_tuple(value.get('version'))
    if not isinstance(value.get('assets'), dict):
        raise ValueError('Missing update assets.')
    for key, asset in value['assets'].items():
        if (key not in ('macos-arm64', 'windows-x64') or not isinstance(asset, dict)
                or asset.get('name') != f'MonitorSwitch-{value["version"]}-{key}.zip'
                or type(asset.get('size')) is not int or not 0 < asset['size'] <= MAX_ARCHIVE
                or not isinstance(asset.get('sha256'), str) or not re.fullmatch('[0-9a-f]{64}', asset['sha256'])):
            raise ValueError('Invalid signed release asset.')
    return value


def check_release(*, fetch=download, current=VERSION, key=None):
    """Ignore drafts, prereleases, rollbacks, and releases without this platform."""
    try:
        release = json.loads(fetch(API, 1024 * 1024))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    tag = release.get('tag_name', '')
    if release.get('draft') or release.get('prerelease') or not tag.startswith('v'):
        return None
    candidate = tag[1:]
    if version_tuple(candidate) <= version_tuple(current):
        return None
    payload = fetch(release_url(candidate, 'update.json'), 64 * 1024)
    signature = fetch(release_url(candidate, 'update.sig'), 64)
    manifest = verified_manifest(payload, signature)
    if manifest['version'] != candidate:
        raise ValueError('The release version does not match its signature.')
    key = key or platform_key()
    return (manifest, payload, signature, key) if key in manifest['assets'] else None


def verify_archive(path, asset):
    path = Path(path)
    if path.stat().st_size != asset['size']:
        raise ValueError('Incomplete update download.')
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    if digest != asset['sha256']:
        raise ValueError('The update checksum does not match.')


def extract_archive(archive, destination, *, key):
    """Extract only relative regular files/directories and internal Mac symlinks.

    Symlinks are created last; file writes can never follow an archive link.
    Windows archives reject links, alternate data streams, and reserved names.
    """
    destination = Path(destination)
    root = 'Monitor Switch.app' if key == 'macos-arm64' else 'app'
    with zipfile.ZipFile(archive) as source:
        entries = source.infolist()
        if len(entries) > 30000 or sum(e.file_size for e in entries) > MAX_EXPANDED:
            raise ValueError('The expanded update is too large.')
        seen, links = set(), []
        for entry in entries:
            if '\\' in entry.orig_filename or '\x00' in entry.orig_filename:
                raise ValueError('Unsafe raw archive path.')
            name = entry.filename.rstrip('/')
            parts = name.split('/')
            if (not name or '\\' in name or '\x00' in name or parts[0] != root
                    or any(p in ('', '.', '..') or ':' in p for p in parts)):
                raise ValueError('Unsafe archive path.')
            folded = name.casefold()
            if folded in seen:
                raise ValueError('Duplicate archive path.')
            seen.add(folded)
            if key == 'windows-x64' and any(
                    p.endswith((' ', '.')) or p.split('.')[0].upper() in
                    {'CON', 'PRN', 'AUX', 'NUL', *{f'COM{i}' for i in range(1,10)}, *{f'LPT{i}' for i in range(1,10)}} for p in parts):
                raise ValueError('Unsafe Windows archive path.')
            mode = entry.external_attr >> 16
            kind = stat.S_IFMT(mode)
            if kind not in (0, stat.S_IFREG, stat.S_IFDIR, stat.S_IFLNK):
                raise ValueError('Unsupported archive file type.')
            path = destination.joinpath(*parts)
            if stat.S_ISLNK(mode):
                if key != 'macos-arm64' or entry.file_size > 4096:
                    raise ValueError('Unsupported archive symlink.')
                target = source.read(entry).decode('utf-8')
                if '\\' in target or PurePosixPath(target).is_absolute():
                    raise ValueError('Unsafe symlink target.')
                links.append((path, target))
            elif entry.is_dir():
                path.mkdir(parents=True, exist_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                with source.open(entry) as reader, path.open('xb') as writer:
                    shutil.copyfileobj(reader, writer)
                path.chmod(0o755 if mode & 0o111 else 0o644)
        base = (destination/root).resolve()
        for path, target in links:
            # Also rejects a file beneath a link and link cycles once created.
            if path.exists() or any(path in parent.parents for parent, _ in links):
                raise ValueError('Conflicting archive symlinks.')
            path.parent.mkdir(parents=True, exist_ok=True)
            path.symlink_to(target)
        for path, _ in links:
            try:
                path.resolve().relative_to(base)
            except (ValueError, RuntimeError):
                raise ValueError('Archive symlink escapes the application.') from None
    return destination/root


def prepare_release(release, *, data=None, fetch=download, stage=None):
    manifest, payload, signature, key = release
    data = Path(data or data_directory())
    data.mkdir(parents=True, exist_ok=True)
    stage = Path(stage) if stage else Path(tempfile.mkdtemp(prefix='update-', dir=data))
    stage.mkdir(parents=True, exist_ok=True)
    stage.chmod(0o700)
    try:
        asset = manifest['assets'][key]
        archive = stage/'release.zip'
        fetch(release_url(manifest['version'], asset['name']), asset['size'], archive)
        verify_archive(archive, asset)
        extract_archive(archive, stage/'payload', key=key)
        (stage/'update.json').write_bytes(payload)
        (stage/'update.sig').write_bytes(signature)
        return stage
    except Exception:
        shutil.rmtree(stage)
        raise


def fetch_worker(stage):
    """Network work runs out of process so cancellation can always stop it."""
    stage = Path(stage).resolve()
    if stage.parent != data_directory().resolve() or not re.fullmatch(r'update-[A-Za-z0-9_-]+', stage.name):
        raise ValueError('Unexpected update worker directory.')
    try:
        release = check_release()
        prepared = prepare_release(release, stage=stage) if release else None
        result = {'stage': str(prepared) if prepared else None}
    except Exception as exc:
        result = {'error': str(exc) or type(exc).__name__}
    write_json(stage/'result.json', result)


def update_preferences(data=None):
    path = Path(data or data_directory())/'updates.json'
    if not path.exists():
        return {'automatic': True}
    value = json.loads(path.read_text(encoding='utf-8'))
    if type(value.get('automatic')) is not bool:
        raise ValueError('Invalid automatic update preference.')
    return value


def set_automatic(enabled, data=None):
    if type(enabled) is not bool:
        raise ValueError('Invalid update setting.')
    write_json(Path(data or data_directory())/'updates.json', {'automatic': enabled})
