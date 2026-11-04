#!/usr/bin/env python3
"""Run the GUI, CLI, or isolated native worker."""
import argparse
import json
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description='Switch external monitor inputs using DDC/CI.')
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument('--list', action='store_true', help='List monitor identifiers without DDC reads.')
    actions.add_argument('--probe', action='store_true', help='Read configured input protocols without switching.')
    actions.add_argument('--switch', choices=('mac', 'windows'), help='Send saved input mappings.')
    actions.add_argument('--dry-run', choices=('mac', 'windows'), help='Show saved mappings without hardware access.')
    parser.add_argument('--config', type=Path, help='Use a separate configuration file for CLI commands.')
    parser.add_argument('--demo', action='store_true', help='Use simulated monitors; no hardware writes.')
    parser.add_argument('--background', action='store_true', help='Start in the menu bar or system tray without opening settings.')
    parser.add_argument('--worker', nargs=2, metavar=('REQUEST', 'RESULT'), help=argparse.SUPPRESS)
    parser.add_argument('--nvidia-worker', nargs=2, metavar=('REQUEST', 'RESULT'), help=argparse.SUPPRESS)
    parser.add_argument('--apply-update', nargs=2, metavar=('STAGE', 'PID'), help=argparse.SUPPRESS)
    parser.add_argument('--update-health', help=argparse.SUPPRESS)
    parser.add_argument('--update-fetch', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.update_fetch:
        from monitor_switch.updates import fetch_worker
        fetch_worker(args.update_fetch)
        return 0
    if args.apply_update:
        from monitor_switch.update_install import apply_update, report_failure
        try:
            apply_update(args.apply_update[0], int(args.apply_update[1]))
            return 0
        except Exception as exc:
            report_failure(exc)
            return 1
    if args.nvidia_worker:
        from monitor_switch.nvidia_worker import worker_main
        worker_main(*args.nvidia_worker)
        return 0
    if args.worker:
        from monitor_switch.worker import worker_main
        worker_main(*args.worker)
        return 0
    if any((args.list, args.probe, args.switch, args.dry_run)):
        from monitor_switch.core import load_config, validate_config
        from monitor_switch.worker import invoke
        try:
            config = load_config(args.config)
            if args.dry_run:
                validate_config(config, require_enabled=True)
                result = {'dry_run': True, 'target': args.dry_run, 'commands': [
                    {'label': r['label'], 'id': r['id'], 'mode': r['mode'], 'code': r[args.dry_run]}
                    for r in config['monitors'] if r['enabled']]}
            else:
                request = {'action': 'switch' if args.switch else 'probe' if args.probe else 'list',
                           'demo': args.demo, 'config': config, 'target': args.switch,
                           'modes': {r['id']: r['mode'] for r in config['monitors']}}
                result = invoke(request)
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 1 if 'error' in result or any(r['status'] != 'sent' for r in result.get('results', [])) else 0
        except Exception as exc:
            print(str(exc), file=sys.stderr)
            return 1
    if args.config:
        parser.error('--config applies to CLI commands only.')
    if not args.demo:
        from monitor_switch.update_install import bootstrap_install
        if bootstrap_install(args.background):
            return 0
    try:
        from monitor_switch.gui import run
    except ImportError:
        print('Install GUI dependencies: python -m pip install -r requirements.txt', file=sys.stderr)
        return 1
    return run(args.demo, background=args.background, update_health=args.update_health)


if __name__ == '__main__':
    raise SystemExit(main())
