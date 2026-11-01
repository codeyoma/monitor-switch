"""One-button host switching with two explicit monitor mappings."""
import copy
import json
import sys
from pathlib import Path

from PySide6.QtCore import QThread, Signal, Qt, QTimer
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QGroupBox, QFormLayout, QComboBox, QCheckBox, QPlainTextEdit,
    QMessageBox, QFileDialog, QScrollArea)

from .core import default_config, load_config, save_config, validate_config, config_path
from .worker import invoke
from .ui import Shell, PopupCombo, Toggle, module, label, divider, switch_feedback

STANDARD = [('DisplayPort 1', 15), ('DisplayPort 2', 16), ('HDMI 1', 17),
            ('HDMI 2', 18), ('USB-C (monitor-dependent)', 27)]
ALTERNATE = [('LG DisplayPort 1', 208), ('LG DisplayPort 2', 209), ('LG HDMI 1', 144),
             ('LG HDMI 2', 145), ('LG USB-C / DP 3', 210)]


def code_text(code):
    return '' if code is None else f'{code} (0x{code:02X})'


class Job(QThread):
    completed = Signal(object)

    def __init__(self, request, parent):
        super().__init__(parent)
        self.request = request

    def run(self):
        try:
            self.completed.emit(invoke(self.request))
        except Exception as exc:
            self.completed.emit({'error': str(exc)})


class USBWatch(QThread):
    observed = Signal(object)

    def __init__(self, parent):
        super().__init__(parent)
        self.monitor = None
        self.stop_requested = False

    def run(self):
        from .usb_events import USBEventWatcher
        self.monitor = USBEventWatcher(lambda result:self.observed.emit(dict(result,watcher_id=id(self))))
        if self.stop_requested or self.isInterruptionRequested():
            self.monitor.stop()
        self.monitor.run()

    def stop(self):
        self.stop_requested = True
        self.requestInterruption()
        if self.monitor is not None:
            self.monitor.stop()

    def current(self, result):
        return self.monitor is not None and self.monitor.current(result)


