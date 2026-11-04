"""Bound child lifetime and retain file journals until their writer has exited."""
from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile


@dataclass(frozen=True)
class ChildReply:
    payload: dict | None
    failure: str | None
    timed_out: bool


@contextmanager
def supervise(request, *, kind, timeout, command=None):
    if kind not in ('ddc', 'nvidia'):
        raise ValueError('Unknown native worker kind.')
    if command is None:
        command = [sys.executable] if getattr(sys, 'frozen', False) else [
            sys.executable, str(Path(__file__).resolve().parent.parent/'app.py')]
    flag = '--worker' if kind == 'ddc' else '--nvidia-worker'
    group = kind == 'ddc' and sys.platform != 'win32'
    directory = Path(tempfile.mkdtemp(prefix='monitor-switch-worker-'))
    process = None
    failure, timed_out, payload = None, False, None
    try:
        request_path, result_path = directory/'request.json', directory/'result.json'
        request_path.write_text(json.dumps(request), encoding='utf-8')
        options = ({'creationflags':subprocess.CREATE_NO_WINDOW} if sys.platform == 'win32'
                   else {'start_new_session':True} if group else {})
        try:
            process = subprocess.Popen([*command, flag, str(request_path), str(result_path)],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding='utf-8', errors='replace', **options)
            try:
                _, stderr = process.communicate(timeout=timeout)
                if process.returncode:
                    failure = f'Worker exited {process.returncode}: {(stderr or "")[-600:]}'
            except subprocess.TimeoutExpired:
                timed_out = True
                failure = f'Driver worker exceeded {timeout} seconds. Termination requested; no automatic retry.'
                try:
                    if group:
                        os.killpg(process.pid, signal.SIGKILL)
                    else:
                        process.kill()
                    process.communicate(timeout=2)
                except subprocess.TimeoutExpired:
                    failure += ' Worker did not exit within the additional 2-second deadline.'
                except OSError as exc:
                    failure += f' Worker termination error: {exc}'
        except Exception as exc:
            failure = str(exc)
        try:
            value = json.loads(result_path.read_text(encoding='utf-8'))
            if isinstance(value, dict):
                payload = value
        except (OSError, ValueError):
            pass
        if payload is None and failure is None:
            failure = 'Worker did not return a valid report. Input state is unknown.'
        # Callers persist diagnostics before this context releases the workspace.
        yield ChildReply(payload, failure, timed_out)
    finally:
        if process is None or process.poll() is not None:
            shutil.rmtree(directory, ignore_errors=True)
