import json
import subprocess
import unittest
from unittest.mock import patch

from monitor_switch import tailscale
from monitor_switch.service import network_hosts


class TailscaleTests(unittest.TestCase):
    def test_cli_local_addresses_are_filtered_to_tailscale_ipv4(self):
        with patch.object(tailscale,'cli_output',return_value='100.70.2.3\nfd7a::1\n8.8.8.8\n100.70.2.3\n'):
            self.assertEqual(tailscale.local_hosts(),['100.70.2.3'])

    def test_windows_discovery_only_returns_online_ipv4_peers(self):
        status={'BackendState':'Running','Peer':{
            'windows':dict(OS='windows',Online=True,TailscaleIPs=['100.70.1.2','fd7a::1']),
            'offline':dict(OS='windows',Online=False,TailscaleIPs=['100.70.1.3']),
            'mac':dict(OS='macOS',Online=True,TailscaleIPs=['100.70.1.4'])}}
        with patch.object(tailscale,'cli_output',return_value=json.dumps(status)):
            self.assertEqual(tailscale.windows_peer_hosts(),['100.70.1.2'])

    def test_not_running_and_invalid_cli_replies_are_optional(self):
        for output in ('','invalid','{}','{"BackendState":"Stopped"}'):
            with patch.object(tailscale,'cli_output',return_value=output):
                self.assertEqual(tailscale.windows_peer_hosts(),[])

    def test_optional_cli_has_a_deadline_and_does_not_change_tailscale_settings(self):
        with patch.object(tailscale,'cli_path',return_value='/installed/tailscale'), \
                patch.object(tailscale.subprocess,'run',side_effect=subprocess.TimeoutExpired('tailscale',2)) as run:
            self.assertEqual(tailscale.cli_output('ip','-4'),'')
        self.assertEqual(run.call_args.args[0],['/installed/tailscale','ip','-4'])
        self.assertEqual(run.call_args.kwargs['timeout'],2)

    def test_export_prefers_tailscale_even_when_lan_lookup_fails(self):
        with patch('monitor_switch.service.local_hosts',return_value=['100.70.1.2']), \
                patch('monitor_switch.service.socket.getaddrinfo',side_effect=OSError):
            self.assertEqual(network_hosts(),['100.70.1.2'])
        with patch('monitor_switch.service.local_hosts',return_value=['100.70.1.2']), \
                patch('monitor_switch.service.socket.getaddrinfo',return_value=[(0,0,0,'',('192.168.0.2',0))]):
            self.assertEqual(network_hosts(),['100.70.1.2','192.168.0.2'])
