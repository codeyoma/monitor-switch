"""Read installed Tailscale CLI state only when connecting or exporting pairing."""
import ipaddress
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

TAILNET = ipaddress.IPv4Network('100.64.0.0/10')
PRIVATE = tuple(ipaddress.IPv4Network(value) for value in
                ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16','127.0.0.0/8'))


def tailscale_host(host):
    if not isinstance(host,str):return False
    try:return ipaddress.IPv4Address(host) in TAILNET
    except (ValueError,TypeError):return False


def relay_host(host):
    if not isinstance(host,str):return False
    try:
        address=ipaddress.IPv4Address(host)
        return address in TAILNET or any(address in network for network in PRIVATE)
    except (ValueError,TypeError):return False


def cli_path():
    found=shutil.which('tailscale')
    if found:return found
    candidates=([Path(os.environ.get('ProgramFiles',r'C:\Program Files'))/'Tailscale/tailscale.exe']
                if sys.platform=='win32' else
                [Path('/Applications/Tailscale.app/Contents/MacOS/Tailscale'),Path('/usr/local/bin/tailscale')])
    return next((str(path) for path in candidates if path.is_file()),None)


def cli_output(*arguments):
    executable=cli_path()
    if not executable:return ''
    options={'creationflags':subprocess.CREATE_NO_WINDOW} if sys.platform=='win32' else {}
    try:
        result=subprocess.run([executable,*arguments],capture_output=True,text=True,timeout=2,**options)
        return result.stdout if result.returncode==0 and len(result.stdout)<1024*1024 else ''
    except (OSError,subprocess.TimeoutExpired):return ''


def local_hosts():
    return list(dict.fromkeys(host for host in cli_output('ip','-4').split() if tailscale_host(host)))


def windows_peer_hosts():
    try:
        status=json.loads(cli_output('status','--json'))
        if status.get('BackendState')!='Running':return []
        peers=status.get('Peer',{}).values()
        hosts=[host for peer in peers if peer.get('OS')=='windows' and peer.get('Online')
               for host in peer.get('TailscaleIPs',[]) if tailscale_host(host)]
        return list(dict.fromkeys(hosts))[:4]
    except (ValueError,TypeError,AttributeError):return []
