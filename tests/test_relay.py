import copy
import http.client
import json
import secrets
import threading
import unittest
from unittest.mock import patch

from monitor_switch.relay import RelayServer, connect_pairing, request, signed_headers, private_host, validate_pairing
from monitor_switch.core import default_config, switch, validate_config


class RelayTests(unittest.TestCase):
    def setUp(self):
        discovery=patch('monitor_switch.relay.windows_peer_hosts',return_value=[])
        discovery.start();self.addCleanup(discovery.stop)
        self.sent = []
        self.batches = []
        self.pairing = {'version': 1, 'hosts': ['127.0.0.1'], 'port': 1,
                        'server_id': secrets.token_hex(16), 'key': secrets.token_hex(32),
                        'monitor_id': 'exact-windows-lg', 'monitors': [
                            dict(role='dell', id='exact-windows-dell', label='Dell', mode='standard', mac=17, windows=15),
                            dict(role='lg', id='exact-windows-lg', label='LG', mode='lg-alt', mac=144, windows=208)]}

        def send(target, identities, automatic=False):
            self.sent.append(target)
            self.batches.append(list(identities))
            return {'results': [dict(id=identity, status='sent', physical_switch_confirmed=False) for identity in identities]}

        self.server = RelayServer(('127.0.0.1', 0), self.pairing, send)
        self.pairing['port'] = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def test_read_only_pairing_then_one_explicit_switch(self):
        paired = connect_pairing(self.pairing)
        self.assertEqual(self.sent, [])
        self.assertEqual(paired['host'], '127.0.0.1')
        self.assertEqual(request(paired, 'POST', '/switch', {'target': 'windows', 'monitors':['exact-windows-lg']})['results'][0]['status'], 'sent')
        self.assertEqual(self.sent, ['windows'])

    def test_tailscale_addresses_are_allowed_and_public_addresses_are_rejected(self):
        for host in ('100.64.0.1','100.70.2.3','100.127.255.254','192.168.1.2','127.0.0.1'):
            with self.subTest(host=host):self.assertTrue(private_host(host))
        for host in ('100.63.255.255','100.128.0.1','8.8.8.8','0.0.0.0','169.254.1.2','224.0.0.1'):
            with self.subTest(host=host):self.assertFalse(private_host(host))
        validate_pairing(self.pairing | {'hosts':['100.70.2.3']})

    def test_tailscale_client_reaches_read_only_authenticated_status(self):
        from monitor_switch.relay import RelayHandler
        original=RelayHandler.dispatch
        def from_tailnet(handler):
            handler.client_address=('100.100.10.20',handler.client_address[1])
            return original(handler)
        with patch.object(RelayHandler,'dispatch',from_tailnet):
            result=request(self.pairing | {'host':'127.0.0.1'},'GET','/status')
        self.assertEqual(result['server_id'],self.pairing['server_id'])
        self.assertEqual(self.sent,[])

    def test_existing_lan_pairing_can_select_authenticated_tailscale_peer_without_switching(self):
        status=dict(server_id=self.pairing['server_id'],monitors=self.pairing['monitors'])
        with patch('monitor_switch.relay.windows_peer_hosts',return_value=['100.70.2.3']), \
                patch('monitor_switch.relay.request',return_value=status) as exchange:
            selected=connect_pairing(self.pairing | {'hosts':['192.168.0.2']})
        self.assertEqual(selected['host'],'100.70.2.3')
        self.assertEqual(exchange.call_args.args[1:],('GET','/status'))
        self.assertEqual(self.sent,[])

    def test_discovered_tailscale_peer_with_wrong_identity_is_not_paired(self):
        with patch('monitor_switch.relay.windows_peer_hosts',return_value=['100.70.2.3']), \
                patch('monitor_switch.relay.request',return_value={'server_id':'wrong','monitors':self.pairing['monitors']}) as exchange:
            with self.assertRaisesRegex(RuntimeError,'Tailscale'):
                connect_pairing(self.pairing)
        self.assertTrue(all(call.args[1]=='GET' for call in exchange.call_args_list))
        self.assertEqual(self.sent,[])

    def test_wrong_secret_and_arbitrary_command_cannot_reach_monitor(self):
        bad = self.pairing | {'key': secrets.token_hex(32), 'host': '127.0.0.1'}
        with self.assertRaises(RuntimeError):
            request(bad, 'POST', '/switch', {'target': 'mac', 'monitors':['exact-windows-lg']})
        paired = self.pairing | {'host': '127.0.0.1'}
        with self.assertRaises(RuntimeError):
            request(paired, 'POST', '/switch', {'target': 'mac', 'vcp': 0xD6})
        self.assertEqual(self.sent, [])

    def test_replayed_authenticated_packet_is_rejected_without_second_write(self):
        body = json.dumps({'target': 'mac', 'monitors':['exact-windows-lg']}, separators=(',', ':')).encode()
        headers = signed_headers(self.pairing, 'POST', '/switch', body, self.server.epoch)
        statuses = []
        for _ in range(2):
            connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=2)
            connection.request('POST', '/switch', body=body, headers=headers)
            response = connection.getresponse()
            statuses.append(response.status)
            response.read()
            connection.close()
        self.assertEqual(statuses, [200, 403])
        self.assertEqual(self.sent, ['mac'])

    def test_wrong_epoch_and_expired_request_are_rejected_before_write(self):
        body = json.dumps({'target':'mac','monitors':['exact-windows-lg']}, separators=(',',':')).encode()
        headers_list = [signed_headers(self.pairing,'POST','/switch',body,'f'*32)]
        with patch('monitor_switch.relay.time.time', return_value=1):
            headers_list.append(signed_headers(self.pairing,'POST','/switch',body,self.server.epoch))
        for headers in headers_list:
            connection = http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=2)
            connection.request('POST','/switch',body,headers)
            response = connection.getresponse()
            self.assertEqual(response.status,403)
            response.read()
            connection.close()
        self.assertEqual(self.sent,[])

    def test_busy_rejection_is_unavailable_without_a_native_write(self):
        from monitor_switch.switching import SwitchBusy
        def busy(*args, **kwargs):
            raise SwitchBusy('A switch is already in progress. No command sent.')
        self.server.send = busy
        with self.assertRaises(RuntimeError) as raised:
            request(self.pairing | {'host':'127.0.0.1'},'POST','/switch',
                    {'target':'mac','monitors':['exact-windows-lg']})
        self.assertEqual(raised.exception.status,'unavailable')
        self.assertEqual(self.sent,[])

    def test_relay_does_not_require_local_monitor_or_native_backend(self):
        config = default_config()
        config['monitors'][1].update(id='local-lg', enabled=True, mode='lg-alt', mac=144, windows=208,
                                     relay=self.pairing | {'host': '127.0.0.1', 'remote_id':'exact-windows-lg'})
        result = switch(config, 'windows', object())
        self.assertEqual(result[0]['status'], 'sent')
        self.assertEqual(self.sent, ['windows'])

    def test_relay_mapping_cannot_disagree_with_fixed_remote_codes(self):
        config = default_config()
        config['monitors'][1].update(id='lg', enabled=True, mode='lg-alt', mac=17, windows=15,
                                     relay=self.pairing | {'host': '127.0.0.1', 'remote_id':'exact-windows-lg'})
        with self.assertRaises(ValueError):
            validate_config(config)

    def test_response_authentication_failure_is_not_treated_as_sent(self):
        original = self.server.sign_reply
        self.server.sign_reply = lambda p,n,s,b: '0'*64 if 'results' in json.loads(b) else original(p,n,s,b)
        config = default_config()
        config['monitors'][1].update(id='local-lg', enabled=True, mode='lg-alt', mac=144, windows=208,
                                     relay=self.pairing | {'host': '127.0.0.1', 'remote_id':'exact-windows-lg'})
        self.assertEqual(switch(config, 'windows', object())[0]['status'], 'uncertain')
        self.assertEqual(self.sent, ['windows'])

    def test_two_rows_send_one_batch_and_disabled_monitor_is_not_sent(self):
        config = default_config()
        for row, profile in zip(config['monitors'], self.pairing['monitors']):
            row.update(id='local-' + profile['role'], enabled=True,
                       **{key:profile[key] for key in ('mode','mac','windows')},
                       relay=self.pairing | {'host':'127.0.0.1', 'remote_id':profile['id']})
        result = switch(config, 'mac', object())
        self.assertEqual([r['status'] for r in result], ['sent','sent'])
        self.assertEqual(self.sent, ['mac'])
        config['monitors'][0]['enabled'] = False
        switch(config,'windows',object())
        self.assertEqual(self.batches[-1],['exact-windows-lg'])


if __name__ == '__main__':
    unittest.main()