class MonitorCard(QWidget):
    def __init__(self, row, changed, parent=None):
        super().__init__(parent)
        self.setObjectName('ProfileCard')
        self.row = copy.deepcopy(row)
        self.latest_monitors = []
        self.changed = changed
        layout=QVBoxLayout(self);layout.setContentsMargins(0,0,0,0);layout.setSpacing(8)
        layout.addWidget(label(row['label'],'group'))
        group=module('SettingsGroup');fields=QVBoxLayout(group)
        fields.setContentsMargins(12,0,12,0);fields.setSpacing(0)
        self.enabled=Toggle('전환에 포함');self.enabled.setChecked(row['enabled'])
        self.monitor=PopupCombo();self.monitor.setMinimumWidth(220);self.monitor.setMaximumWidth(410)
        self.mode=PopupCombo();self.mode.setMinimumWidth(220);self.mode.setMaximumWidth(410)
        self.mode.addItem('Standard DDC/CI · VCP 0x60','standard')
        self.mode.addItem('LG alternate · Mac / Windows helper','lg-alt')
        self.mode.setCurrentIndex(1 if row['mode']=='lg-alt' else 0)
        self.mac,self.windows=PopupCombo(),PopupCombo()
        for combo in (self.mac,self.windows):
            combo.setEditable(True);combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            combo.setMinimumWidth(220);combo.setMaximumWidth(410)
            combo.lineEdit().setAlignment(Qt.AlignmentFlag.AlignRight)
            combo.lineEdit().setPlaceholderText('입력 선택 또는 직접 입력')
            combo.setAccessibleDescription('입력 코드. 십진수 또는 0x로 시작하는 16진수를 입력할 수 있습니다.')
        self.populate_codes(row['mac'],row['windows'])
        for index,(title,control) in enumerate([('전환에 포함',self.enabled),('모니터',self.monitor),
                ('제어 방식',self.mode),('Mac 입력',self.mac),('Windows 입력',self.windows)]):
            if index:fields.addWidget(divider())
            row_widget=QWidget();row_layout=QHBoxLayout(row_widget)
            row_layout.setContentsMargins(0,9,0,9);row_layout.setSpacing(10)
            row_layout.addWidget(label(title),1);row_layout.addWidget(control)
            fields.addWidget(row_widget)
        layout.addWidget(group)
        self.readout=label('Scan to detect monitors.','secondary');self.readout.hide();layout.addWidget(self.readout)
        self.local_button=QPushButton('이 컴퓨터에서 직접 제어');self.local_button.clicked.connect(self.use_local)
        layout.addWidget(self.local_button,0,Qt.AlignmentFlag.AlignLeft)
        self.enabled.toggled.connect(changed)
        self.monitor.currentIndexChanged.connect(self.monitor_changed)
        self.mode.currentIndexChanged.connect(self.mode_changed)
        self.mac.editTextChanged.connect(changed)
        self.windows.editTextChanged.connect(changed)
        self.relay_controls()

    def relay_controls(self):
        paired = bool(self.row.get('relay'))
        self.local_button.setVisible(paired)
        for control in (self.mode, self.mac, self.windows):
            control.setEnabled(not paired)

    def use_local(self):
        self.row.pop('relay', None)
        self.relay_controls()
        self.update_readout()
        self.changed()

    def pair(self, pairing, profile):
        self.row['relay'] = dict(pairing, remote_id=profile['id'])
        identity = self.monitor.currentData() or self.row['id'] or ('relay:' + profile['role'] + ':' + pairing['server_id'])
        self.row['id'] = identity
        if self.monitor.findData(identity) < 0:
            self.monitor.addItem('Windows-controlled ' + profile['label'], identity)
        self.monitor.setCurrentIndex(self.monitor.findData(identity))
        self.mode.setCurrentIndex(self.mode.findData(profile['mode']))
        self.populate_codes(profile['mac'], profile['windows'])
        self.enabled.setChecked(True)
        self.relay_controls()
        self.update_readout()
        self.changed()

    def populate_codes(self, mac=None, windows=None):
        choices = ALTERNATE if self.mode.currentData() == 'lg-alt' else STANDARD
        for combo, value in ((self.mac, mac), (self.windows, windows)):
            combo.blockSignals(True)
            combo.clear()
            combo.addItem('Choose input…', None)
            for label, code in choices:
                combo.addItem(f'{label} · {code} / 0x{code:02X}', code)
            index = combo.findData(value) if value is not None else 0
            if index >= 0:
                combo.setCurrentIndex(index)
            else:
                combo.setEditText(str(value))
            combo.blockSignals(False)

    def mode_changed(self):
        # Never reinterpret standard codes as LG vendor-specific codes.
        self.populate_codes()
        self.readout.setText('Protocol changed. Select matching codes, then read inputs again.')
        self.readout.show()
        self.changed()

    def monitor_changed(self):
        self.update_readout()
        self.changed()

    def detected(self, monitors):
        self.latest_monitors = monitors
        identity = self.monitor.currentData() or self.row['id']
        self.monitor.blockSignals(True)
        self.monitor.clear()
        self.monitor.addItem('Choose detected monitor…', '')
        for monitor in monitors:
            self.monitor.addItem(f"{monitor['name']} · {monitor['id'][-12:]}", monitor['id'])
        if identity and self.monitor.findData(identity) < 0:
            self.monitor.addItem(f'Saved monitor · unavailable · {identity[-12:]}', identity)
        self.monitor.setCurrentIndex(max(0, self.monitor.findData(identity)))
        self.monitor.blockSignals(False)
        self.update_readout()

    def update_readout(self):
        self.readout.hide()
        if self.row.get('relay'):
            self.readout.setText('Controlled by paired Windows helper · ' + self.row['relay']['host'] +
                                 ' · keep Windows awake. Input readback is not used.')
            return
        found = next((m for m in self.latest_monitors if m['id'] == self.monitor.currentData()), None)
        if not found:
            self.readout.setText('No monitor selected, or saved monitor is unavailable.')
        elif found.get('error'):
            self.readout.show()
            self.readout.setText('Input unreadable: ' + found['error'])
        elif found.get('current') is not None:
            self.readout.show()
            if found.get('read_mode') != self.mode.currentData():
                self.readout.setText('Read inputs again using the selected protocol.')
                return
            raw = found.get('raw_current')
            extra = f' · raw {code_text(raw)}' if raw is not None and raw != found['current'] else ''
            self.readout.setText('Current input: ' + code_text(found['current']) + extra + ' · read reply only')
        else:
            self.readout.setText('Detected. Use Read inputs to query the current source.')

    @staticmethod
    def code(combo):
        text = combo.currentText().strip()
        index = combo.currentIndex()
        if index >= 0 and text == combo.itemText(index):
            return combo.currentData()
        if not text:
            return None
        try:
            return int(text, 16 if text.lower().startswith('0x') else 10)
        except ValueError as exc:
            raise ValueError('Use a listed input, decimal code, or 0x hexadecimal code.') from exc

    def value(self):
        return dict(self.row, enabled=self.enabled.isChecked(), id=self.monitor.currentData() or '',
                    mode=self.mode.currentData(), mac=self.code(self.mac), windows=self.code(self.windows))


