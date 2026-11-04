"""A bounded, one-command test of LG alternate switching over NVIDIA DisplayPort."""
import argparse
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def run_test(config, target, *, timeout=20, reporter=print, diagnostics=None):
    from monitor_switch.nvidia_worker import run_switch
    report = run_switch(config, target, timeout=timeout, diagnostics=diagnostics or ROOT/'diagnostics')
    for row in report.get('results', [report]):
        reporter(f'\n{row.get("label", "Result")}: {row["status"]}')
        if row.get('error'):
            reporter(row['error'])
    if report.get('error'):
        reporter(report['error'])
    if report.get('status') == 'sent':
        reporter('NVIDIA accepted the command. This does not confirm a visible input change.')
    reporter('Report: '+report['report_path'])
    reporter('If control is lost, select the required input using the monitor buttons.')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=ROOT / 'lg-switch-test.json')
    parser.add_argument('--target', choices=('mac', 'windows'))
    parser.add_argument('--native-child', nargs=2, metavar=('REQUEST', 'RESULT'), help=argparse.SUPPRESS)
    args = parser.parse_args()
    if sys.platform != 'win32' or struct.calcsize('P') != 8:
        print('Run this test using 64-bit Python on the Windows NVIDIA PC.')
        return 2
    if args.native_child:
        from monitor_switch.nvidia_worker import worker_main
        worker_main(*args.native_child)
        return 0
    try:
        config = json.loads(args.config.read_text(encoding='utf-8'))
        if config.get('version') != 1 or not isinstance(config.get('monitor_id'), str) or not config['monitor_id']:
            raise ValueError('Invalid LG identity configuration.')
    except (OSError, ValueError) as exc:
        print(f'Cannot load the LG identity: {exc}')
        return 2
    if args.target:
        report = run_test(config, args.target)
        return 0 if report['status'] == 'sent' else 1
    print('LG input test over the existing Windows NVIDIA DisplayPort cable.')
    print('This tool sends a real input change to the exact LG from your probe report.')
    print('Dell is not controlled. No network service or extra pip packages are used.')
    while True:
        print('\n[W] LG to Windows DP (208 / 0xD0)')
        print('[M] LG to Mac HDMI 1 (144 / 0x90)')
        print('[Q] Quit')
        try:
            answer = input('Choose W, M, or Q: ').strip().upper()
        except (EOFError, KeyboardInterrupt):
            return 0
        if answer == 'Q':
            return 0
        if answer not in ('M', 'W'):
            continue
        run_test(config, 'mac' if answer == 'M' else 'windows')


if __name__ == '__main__':
    raise SystemExit(main())
