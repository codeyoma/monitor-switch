"""Isolate native drivers from the UI so a blocked DDC call has a deadline."""
import json
import copy
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

from .backends import Monitor, backend
from .core import switch


class DemoBackend:
    @contextmanager
    def snapshot(self):
        yield [Monitor('demo-dell', 'DELL P2425 · demo', '1'),
               Monitor('demo-lg', 'LG ULTRAGEAR · demo', '2')]

    def read_source(self, monitor, mode='standard'):
        return 15

    def set_source(self, monitor, code, mode):
        pass


def execute(request):
    if request['action'] == 'usb-event-check':
        if request.get('demo'):
            raise ValueError('Native USB event checks are unavailable in demo mode.')
        from .usb_events import check_idle
        return {'usb_events':check_idle(request.get('seconds',3))}
    if request['action'] == 'connect-relay':
        if request.get('demo'):
            raise ValueError('Pairing is unavailable in demo mode.')
        from .relay import connect_pairing
        return {'pairing': connect_pairing(request['pairing'])}
    rows = request.get('config', {}).get('monitors', [])
    all_remote = bool(rows) and all(row.get('relay') for row in rows)
    transport = DemoBackend() if request.get('demo') else object() if all_remote else backend()
    if request['action'] == 'switch':
        config = copy.deepcopy(request['config'])
        if request.get('demo'):
            for row in config['monitors']:
                row.pop('relay', None)
        return {'results': switch(config, request['target'], transport, automatic=request.get('automatic',False))}
    if request['action'] not in ('list', 'probe'):
        raise ValueError('Unknown worker action.')
    if all_remote and not request.get('demo'):
        return {'monitors': [Monitor(row['id'], row['label'] + ' · Windows helper', row['id']).public() for row in rows]}
    with transport.snapshot() as monitors:
        if request['action'] == 'probe' and monitors:
            modes = request.get('modes', {})
            remote_ids = {row['id'] for row in request.get('config', {}).get('monitors', []) if row.get('relay')}

            def read(monitor):
                if monitor.id in remote_ids:
                    return  # Standard readback is not the actual picture on this LG.
                if monitor.error:
                    return
                try:
                    monitor.read_mode = modes.get(monitor.id, 'standard')
                    monitor.current = transport.read_source(monitor, monitor.read_mode)
                except Exception as exc:
                    monitor.error = str(exc)

            with ThreadPoolExecutor(max_workers=min(len(monitors), 4)) as pool:
                list(pool.map(read, monitors))
        return {'monitors': [m.public() for m in monitors]}


def worker_main(request_path, result_path):
    try:
        request = json.loads(Path(request_path).read_text(encoding='utf-8'))
        result = execute(request)
    except Exception as exc:
        result = {'error': str(exc)}
    Path(result_path).write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')


def invoke(request, timeout=30, command=None):
    """Use the same bounded child lifetime as the NVIDIA execution adapter."""
    from .child_process import supervise
    with supervise(request, kind='ddc', timeout=timeout, command=command) as child:
        if child.failure:
            if child.timed_out and request.get('action') == 'connect-relay':
                raise RuntimeError('Windows pairing timed out. No input switch was requested.')
            if child.timed_out:
                raise RuntimeError('DDC operation timed out. Input state is unknown. '
                                   'No automatic retry; check both displays. '+child.failure)
            raise RuntimeError(child.failure)
        return child.payload