class Window(QMainWindow):
    state_changed = Signal()

    def __init__(self, demo=False, auto_scan=True):
        super().__init__()
        self.demo, self.job, self.monitors = demo, None, []
        self.tray_managed = False
        self.usb_watch, self.closing, self.usb_destination = None, False, 'none'
        self.usb_restart_pending = False
        self.target = 'windows' if sys.platform == 'darwin' else 'mac'
        self.dirty = False
        load_error = None
        try:
            self.config = default_config() if demo else load_config()
        except ValueError as exc:
            self.config, load_error = default_config(), str(exc)
        self.load_failed = load_error is not None
        if demo:
            for row, identity in zip(self.config['monitors'], ('demo-dell', 'demo-lg')):
                row.update(id=identity, enabled=True, mac=15, windows=17)
        self.setWindowTitle('Monitor Switch' + (' · DEMO' if demo else ''))
        self.resize(680,663)
        self.setMinimumSize(640,600)
        self.shell=Shell('mac' if sys.platform=='darwin' else 'windows')
        self.setCentralWidget(self.shell)
        self.shell.attach_toolbar(self)
        self.switch_button=self.shell.windows if self.target=='windows' else self.shell.mac
        self.return_button=self.shell.mac if self.target=='windows' else self.shell.windows
        self.shell.mac.clicked.connect(lambda:self.switch_to('mac'))
        self.shell.windows.clicked.connect(lambda:self.switch_to('windows'))
        self.usb_toggle,self.usb_status=self.shell.automatic,self.shell.usb_status
        self.usb_toggle.setChecked(bool(self.config.get('usb_auto',False)))
        self.usb_toggle.toggled.connect(self.usb_changed)
        self.pair_button=self.shell.pair
        self.pair_button.clicked.connect(self.import_pairing)
        self.cards=[MonitorCard(row,self.changed) for row in self.config['monitors']]
        for card,name in zip(self.cards,('Dell','LG')):
            self.shell.monitor_tabs.addTab(card,name)
        controls=QHBoxLayout()
        self.scan_button=QPushButton('모니터 찾기')
        self.probe_button=QPushButton('현재 입력 읽기')
        self.capture_button=QPushButton('이 컴퓨터 입력에 적용')
        self.scan_button.clicked.connect(lambda:self.scan(False))
        self.probe_button.clicked.connect(lambda:self.scan(True))
        self.capture_button.clicked.connect(self.capture)
        controls.addWidget(self.scan_button);controls.addWidget(self.probe_button);controls.addStretch(1)
        controls.addWidget(self.capture_button)
        self.save_button=QPushButton('설정 저장')
        self.save_button.setProperty('role','primary')
        self.save_button.clicked.connect(self.save)
        controls.addWidget(self.save_button)
        self.shell.monitor_layout.addLayout(controls)
        self.shell.monitor_layout.addStretch(1)
        self.log=self.shell.log
        self.statusBar().hide()
        if demo:self.shell.message('미리보기 · 실제 화면과 설정은 바뀌지 않습니다.')
        if load_error:
            self.log.appendPlainText(load_error + '\nOriginal file preserved. Fix or back it up before saving new mappings.')
            self.save_button.setEnabled(False)
        self.refresh()
        if auto_scan:
            QTimer.singleShot(0, lambda: self.scan(False))
            if self.usb_toggle.isChecked() and not demo:
                QTimer.singleShot(0, self.start_usb_watch)

    def changed(self, *_args):
        self.dirty = True
        self.refresh()

    def refresh(self):
        busy = self.job is not None
        try:
            validate_config(self.config, require_enabled=True)
            ready = True
        except ValueError:
            ready = False
        self.switch_button.setEnabled(ready and not busy and not self.dirty)
        for control in [*getattr(self, 'cards', []), self.scan_button, self.probe_button, self.capture_button]:
            control.setEnabled(not busy)
        # A malformed config must remain recoverable, so its Save button stays disabled.
        if hasattr(self, 'load_failed'):
            self.save_button.setEnabled(not busy and not self.load_failed)
        if hasattr(self, 'pair_button'):
            self.pair_button.setEnabled(not busy and not self.demo and not self.load_failed)
            paired = all(row.get('relay') for row in self.config['monitors'])
            self.usb_toggle.setEnabled(paired and not busy and not self.demo and not self.load_failed)
            self.usb_toggle.setEnabled(self.usb_toggle.isEnabled() and sys.platform == 'darwin' and not self.dirty)
            self.return_button.setVisible(True)
            self.return_button.setEnabled(ready and not busy and not self.dirty)
        if self.dirty and not busy:
            self.statusBar().showMessage('Unsaved mappings · save before switching.')
        self.shell.update_rows(self.config['monitors'])
        self.shell.update_usb(self.usb_status.text(),self.usb_toggle.isChecked())
        if self.dirty and not busy:
            self.shell.message('변경한 모니터 설정을 저장한 뒤 전환하세요.',True)
        self.state_changed.emit()

    def values(self):
        return {'version': 1, 'monitors': [card.value() for card in self.cards],
                'usb_auto':self.usb_toggle.isChecked()}

    def usb_changed(self):
        if self.dirty:
            self.usb_toggle.blockSignals(True)
            self.usb_toggle.setChecked(bool(self.config.get('usb_auto',False)))
            self.usb_toggle.blockSignals(False)
            self.log.appendPlainText('Save mappings before changing USB automatic mode.')
            return
        if not self.save():
            self.usb_toggle.blockSignals(True)
            self.usb_toggle.setChecked(False)
            self.usb_toggle.blockSignals(False)
            self.stop_usb_watch()
            self.usb_status.setText('HiFime SA9023 USB Audio · automatic switching off; settings were not saved.')
            return
        if self.usb_toggle.isChecked():
            self.start_usb_watch()
        else:
            self.stop_usb_watch()
            self.usb_status.setText('HiFime SA9023 USB Audio · automatic switching off')
        self.state_changed.emit()

    def start_usb_watch(self):
        if self.demo or sys.platform != 'darwin' or not all(row.get('relay') for row in self.config['monitors']):
            return
        if self.usb_watch and self.usb_watch.isRunning():
            if self.usb_watch.stop_requested or self.usb_watch.isInterruptionRequested():
                self.usb_restart_pending = True
            return
        self.usb_restart_pending = False
        self.usb_status.setText('HiFime SA9023 USB Audio · native USB notification subscription starting…')
        self.usb_watch = USBWatch(self)
        self.usb_watch.observed.connect(self.usb_observed)
        self.usb_watch.finished.connect(self.usb_finished)
        self.usb_watch.start()

    def usb_finished(self):
        watch = self.sender()
        if self.usb_watch is watch:
            self.usb_watch = None
            if self.usb_restart_pending and not self.closing and self.usb_toggle.isChecked():
                QTimer.singleShot(0, self.start_usb_watch)
        if watch is not None:
            watch.deleteLater()

    def stop_usb_watch(self):
        if self.usb_watch:
            self.usb_watch.stop()
            if self.usb_watch.wait(4000):
                self.usb_watch.deleteLater()
                self.usb_watch = None

    def usb_observed(self, result):
        if self.closing or sys.platform != 'darwin' or not self.usb_toggle.isChecked():
            return
        if 'watcher_id' in result:
            if self.usb_watch is None or result['watcher_id'] != id(self.usb_watch):
                return
            if result['trigger'] and not self.usb_watch.current(result):
                return
        state = result['state']
        suffix = ' · baseline, no switch' if result.get('baseline') else ''
        self.usb_status.setText('HiFime SA9023 USB Audio · ' + state + suffix +
                               ' · arrival selects Mac · last request: ' + self.usb_destination +
                               (' · ' + result['message'] if result.get('message') else ''))
        self.state_changed.emit()
        if not result['trigger']:
            return
        if self.job or self.dirty:
            self.log.appendPlainText('USB arrived on Mac while busy or mappings are unsaved. Automatic switch skipped; no retry.')
            return
        self.usb_destination = 'Mac'
        self.log.appendPlainText('HiFime USB audio arrived on Mac. Requesting both enabled monitors to Mac.')
        self.start({'action':'switch', 'config':copy.deepcopy(self.config), 'target':'mac', 'automatic':True}, self.switched)

    def import_pairing(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Select MonitorSwitch-Pairing.json', '', 'JSON files (*.json)')
        if not path:
            return
        try:
            if Path(path).stat().st_size > 16384:
                raise ValueError('The connection file is too large.')
            pairing = json.loads(Path(path).read_text(encoding='utf-8'))
            from .relay import validate_pairing
            validate_pairing(pairing)
            self.start({'action': 'connect-relay', 'pairing': pairing}, self.paired)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, 'Windows connection', str(exc))

    def paired(self, result):
        pairing = result['pairing']
        profiles = {p['role']: p for p in pairing['monitors']}
        for card, role in zip(self.cards, ('dell', 'lg')):
            card.pair(pairing, profiles[role])
        self.usb_toggle.blockSignals(True)
        self.usb_toggle.setChecked(sys.platform == 'darwin')
        self.usb_toggle.blockSignals(False)
        if self.save():
            self.start_usb_watch()
            self.log.appendPlainText('Windows connection saved for Dell and LG. No input change was sent during pairing.')
        else:
            self.log.appendPlainText('Windows connection has not been saved. Automatic switching was not started.')

    def save(self):
        try:
            config = self.values()
            if not all(row.get('relay') for row in config['monitors']):
                config['usb_auto'] = False
                self.usb_toggle.blockSignals(True)
                self.usb_toggle.setChecked(False)
                self.usb_toggle.blockSignals(False)
                self.stop_usb_watch()
            validate_config(config)
            if not self.demo:
                save_config(config)
            self.config, self.dirty = config, False
            self.log.appendPlainText('Mappings saved.' if not self.demo else 'Demo mappings applied in memory.')
            self.statusBar().showMessage('Ready. Switch from the computer currently showing on the monitors.')
            self.shell.message('모니터 설정을 저장했습니다.')
            self.refresh()
            return True
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, 'Check mappings', str(exc))
            return False

    def start(self, request, callback):
        if self.job:
            return
        request['demo'] = self.demo
        self.pending_target=request['target'] if request['action']=='switch' else None
        self.job = Job(request, self)
        self.pending_callback = callback
        self.pending_result = None
        self.job.completed.connect(self.received)
        self.job.finished.connect(self.finished)
        self.log.appendPlainText('Working…' if request['action'] == 'switch' else 'Reading monitor information…')
        self.refresh()
        self.job.start()

    def received(self, result):
        self.pending_result = result

    def finished(self):
        job, self.job = self.job, None
        job.deleteLater()
        result = self.pending_result or {'error': 'Worker ended without a result.'}
        if 'error' in result:
            self.log.appendPlainText(result['error'])
            if getattr(self,'pending_target',None):self.shell.record_outcome(self.pending_target,[{'status':'uncertain'}])
            self.shell.message('작업을 완료하지 못했습니다. 활동 기록을 확인하세요.',True)
        else:
            self.pending_callback(result)
        self.refresh()

    def scan(self, probe):
        modes = {c.monitor.currentData(): c.mode.currentData() for c in self.cards}
        self.start({'action': 'probe' if probe else 'list', 'modes': modes, 'config':self.config}, self.scanned)

    def scanned(self, result):
        self.monitors = result['monitors']
        for card in self.cards:
            card.detected(self.monitors)
        self.log.appendPlainText(f"Detected {len(self.monitors)} monitor(s). Choose Dell and LG separately.")

    def capture(self):
        host = 'mac' if sys.platform == 'darwin' else 'windows'
        captured = 0
        for card in self.cards:
            if card.row.get('relay'):
                continue
            monitor = next((m for m in self.monitors if m['id'] == card.monitor.currentData()), None)
            if (monitor and monitor.get('current') is not None and not monitor.get('error')
                    and monitor.get('read_mode') == card.mode.currentData()):
                combo = card.mac if host == 'mac' else card.windows
                combo.setEditText(str(monitor['current']))
                captured += 1
        self.log.appendPlainText(f'Copied {captured} current input read(s) into {host} mappings. Save after checking.')

    def switch_now(self):
        self.switch_to(self.target)

    def switch_to(self, target):
        if self.job or self.dirty or self.closing:
            return
        try:
            validate_config(self.config, require_enabled=True)
        except ValueError as exc:
            self.log.appendPlainText(str(exc))
            return
        self.start({'action': 'switch', 'config': copy.deepcopy(self.config), 'target': target}, self.switched)

    def switched(self, result):
        self.shell.record_outcome(getattr(self,'pending_target',None),result['results'])
        self.shell.message(*switch_feedback(result['results']))
        for row in result['results']:
            prefix = 'SIMULATED · ' if self.demo else ''
            self.log.appendPlainText(f"{prefix}{row['label']}: {row['status']} · {row['message']}")
        if any(row['status'] == 'sent' for row in result['results']):
            self.log.appendPlainText('To return, use the Monitor Switch tray/menu bar icon on either computer. If unreachable, use the monitor OSD.')

    def shutdown(self):
        if self.job:
            self.log.appendPlainText('Wait for the current operation to finish (deadline: 30 seconds).')
            return False
        self.closing = True
        self.stop_usb_watch()
        if self.usb_watch is not None:
            self.closing = False
            self.log.appendPlainText('USB notifications are still stopping. Try Quit again shortly.')
            return False
        return True

    def closeEvent(self, event):
        if self.tray_managed and not self.closing:
            self.hide()
            event.ignore()
            return
        if self.job:
            self.log.appendPlainText('Wait for the current operation to finish (deadline: 30 seconds).')
            event.ignore()
        else:
            self.closing = True
            self.stop_usb_watch()
            if self.usb_watch is not None:
                self.log.appendPlainText('USB inventory is still finishing. Close again after it stops.')
                event.ignore()
                self.closing = False
                return
            event.accept()


def run(demo=False, background=False, update_health=None):
    from .tray import run as run_resident
    return run_resident(demo=demo, background=background, update_health=update_health)
