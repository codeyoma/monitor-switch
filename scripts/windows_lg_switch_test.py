"""A bounded, one-command test of LG alternate switching over NVIDIA DisplayPort."""
import argparse
import json
import platform
import shutil
import struct
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def save_json(path, report):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    temporary.replace(path)


def run_test(config, target, *, timeout=20, reporter=print, diagnostics=None):
    from monitor_switch.nvidia import LG_INPUTS
    base = {'platform': platform.platform(), 'python': platform.python_version(),
            'time_utc': datetime.now(timezone.utc).isoformat(),
            'target': target,
            'physical_switch_confirmed': False}
    if 'monitors' not in config:
        base['input_code'] = LG_INPUTS[target]
    directory = tempfile.mkdtemp(prefix='lg-switch-test-')
    process = None
    try:
        request = Path(directory) / 'request.json'
        result = Path(directory) / 'result.json'
        job = {'target': target}
        job.update(monitors=config['monitors']) if 'monitors' in config else job.update(monitor_id=config['monitor_id'])
        save_json(request, job)
        failure = None
        try:
            command = ([sys.executable, '--nvidia-worker'] if getattr(sys, 'frozen', False) else
                       [sys.executable, str(Path(__file__).resolve()), '--native-child'])
            process = subprocess.Popen(command + [str(request), str(result)],
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       text=True, encoding='utf-8', errors='replace',
                                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            _, stderr = process.communicate(timeout=timeout)
            if process.returncode:
                failure = f'Worker exited {process.returncode}: {stderr[-600:]}'
        except subprocess.TimeoutExpired:
            failure = f'Driver worker exceeded {timeout} seconds. Termination requested; no automatic retry.'
            try:
                process.kill()
                process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                failure += ' Worker did not exit within the additional 2-second deadline.'
            except OSError as exc:
                failure += f' Worker termination error: {exc}'
        except Exception as exc:
            failure = str(exc)
        if result.exists():
            try:
                report = json.loads(result.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                report = {}
        else:
            report = {}
        if not report:
            report = {'status': 'uncertain', 'write_attempted': None,
                      'error': 'Worker did not return a complete report. Input state is unknown.'}
        if failure:
            if report.get('status') == 'sent':
                report['worker_warning'] = failure
            else:
                report['error'] = failure
        report = base | report
        prefix = 'monitor-switch' if 'monitors' in config else 'lg-switch'
        output = Path(diagnostics or ROOT / 'diagnostics') / f'{prefix}-{target}-{uuid4().hex[:8]}.json'
        # Save outside the worker directory before attempting any cleanup.
        save_json(output, report)
    finally:
        if process is None or process.poll() is not None:
            try:
                shutil.rmtree(directory, ignore_errors=True)
            except OSError:
                pass
    for row in report.get('results', [report]):
        reporter(f'\n{row.get("label", "Result")}: {row["status"]}')
        if row.get('error'):
            reporter(row['error'])
    if report.get('error'):
        reporter(report['error'])
    if report.get('status') == 'sent':
        reporter('NVIDIA accepted the command. This does not confirm a visible input change.')
    reporter(f'Report: {output.resolve()}')
    reporter('If control is lost, select the required input using the monitor buttons.')
    return report


def native_worker_main(request, result):
    if sys.platform != 'win32' or struct.calcsize('P') != 8:
        raise RuntimeError('The NVIDIA worker requires 64-bit Windows.')
    from monitor_switch.nvidia import switch_lg, switch_group
    request, result = Path(request), Path(result)
    job = json.loads(request.read_text(encoding='utf-8'))
    if 'monitors' in job:
        switch_group(job['monitors'], job['target'], journal=lambda report: save_json(result, report))
    else:
        switch_lg(job['monitor_id'], job['target'], journal=lambda report: save_json(result, report))


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
        native_worker_main(*args.native_child)
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
