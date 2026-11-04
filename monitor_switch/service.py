"""Windows relay and native USB notifications, shared by the tray and console."""
import json
import secrets
import socket
import threading
from pathlib import Path

from .relay import RelayServer, private_host, validate_pairing
from .tailscale import local_hosts, tailscale_host
from .startup import data_directory, write_json
from .switching import SwitchBatch, SwitchBusy
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
        self.batch = None
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
        # Recreate process-local accepted-command history on every start.
        self.batch = SwitchBatch(self.profiles, self._run_native, self.emit, stopping=self.stopping)
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
        try:
            self.batch.send('windows', [p['id'] for p in self.profiles], automatic=True,
                            eligible=lambda:self.auto_enabled.is_set() and self.watcher.current(result))
        except SwitchBusy:
            self.emit('message', message='A switch is in progress. USB action skipped; use manual switching.')

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
        return self.batch.send(target, identities, automatic)

    def _run_native(self, monitors, target):
        runner = self.runner
        if runner is None:
            from scripts.windows_lg_switch_test import run_test
            runner = run_test
        return runner({'monitors':monitors}, target, timeout=15,
                      diagnostics=self.data/'diagnostics',
                      reporter=lambda message:self.emit('message', message=message))

    def manual(self, target):
        if target not in ('mac','windows'):
            raise ValueError('Invalid destination.')
        if self.stopping.is_set():
            raise RuntimeError('The helper is stopping. No command sent.')
        return self.send(target, [p['id'] for p in self.profiles])

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
        if self.batch:
            self.batch.stop()
        if self.watcher_thread and self.watcher_thread.is_alive():
            raise RuntimeError('A worker is still stopping. Please wait before quitting.')
