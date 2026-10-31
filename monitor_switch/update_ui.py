"""Low-frequency release checks integrated with the resident's idle state."""
import shutil
import threading
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QObject, QThread, QTimer, Signal
from PySide6.QtWidgets import QPushButton, QCheckBox, QLabel

from .startup import data_directory
from .updates import CHECK_INTERVAL_MS, set_automatic, update_preferences
from .update_install import installed_here
from .version import VERSION


class UpdateJob(QThread):
    completed = Signal(object)

    def __init__(self, parent):
        super().__init__(parent)
        self.cancelled = threading.Event()
        self.process = None
        self.process_lock = threading.Lock()

    def cancel(self, force=False):
        self.cancelled.set()
        with self.process_lock:
            if self.process and self.process.poll() is None:
                self.process.kill() if force else self.process.terminate()

    def run(self):
        data = data_directory()
        data.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix='update-', dir=data))
        stage.chmod(0o700)
        result = None
        try:
            command = ([sys.executable] if getattr(sys, 'frozen', False) else
                       [sys.executable, str(Path(__file__).resolve().parents[1]/'app.py')])
            command += ['--update-fetch', str(stage)]
            with self.process_lock:
                if self.cancelled.is_set():
                    raise InterruptedError('Update cancelled.')
                self.process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    close_fds=True, **({'creationflags': subprocess.CREATE_NO_WINDOW} if sys.platform == 'win32' else {}))
            try:
                self.process.wait(timeout=620)
            except subprocess.TimeoutExpired:
                self.process.kill();self.process.wait(5)
                raise TimeoutError('Update check timed out.')
            if self.cancelled.is_set():
                raise InterruptedError('Update cancelled.')
            result = json.loads((stage/'result.json').read_text(encoding='utf-8'))
            if result.get('stage') and Path(result['stage']).resolve() != stage.resolve():
                raise ValueError('Unexpected update worker result.')
        except Exception as exc:
            result = {'error': str(exc) or type(exc).__name__}
        finally:
            if not result or not result.get('stage'):
                shutil.rmtree(stage, ignore_errors=True)
        self.completed.emit(result)


class UpdateController(QObject):
    def __init__(self, resident):
        super().__init__(resident)
        self.resident, self.window = resident, resident.window
        self.job, self.pending, self.installing, self.result = None, None, False, None
        self.manual = False
        self.cancelling = False
        self.enabled = installed_here() and not self.window.demo
        self.check_action = resident.menu.addAction('업데이트 확인', lambda:self.check(True))
        self.automatic = resident.menu.addAction('새 버전 자동 업데이트')
        self.automatic.setCheckable(True)
        self.check_button = QPushButton('업데이트 확인')
        self.check_button.setProperty('role', 'link')
        self.check_button.clicked.connect(lambda:self.check(True))
        self.toggle = QCheckBox('새 버전 자동 업데이트')
        self.version_label = QLabel(f'버전 {VERSION}')
        layout = self.window.shell.activity_page.layout()
        for widget in (self.version_label, self.toggle, self.check_button):
            layout.addWidget(widget)
        try:
            automatic = update_preferences()['automatic']
        except Exception as exc:
            automatic = False
            self.window.log.appendPlainText('업데이트 설정 확인 실패: '+str(exc))
        self.automatic.setChecked(automatic)
        self.toggle.setChecked(automatic)
        self.automatic.triggered.connect(self.change_automatic)
        self.toggle.toggled.connect(self.change_automatic)
        for action in (self.check_action, self.automatic):
            resident.more_menu.insertAction(resident.quit_action, action)
        self.timer = QTimer(self)
        self.timer.setInterval(CHECK_INTERVAL_MS)
        self.timer.timeout.connect(self.check)
        self.window.state_changed.connect(self.maybe_install)
        if self.enabled and automatic:
            self.timer.start()
            QTimer.singleShot(30000, self.scheduled_check)
        self.refresh()
        result = data_directory()/'update-result.json'
        if result.exists() and not self.window.demo:
            import json
            try:
                value = json.loads(result.read_text(encoding='utf-8'))
                self.window.log.appendPlainText('업데이트 완료: '+value['version'] if value.get('ok')
                                               else '업데이트 실패 · 기존 버전 유지: '+value.get('error', ''))
            except (ValueError, KeyError):
                pass
        # Delete only successful stages left by our helper, after it has exited.
        if self.enabled:
            for stage in data_directory().glob('update-*'):
                if stage.is_dir() and (stage/'complete').is_file():
                    try:
                        shutil.rmtree(stage)
                    except OSError:
                        pass

    def scheduled_check(self):
        if self.automatic.isChecked():
            self.check()

    def refresh(self):
        for item in (self.check_action, self.check_button):
            item.setEnabled(self.enabled and self.job is None and not self.installing)
        self.check_action.setText('업데이트 확인 중…' if self.job else '업데이트 확인')
        self.automatic.setEnabled(self.enabled and not self.installing)
        self.toggle.setEnabled(self.enabled and not self.installing)
        self.version_label.setText(f'버전 {VERSION}'+(' · 소스 실행에서는 자동 업데이트를 지원하지 않습니다.' if not self.enabled else ''))

    def change_automatic(self, enabled):
        try:
            set_automatic(enabled)
        except Exception as exc:
            self.failed(str(exc))
            enabled = not enabled
        for item in (self.automatic, self.toggle):
            item.blockSignals(True);item.setChecked(enabled);item.blockSignals(False)
        self.timer.start() if enabled and self.enabled else self.timer.stop()
        if enabled:
            self.check()
        elif self.pending:
            shutil.rmtree(self.pending, ignore_errors=True)
            self.pending = None

    def check(self, manual=False):
        if not self.enabled or self.job or self.installing or self.resident.quitting:
            return
        self.manual = bool(manual)
        if self.pending:
            self.maybe_install()
            return
        self.result = None
        self.job = UpdateJob(self)
        self.job.completed.connect(lambda result:setattr(self, 'result', result))
        self.job.finished.connect(self.finished)
        self.job.start()
        self.refresh()

    def finished(self):
        job, self.job = self.job, None
        job.deleteLater()
        result = self.result or {'error': 'Update worker stopped without a result.'}
        if self.cancelling:
            self.cancelling = False
            if result.get('stage'):
                shutil.rmtree(result['stage'], ignore_errors=True)
            self.refresh()
            return
        if result.get('error'):
            self.failed(result['error'], quiet=not self.manual)
        elif result['stage']:
            if self.automatic.isChecked() or self.manual:
                self.pending = Path(result['stage'])
                self.window.shell.message('새 버전을 받았습니다. 진행 중인 작업이 끝나면 업데이트합니다.')
                self.maybe_install()
            else:
                shutil.rmtree(result['stage'], ignore_errors=True)
        elif self.manual:
            self.window.shell.message('최신 버전입니다. · '+VERSION)
        self.refresh()

    def maybe_install(self):
        w = self.window
        if (not self.pending or self.installing or self.resident.quitting or w.job
                or getattr(w, 'native_busy', False) or getattr(w, 'dirty', False) or getattr(w, 'closing', False)):
            return
        self.installing = True
        if not self.resident.begin_update(self.pending):
            self.installing = False
        self.refresh()

    def failed(self, message, quiet=False):
        self.window.log.appendPlainText('업데이트 실패: '+message)
        if not quiet:
            self.window.shell.message('업데이트하지 못했습니다. 기존 버전을 계속 사용합니다.', True)
        self.installing = False

    def close(self):
        self.timer.stop()
        if self.job:
            self.job.cancel()
            if not self.job.wait(5000):
                self.job.cancel(force=True)
                self.job.wait()
