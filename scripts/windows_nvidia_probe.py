"""Read-only inventory for testing the existing Windows-to-LG DisplayPort path."""
import argparse
import ctypes
import json
import platform
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class DisplayID(ctypes.Structure):
    # NV_GPU_DISPLAYIDS, current SDK version is MAKE_NVAPI_VERSION(struct, 3).
    _fields_ = [('version', ctypes.c_uint32), ('connector_type', ctypes.c_int32),
                ('display_id', ctypes.c_uint32), ('flags', ctypes.c_uint32)]


def nvidia_inventory():
    if struct.calcsize('P') != 8:
        return {'error': 'Use 64-bit Python with the installed NVIDIA display driver.'}
    # Load the driver's own DLL from System32, never a downloaded DLL beside this script.
    dll = ctypes.CDLL('nvapi64.dll', winmode=0x800)
    query = dll.nvapi_QueryInterface
    query.argtypes, query.restype = [ctypes.c_uint32], ctypes.c_void_p

    def function(identifier, *args):
        pointer = query(identifier)
        if not pointer:
            raise RuntimeError(f'NVIDIA interface 0x{identifier:08X} is unavailable.')
        return ctypes.CFUNCTYPE(ctypes.c_int, *args)(pointer)

    initialize = function(0x0150E828)
    unload = function(0xD22BDD7E)
    error_message = function(0x6C2D048C, ctypes.c_int, ctypes.POINTER(ctypes.c_char))

    def checked(status):
        if status:
            message = ctypes.create_string_buffer(64)
            error_message(status, message)
            raise RuntimeError(f'NVAPI {status}: {message.value.decode(errors="replace")}')

    checked(initialize())
    try:
        enumerate_gpus = function(0xE5AC921F, ctypes.POINTER(ctypes.c_void_p),
                                  ctypes.POINTER(ctypes.c_uint32))
        gpu_name = function(0xCEEE8E9F, ctypes.c_void_p, ctypes.POINTER(ctypes.c_char))
        all_displays = function(0x785210A2, ctypes.c_void_p, ctypes.POINTER(DisplayID),
                                ctypes.POINTER(ctypes.c_uint32))
        handles = (ctypes.c_void_p * 64)()
        count = ctypes.c_uint32()
        checked(enumerate_gpus(handles, ctypes.byref(count)))
        if count.value > len(handles):
            raise RuntimeError('Driver returned an invalid GPU count.')
        gpus = []
        for handle in list(handles)[:count.value]:
            name = ctypes.create_string_buffer(64)
            checked(gpu_name(handle, name))
            item = {'name': name.value.decode(errors='replace'), 'outputs': []}
            try:
                size = ctypes.c_uint32()
                checked(all_displays(handle, None, ctypes.byref(size)))
                if size.value > 512:
                    raise RuntimeError('Driver returned an excessive display count.')
                if size.value:
                    records = (DisplayID * size.value)()
                    for record in records:
                        record.version = ctypes.sizeof(DisplayID) | (3 << 16)
                    checked(all_displays(handle, records, ctypes.byref(size)))
                    if size.value > len(records):
                        raise RuntimeError('Display inventory changed during the query; rerun the probe.')
                    for record in list(records)[:size.value]:
                        item['outputs'].append({
                            'display_id': f'0x{record.display_id:08X}',
                            'active': bool(record.flags & (1 << 2)),
                            'os_visible': bool(record.flags & (1 << 4)),
                            'connected': bool(record.flags & (1 << 6)),
                            'physically_connected': bool(record.flags & (1 << 17)),
                            'raw_flags': f'0x{record.flags:08X}',
                        })
            except Exception as exc:
                item['output_error'] = str(exc)
            gpus.append(item)
        return {'gpus': gpus}
    finally:
        unload()


def collect():
    report = {'platform': platform.platform(), 'python': platform.python_version(),
              'read_only': True, 'source_switch_attempted': False}
    try:
        report['nvidia'] = nvidia_inventory()
    except Exception as exc:
        report['nvidia'] = {'error': str(exc)}
    try:
        from monitor_switch.backends import WindowsNative
        native = WindowsNative()
        rows = []
        with native.snapshot() as monitors:
            for monitor in monitors:
                item = {'name': monitor.name, 'id': monitor.id,
                        'physical_handle_available': bool(monitor.handle)}
                if monitor.error:
                    item['error'] = monitor.error
                if monitor.handle:
                    try:
                        value = native.read(monitor.handle)
                        item.update(input_raw=value, input_low_byte=value & 0xFF)
                    except Exception as exc:
                        item['input_read_error'] = str(exc)
                rows.append(item)
        report['windows_monitors'] = rows
    except Exception as exc:
        report['windows_monitor_error'] = str(exc)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label', choices=('mac-visible', 'windows-visible', 'unknown'), default='unknown')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--native-child', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if sys.platform != 'win32':
        print('Run this read-only probe on the Windows PC connected to LG by DisplayPort.')
        return 2
    if args.native_child:
        args.native_child.write_text(json.dumps(collect(), indent=2), encoding='utf-8')
        return 0
    with tempfile.TemporaryDirectory(prefix='monitor-switch-probe-') as directory:
        child_result = Path(directory) / 'result.json'
        try:
            process = subprocess.run([sys.executable, str(Path(__file__).resolve()),
                                      '--native-child', str(child_result)],
                                     capture_output=True, text=True, timeout=25)
            if process.returncode or not child_result.exists():
                raise RuntimeError(f'Probe worker exited {process.returncode}: {process.stderr[-500:]}')
            report = json.loads(child_result.read_text(encoding='utf-8'))
        except subprocess.TimeoutExpired:
            report = {'read_only': True, 'source_switch_attempted': False,
                      'error': 'A driver query exceeded the 25-second deadline; worker terminated.'}
        except Exception as exc:
            report = {'read_only': True, 'source_switch_attempted': False, 'error': str(exc)}
    report['operator_selected_lg_picture'] = args.label
    output = args.output or ROOT / 'diagnostics' / f'windows-nvidia-{args.label}.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(f'Saved read-only report: {output.resolve()}')
    print('This inventory does not establish that LG accepts alternate input commands.')
    return 0 if 'error' not in report else 1


if __name__ == '__main__':
    raise SystemExit(main())
