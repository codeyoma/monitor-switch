"""Optional console interface for the same Windows relay used by the tray app."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main():
    if sys.platform != 'win32':
        print('Run this helper on the Windows NVIDIA PC.')
        return 2
    from monitor_switch.service import RelayService

    def observed(event):
        if event['kind'] == 'usb':
            print('USB:', event['result']['state'], event['result'].get('message',''))
        elif event.get('message'):
            print(event['message'])

    service = RelayService(ROOT, observed).start()
    try:
        path = ROOT/'MonitorSwitch-Pairing.json'
        try:
            service.export_pairing(path)
            print('Private connection file:', path)
        except RuntimeError as exc:
            print(exc)
        while True:
            answer = input('[M] Mac, [W] Windows, [A] USB auto, [Q] Quit: ').strip().upper()
            if answer == 'Q':
                break
            try:
                if answer == 'A':
                    service.set_auto(not service.auto_enabled.is_set())
                    print('USB auto:', service.auto_enabled.is_set())
                elif answer in ('M','W'):
                    service.manual('mac' if answer == 'M' else 'windows')
            except Exception as exc:
                print(exc)
    except (EOFError, KeyboardInterrupt):
        pass
    finally:
        service.stop()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
