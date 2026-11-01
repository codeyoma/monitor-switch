"""Authenticated LAN/Tailscale relay for the two configured monitor profiles."""
import hashlib
import hmac
import http.client
import json
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from .tailscale import relay_host, tailscale_host, windows_peer_hosts

MAX_BODY = 4096


class RelayFailure(RuntimeError):
    def __init__(self, message, status='uncertain'):
        super().__init__(message)
        self.status = status


def private_host(host):
    return relay_host(host)


def validate_pairing(pairing, selected=False):
    if not isinstance(pairing, dict) or pairing.get('version') != 1:
        raise ValueError('Invalid Windows pairing file version.')
    for name, length in [('key', 64), ('server_id', 32)]:
        if not isinstance(pairing.get(name), str) or not re.fullmatch('[0-9a-f]{' + str(length) + '}', pairing[name]):
            raise ValueError('Invalid Windows pairing credentials.')
    if type(pairing.get('port')) is not int or not 1024 <= pairing['port'] <= 65535:
        raise ValueError('Invalid Windows relay port.')
    identity = pairing.get('monitor_id')
    if not isinstance(identity, str) or not 1 <= len(identity) <= 256:
        raise ValueError('Invalid remote LG identity.')
    hosts = [pairing.get('host')] if selected else pairing.get('hosts')
    if not isinstance(hosts, list) or not 1 <= len(hosts) <= 8 or not all(private_host(h) for h in hosts):
        raise ValueError('Pairing requires private LAN or Tailscale IPv4 addresses.')
    profiles = pairing.get('monitors')
    if not isinstance(profiles, list) or len(profiles) != 2:
        raise ValueError('The Windows connection must describe Dell and LG separately.')
    if {p.get('role') for p in profiles if isinstance(p, dict)} != {'dell', 'lg'}:
        raise ValueError('The Windows connection needs one Dell and one LG profile.')
    identities = []
    for profile in profiles:
        if (not isinstance(profile.get('id'), str) or not profile['id'] or len(profile['id']) > 256
                or not isinstance(profile.get('label'), str) or not profile['label']
                or profile.get('mode') not in ('standard', 'lg-alt')
                or any(type(profile.get(host)) is not int or not 1 <= profile[host] <= 65535 for host in ('mac', 'windows'))):
            raise ValueError('Invalid configured Windows monitor profile.')
        identities.append(profile['id'].casefold())
    if len(set(identities)) != 2 or pairing['monitor_id'].casefold() not in identities:
        raise ValueError('Invalid or duplicated remote monitor identities.')
    by_role = {profile['role']:profile for profile in profiles}
    if (by_role['dell']['mode'] != 'standard' or by_role['lg']['mode'] != 'lg-alt'
            or pairing['monitor_id'] != by_role['lg']['id']):
        raise ValueError('Dell needs standard DDC and LG needs alternate DDC with its exact identity.')


def signature(pairing, *parts):
    message = '\n'.join([pairing['server_id'], *parts]).encode('ascii')
    return hmac.new(bytes.fromhex(pairing['key']), message, hashlib.sha256).hexdigest()


def signed_headers(pairing, method, path, body, epoch=''):
    timestamp, nonce = str(int(time.time())), secrets.token_hex(16)
    return {'Content-Type': 'application/json', 'X-MS-Time': timestamp, 'X-MS-Nonce': nonce, 'X-MS-Epoch': epoch,
            'X-MS-Signature': signature(pairing, method, path, timestamp, nonce, epoch, hashlib.sha256(body).hexdigest())}


def response_signature(pairing, nonce, status, body):
    return signature(pairing, 'response', nonce, str(status), hashlib.sha256(body).hexdigest())


