"""Configuration and switching policy, independent of the GUI and native APIs."""
import json
import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, Future
from contextlib import ExitStack
import threading
from pathlib import Path


def config_path():
    if sys.platform == 'darwin':
        base = Path.home() / 'Library/Application Support'
    else:
        base = Path(os.environ.get('LOCALAPPDATA', Path.home() / '.config'))
    return base / 'MonitorSwitch' / 'config.json'


def default_config():
    return {'version': 1, 'monitors': [
        {'label': name, 'id': '', 'enabled': False, 'mode': 'standard',
         'mac': None, 'windows': None}
        for name in ('Dell P2425', 'LG UltraGear')
    ]}


def validate_config(config, require_enabled=False):
    if not isinstance(config, dict) or config.get('version') != 1:
        raise ValueError('Unsupported configuration version.')
    if 'usb_auto' in config and type(config['usb_auto']) is not bool:
        raise ValueError('Invalid USB automatic-switch setting.')
    rows = config.get('monitors')
    if not isinstance(rows, list) or len(rows) != 2:
        raise ValueError('Configuration must contain exactly two monitor slots.')
    seen, remote_seen = set(), set()
    count = 0
    for row in rows:
        if not isinstance(row, dict) or type(row.get('enabled')) is not bool:
            raise ValueError('Each monitor needs an enabled flag.')
        if not isinstance(row.get('label'), str) or not row['label'].strip():
            raise ValueError('Each monitor needs a label.')
        if not isinstance(row.get('id'), str):
            raise ValueError('Invalid monitor identifier.')
        if row.get('mode') not in ('standard', 'lg-alt'):
            raise ValueError('Unknown input protocol.')
        if row.get('relay'):
            from .relay import validate_pairing
            validate_pairing(row['relay'], selected=True)
            profile = next((p for p in row['relay']['monitors'] if p['id'] == row['relay'].get('remote_id')), None)
            if profile is None or any(row[key] != profile[key] for key in ('mode', 'mac', 'windows')):
                raise ValueError('Mappings must match the paired Windows monitor profile. Import the connection again.')
        if not row['enabled']:
            continue
        count += 1
        if row.get('relay'):
            remote = (row['relay']['server_id'], row['relay']['remote_id'].casefold())
            if remote in remote_seen:
                raise ValueError('The same remote monitor cannot occupy both slots.')
            remote_seen.add(remote)
        if not row['id']:
            raise ValueError(f"{row['label']}: choose a detected monitor.")
        if row['id'] in seen:
            raise ValueError('The same monitor cannot occupy both slots.')
        seen.add(row['id'])
        for host in ('mac', 'windows'):
            code = row.get(host)
            if type(code) is not int or not 1 <= code <= 65535:
                raise ValueError(f"{row['label']}: select a {host} input code (1–65535).")
    if require_enabled and not count:
        raise ValueError('Enable at least one monitor and save its input mappings.')


def load_config(path=None):
    path = path or config_path()
    if not path.exists():
        return default_config()
    try:
        config = json.loads(path.read_text(encoding='utf-8'))
        validate_config(config)
        return config
    except (ValueError, OSError) as exc:
        raise ValueError(f'Cannot load {path}: {exc}') from exc


def save_config(config, path=None):
    validate_config(config)
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         delete=False) as stream:
            name = stream.name
            json.dump(config, stream, indent=2, ensure_ascii=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def switch(config, target, backend, automatic=False):
    if target not in ('mac', 'windows'):
        raise ValueError('Target must be mac or windows.')
    validate_config(config, require_enabled=True)
    rows = [r for r in config['monitors'] if r['enabled']]
    relay_groups, relay_jobs, relay_lock = {}, {}, threading.Lock()

    def relay_key(pairing):
        return (pairing['host'], pairing['port'], pairing['server_id'], pairing['key'])

    for row in rows:
        if row.get('relay'):
            relay_groups.setdefault(relay_key(row['relay']), []).append(row['relay']['remote_id'])

    def relay_send(row):
        from .relay import request
        key = relay_key(row['relay'])
        with relay_lock:
            leader = key not in relay_jobs
            if leader:
                relay_jobs[key] = Future()
            future = relay_jobs[key]
        if leader:
            try:
                payload = {'target': target, 'monitors': relay_groups[key]}
                if automatic:
                    payload['automatic'] = True
                future.set_result(request(row['relay'], 'POST', '/switch', payload))
            except Exception as exc:
                future.set_exception(exc)
        replies = [r for r in future.result().get('results', []) if r.get('id') == row['relay']['remote_id']]
        if len(replies) != 1:
            raise RuntimeError('Missing or ambiguous Windows monitor result; no automatic retry.')
        return replies[0]
    # Resolve every identity and acquire all native handles before the first write.
    with ExitStack() as stack:
        monitors, local_error = [], None
        if any(not row.get('relay') for row in rows):
            try:
                monitors = stack.enter_context(backend.snapshot())
            except Exception as exc:
                local_error = str(exc)
        by_id = {}
        for monitor in monitors:
            by_id.setdefault(monitor.id, []).append(monitor)

        def send(row):
            result = {'label': row['label'], 'id': row['id'], 'code': row[target]}
            if row.get('relay'):
                try:
                    reply = relay_send(row)
                    if reply.get('status') not in ('sent', 'uncertain', 'unavailable', 'skipped'):
                        raise RuntimeError('Windows returned an invalid switch result.')
                    return dict(result, status=reply['status'], message=reply.get('message') or reply.get('error') or (
                                'Duplicate request skipped; no input command sent.' if reply['status']=='skipped' else
                                'Windows command sent; check the actual monitor picture.'))
                except Exception as exc:
                    return dict(result, status=getattr(exc, 'status', 'uncertain'), message=str(exc))
            matches = by_id.get(row['id'], [])
            if len(matches) != 1:
                return dict(result, status='unavailable', message=(
                    (local_error + ' · ') if local_error else '') + (
                    'Monitor missing or identity ambiguous. No command sent. '
                    'Run this app on the active computer, or use the monitor OSD.'))
            try:
                backend.set_source(matches[0], row[target], row['mode'])
                return dict(result, status='sent', message=(
                    'Command sent; the new input is not verified.'))
            except Exception as exc:
                # A failed write can still have changed inputs. Never retry blindly.
                return dict(result, status='uncertain', message=str(exc))

        with ThreadPoolExecutor(max_workers=len(rows)) as pool:
            return list(pool.map(send, rows))
