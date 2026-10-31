"""Windows relay and native USB notifications, shared by the tray and console."""
import json
import secrets
import socket
import threading
from pathlib import Path

from .relay import RelayServer, private_host, validate_pairing
from .tailscale import local_hosts, tailscale_host
from .startup import data_directory, write_json
from .usb import AutomaticRequests
from .usb_events import USBEventWatcher


def network_hosts():
    hosts=set(local_hosts())
    try:
        hosts.update(info[4][0] for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
                     if private_host(info[4][0]) and not info[4][0].startswith('127.'))
    except OSError:
        pass
    return sorted(hosts,key=lambda host:(not tailscale_host(host),host))[:8]


class RelayService:
    def __init__(self, root, notify=lambda event: None, *, data=None, runner=None,
                 watcher_factory=USBEventWatcher, address=('0.0.0.0', 55661)):
        self.root, self.data = Path(root), Path(data or data_directory())
        self.notify, self.runner = notify, runner
        self.watcher_factory, self.address = watcher_factory, address
        self.server = self.server_thread = self.watcher = self.watcher_thread = None
        self.stopping = threading.Event()
        self.auto_enabled = threading.Event()
        self.automatic_requests = AutomaticRequests()
        self.state_path = self.data/'relay-server.json'
        self.mapping_path = self.data/'relay-monitors.json'

    def emit(self, kind, **values):
        self.notify(dict(kind=kind, **values))

    def start(self):
        self.stopping.clear()
        self.state = json.loads(self.state_path.read_text(encoding='utf-8')) if self.state_path.exists() else {}
        if self.mapping_path.exists():
            self.profiles = json.loads(self.mapping_path.read_text(encoding='utf-8'))['monitors']
        elif (self.root/'relay-monitors.json').exists():
            self.profiles = json.loads((self.root/'relay-monitors.json').read_text(encoding='utf-8'))['monitors']
            write_json(self.mapping_path, {'monitors': self.profiles})
        elif self.state.get('profiles'):
            self.profiles = self.state['profiles']
            write_json(self.mapping_path, {'monitors': self.profiles})
        else:
            write_json(self.mapping_path, {'monitors': []})
            raise ValueError('Set Dell and LG identities in the input mapping file, then restart Monitor Switch. Existing settings are preserved.')
        if self.state.get('profiles') != self.profiles:
            self.state = dict(server_id=secrets.token_hex(16), key=secrets.token_hex(32),
                              profiles=self.profiles, usb_auto=self.state.get('usb_auto', True))
        if type(self.state.get('usb_auto', True)) is not bool:
            raise ValueError('Invalid saved USB automatic setting; existing file preserved.')
        # The listener can start before Wi-Fi is ready. No network polling is needed.
        self.pairing = dict(version=1, hosts=network_hosts() or ['127.0.0.1'], port=self.address[1],
                            monitors=self.profiles, monitor_id=next(p['id'] for p in self.profiles if p['role']=='lg'),
                            server_id=self.state['server_id'], key=self.state['key'])
        validate_pairing(self.pairing)
        self.server = RelayServer(self.address, self.pairing, self.send)
        try:
            write_json(self.state_path, self.state)
            self.server_thread = threading.Thread(target=self.server.serve_forever, name='Monitor relay', daemon=True)
            self.server_thread.start()
            if self.state.get('usb_auto', True):
                self.auto_enabled.set()
            self.start_watcher()
            self.emit('ready', message='Windows relay ready · port 55661')
        except Exception:
            self.stop()
            raise
        return self

    def start_watcher(self):
        watcher = self.watcher_factory(self.observed_usb, enabled=self.auto_enabled.is_set())
        self.watcher = watcher
        self.watcher_thread = threading.Thread(target=watcher.run, name='USB native notifications', daemon=True)
        self.watcher_thread.start()

    def observed_usb(self, result):
        self.emit('usb', result=result)
        if (self.stopping.is_set() or not result.get('trigger') or not self.auto_enabled.is_set()
                or not self.watcher.current(result)):
            return
        if not self.server.operation.acquire(blocking=False):
            self.emit('message', message='A switch is in progress. USB action skipped; use manual switching.')
            return
        try:
            if not self.stopping.is_set() and self.auto_enabled.is_set() and self.watcher.current(result):
                self.send('windows', [p['id'] for p in self.profiles], automatic=True)
        finally:
            self.server.operation.release()

    def set_auto(self, enabled):
        if self.stopping.is_set():
            return
        next_state = dict(self.state, usb_auto=bool(enabled))
        write_json(self.state_path, next_state)
        self.state = next_state
        self.auto_enabled.set() if enabled else self.auto_enabled.clear()
        if enabled and not self.watcher_thread.is_alive():
            self.start_watcher()
        else:
            self.watcher.set_enabled(bool(enabled))
        self.emit('automatic', enabled=bool(enabled))

    def send(self, target, identities, automatic=False):
        # Callers hold server.operation for the complete batch, including deduplication.
        requested = [p for p in self.profiles if p['id'] in identities]
        if self.stopping.is_set():
            return {'results':[dict(id=p['id'],status='unavailable',write_attempted=False,error='Helper is stopping.')
                               for p in requested]}
        if not self.automatic_requests.allow(target, identities, automatic):
            result = {'results':[dict(id=p['id'],status='skipped',write_attempted=False,
                                      error='Duplicate USB arrival; no command sent.') for p in requested]}
            self.emit('result', target=target, result=result)
            return result
        self.emit('busy', target=target)
        try:
            runner = self.runner
            if runner is None:
                from scripts.windows_lg_switch_test import run_test
                runner = run_test
            result = runner({'monitors':requested}, target, timeout=15,
                            diagnostics=self.data/'diagnostics',
                            reporter=lambda message:self.emit('message',message=message))
        except Exception as exc:
            result = {'error':str(exc)}
        if 'results' not in result:
            result['results'] = [dict(id=p['id'],status='uncertain',error=result.get('error','Worker failed.'))
                                 for p in requested]
        self.emit('result', target=target, result=result)
        return result

    def manual(self, target):
        if target not in ('mac','windows'):
            raise ValueError('Invalid destination.')
        if self.stopping.is_set() or not self.server.operation.acquire(blocking=False):
            raise RuntimeError('A switch is already in progress, or the helper is stopping.')
        try:
            return self.send(target, [p['id'] for p in self.profiles])
        finally:
            self.server.operation.release()

    def export_pairing(self, path):
        hosts = network_hosts()
        if not hosts:
            raise RuntimeError('Connect Windows to Tailscale or your private network, then export the connection again.')
        pairing = dict(self.pairing, hosts=hosts)
        validate_pairing(pairing)
        write_json(Path(path), pairing)

    def stop(self):
        self.stopping.set()
        self.auto_enabled.clear()
        if self.watcher:
            self.watcher.stop()
        if self.server:
            if self.server_thread and self.server_thread.is_alive():
                self.server.shutdown()
                self.server_thread.join(2)
            self.server.server_close()
        if self.watcher_thread:
            self.watcher_thread.join(20)
        if self.server:
            acquired = self.server.operation.acquire(timeout=20)
            if acquired:
                self.server.operation.release()
            if not acquired or (self.watcher_thread and self.watcher_thread.is_alive()):
                raise RuntimeError('A worker is still stopping. Please wait before quitting.')