def request(pairing, method, path, data=None, timeout=20):
    validate_pairing(pairing, selected=True)
    body = b'' if data is None else json.dumps(data, separators=(',', ':')).encode('utf-8')
    # A fresh server instance has a different epoch. Old write packets cannot be
    # replayed after a helper restart, even though the pairing key is persistent.
    epoch = ''
    if method == 'POST':
        try:
            preflight = request(pairing, 'GET', '/status', timeout=2)
            epoch = preflight.get('epoch', '')
            if preflight.get('monitors') != pairing['monitors'] or not re.fullmatch('[0-9a-f]{32}', epoch):
                raise RuntimeError('Windows mappings changed; import the new connection file.')
        except RuntimeError as exc:
            raise RelayFailure(str(exc), status='unavailable') from exc
    headers = signed_headers(pairing, method, path, body, epoch)
    connection = http.client.HTTPConnection(pairing['host'], pairing['port'], timeout=timeout)
    try:
        # HTTPConnection uses no proxy, redirect, discovery or write retry.
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        payload = response.read(MAX_BODY + 1)
        if len(payload) > MAX_BODY:
            raise RuntimeError('Windows reply is too large; input state is unknown.')
        actual = response.getheader('X-MS-Signature', '')
        expected = response_signature(pairing, headers['X-MS-Nonce'], response.status, payload)
        if not re.fullmatch('[0-9a-f]{64}', actual) or not hmac.compare_digest(actual, expected):
            raise RelayFailure('Windows reply authentication failed. Check the pairing file and automatic clock sync '
                               'on both computers. No automatic retry.')
        result = json.loads(payload)
        if response.status != 200:
            raise RelayFailure(result.get('error', 'Windows relay rejected the request.'),
                               status='unavailable' if response.status in (400,409) else 'uncertain')
        if result.get('monitor_id') != pairing['monitor_id']:
            raise RuntimeError('Windows LG identity changed; re-pair before switching.')
        return result
    except (OSError, http.client.HTTPException, ValueError) as exc:
        network=('Tailscale on both computers and Windows Firewall TCP access to the helper'
                 if tailscale_host(pairing['host']) else 'the LAN connection and Private firewall access')
        raise RelayFailure('Windows relay is unreachable or returned an invalid reply. '
                           'Keep Windows awake and the helper running; check '+network+'. '
                           'Input state may be unknown. No automatic retry.') from exc
    finally:
        connection.close()


def connect_pairing(pairing):
    validate_pairing(pairing)
    # Discovery is read-only and credentials/server identity must match before
    # saving an endpoint. Never use discovered peers as switch destinations
    # without this check, and never retry a switch on another endpoint.
    deadline = time.monotonic() + 10
    candidates=list(dict.fromkeys([*pairing['hosts'],*windows_peer_hosts()]))
    candidates.sort(key=lambda host:0 if tailscale_host(host) else 1)
    for host in candidates:
        if time.monotonic() >= deadline:
            break
        selected = dict(pairing, host=host, hosts=[host])
        try:
            status = request(selected, 'GET', '/status', timeout=min(1, deadline-time.monotonic()))
            if status.get('server_id') == pairing['server_id'] and status.get('monitors') == pairing['monitors']:
                return selected
        except RuntimeError:
            pass
    if any(tailscale_host(host) for host in candidates):
        raise RuntimeError('Cannot connect to the paired Windows helper over Tailscale. '
                           'Update the Windows app, keep both computers online in the same tailnet, '
                           'and allow the helper TCP port in Windows Firewall. Then import the connection file again.')
    raise RuntimeError('Cannot connect to the paired Windows helper. Connect both computers to Tailscale '
                       'or the same LAN, keep the Windows app running, then import the pairing file again.')


class RelayServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False
    allow_reuse_address = False

    def __init__(self, address, pairing, send):
        self.pairing, self.send = dict(pairing), send
        self.epoch = secrets.token_hex(16)
        self.operation = threading.Lock()
        self.nonce_lock, self.nonces = threading.Lock(), {}
        self.slots = threading.BoundedSemaphore(8)
        self.sign_reply = response_signature
        super().__init__(address, RelayHandler)

    def process_request(self, request_socket, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request_socket)
            return
        try:
            super().process_request(request_socket, client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request_socket, client_address):
        try:
            super().process_request_thread(request_socket, client_address)
        finally:
            self.slots.release()

    def authenticate(self, handler, body):
        names = ('X-MS-Time', 'X-MS-Nonce', 'X-MS-Signature', 'X-MS-Epoch')
        if any(len(handler.headers.get_all(name, [])) != 1 for name in names):
            return False
        timestamp, nonce, actual, epoch = [handler.headers.get(name, '') for name in names]
        if handler.command == 'POST' and epoch != self.epoch:
            return False
        if handler.command == 'GET' and epoch:
            return False
        if not re.fullmatch('[0-9]{1,12}', timestamp) or abs(time.time() - int(timestamp)) > 90:
            return False
        if not re.fullmatch('[0-9a-f]{32}', nonce) or not re.fullmatch('[0-9a-f]{64}', actual):
            return False
        expected = signature(self.pairing, handler.command, handler.path, timestamp, nonce, epoch,
                             hashlib.sha256(body).hexdigest())
        if not hmac.compare_digest(actual, expected):
            return False
        with self.nonce_lock:
            now = time.time()
            self.nonces = {n: expiry for n, expiry in self.nonces.items() if expiry > now}
            if nonce in self.nonces or len(self.nonces) >= 4096:
                return False
            self.nonces[nonce] = now + 180
        return True


class RelayHandler(BaseHTTPRequestHandler):
    server_version = 'MonitorSwitch'
    sys_version = ''

    def setup(self):
        super().setup()
        self.connection.settimeout(2)

    def log_message(self, format, *args):
        pass  # No credentials, headers or request bodies are logged.

    def do_GET(self):
        self.dispatch()

    def do_POST(self):
        self.dispatch()

    def reply(self, status, result, authenticated=True):
        payload = json.dumps(result, separators=(',', ':')).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Connection', 'close')
        if authenticated:
            self.send_header('X-MS-Signature', self.server.sign_reply(
                self.server.pairing, self.headers.get('X-MS-Nonce', ''), status, payload))
        self.end_headers()
        self.wfile.write(payload)
        self.close_connection = True

    def dispatch(self):
        lengths = self.headers.get_all('Content-Length', [])
        if (not private_host(self.client_address[0]) or self.headers.get('Transfer-Encoding')
                or len(lengths) > 1 or not re.fullmatch('[0-9]{1,5}', lengths[0] if lengths else '0')):
            self.reply(400, {'error': 'Invalid request.'}, False)
            return
        length = int(lengths[0]) if lengths else 0
        if length > MAX_BODY:
            self.reply(413, {'error': 'Request too large.'}, False)
            return
        body = self.rfile.read(length)
        if len(body) != length or not self.server.authenticate(self, body):
            self.reply(403, {'error': 'Pairing authentication failed or request expired.'}, False)
            return
        pairing = self.server.pairing
        if self.command == 'GET' and self.path == '/status' and not body:
            self.reply(200, {'server_id': pairing['server_id'], 'monitor_id': pairing['monitor_id'],
                             'monitors': pairing['monitors'], 'epoch':self.server.epoch,
                             'status': 'ready', 'physical_switch_confirmed': False})
            return
        try:
            data = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            data = None
        if (self.command != 'POST' or self.path != '/switch' or not isinstance(data, dict)
                or set(data) not in ({'target', 'monitors'}, {'target','monitors','automatic'})
                or type(data.get('automatic',False)) is not bool or data['target'] not in ('mac', 'windows')
                or not isinstance(data['monitors'], list) or not 1 <= len(data['monitors']) <= 2
                or not all(isinstance(identity, str) for identity in data['monitors'])
                or len(set(data['monitors'])) != len(data['monitors'])
                or not set(data['monitors']) <= {profile['id'] for profile in pairing['monitors']}):
            self.reply(400, {'error': 'Only configured monitor identities and the mac/windows destination are accepted.'})
            return
        if not self.server.operation.acquire(blocking=False):
            self.reply(409, {'error': 'A switch is already in progress. No command sent.'})
            return
        try:
            try:
                result = self.server.send(data['target'], data['monitors'], automatic=data.get('automatic',False))
                if (not isinstance(result.get('results'), list)
                        or len(result['results']) != len(data['monitors'])
                        or {r.get('id') for r in result['results']} != set(data['monitors'])
                        or any(r.get('status') not in ('sent', 'uncertain', 'unavailable', 'skipped') for r in result['results'])):
                    raise RuntimeError('Invalid native result.')
            except Exception:
                result = {'results': [dict(id=identity, status='uncertain', error='Native worker failed; no automatic retry.')
                                      for identity in data['monitors']]}
            # Never expose the pairing secret or accept an arbitrary feature/value.
            public = {'results': [{name: row[name] for name in ('id', 'status', 'error', 'message', 'write_attempted') if name in row}
                                  for row in result['results']]}
            public.update(monitor_id=pairing['monitor_id'], physical_switch_confirmed=False)
            self.reply(200, public)
        finally:
            self.server.operation.release()
