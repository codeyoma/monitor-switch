"""Resident menu bar/tray UI with per-user login registration."""
import hashlib
import os
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QObject, QLockFile, QThread, QTimer, Signal, Qt, QUrl, QRect, QSize
from PySide6.QtGui import QAction, QColor, QDesktopServices, QIcon, QPainter, QPen, QPixmap, QCursor, QKeySequence
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QCheckBox, QPlainTextEdit, QFileDialog, QMessageBox, QMenu, QSystemTrayIcon, QGroupBox, QFormLayout)

from .startup import LoginStartup, data_directory, login_command
from .ui import ASSETS, Shell, Toggle, apply_theme, colors, icon, input_name, label, module, divider, switch_feedback

from .quick_panel import QuickPanel

ROOT = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent.parent))


def update_health_ready(window):
    if window.job or getattr(window, 'closing', False) or getattr(window, 'load_failed', False):
        return False
    if isinstance(window, WindowsWindow):
        service = window.service
        return bool(window.ready and service.server_thread and service.server_thread.is_alive())
    return True


def application_icon(template=False):
    result=icon('monitor','#000000' if template else colors()['accent'],32)
    result.setIsMask(template)
    return result


class SingleInstance(QObject):
    show_requested = Signal()

    def __init__(self, data, demo=False):
        super().__init__()
        data = Path(data)
        data.mkdir(parents=True,exist_ok=True)
        name = 'demo' if demo else 'resident'
        self.lock = QLockFile(str(data/f'{name}.lock'))
        digest = hashlib.sha256(str(data.resolve()).encode()).hexdigest()[:16]
        if sys.platform == 'darwin':
            private = Path(tempfile.gettempdir())/f'monitorswitch-{os.getuid()}'
            private.mkdir(mode=0o700,exist_ok=True)
            if private.is_symlink() or private.stat().st_uid != os.getuid():
                raise RuntimeError('The application IPC directory is not owned by this user.')
            private.chmod(0o700)
            self.name = str(private/f'{digest}-{name}')
        else:
            self.name = f'MonitorSwitch-{digest}-{name}'
        self.server = None
        self.connections = []

    def acquire(self, background=False):
        if not self.lock.tryLock(0):
            if not background:
                socket = QLocalSocket(self)
                socket.connectToServer(self.name)
                if socket.waitForConnected(800):
                    socket.write(b'SHOW\n')
                    socket.waitForBytesWritten(800)
                socket.disconnectFromServer()
            return False
        QLocalServer.removeServer(self.name)
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        if not self.server.listen(self.name):
            self.lock.unlock()
            raise RuntimeError('Cannot create the application control socket: '+self.server.errorString())
        self.server.newConnection.connect(self.connected)
        return True

    def connected(self):
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            # Keep accepted sockets owned by the instance until deferred cleanup.
            socket.setParent(self)
            self.connections.append(socket)
            socket.readyRead.connect(self.read_request)
            socket.disconnected.connect(self.retire_connection)
            self.read_socket(socket)

    def read_request(self):
        self.read_socket(self.sender())

    def read_socket(self, socket):
        if socket.canReadLine():
            if bytes(socket.readLine(32)).strip() == b'SHOW':
                self.show_requested.emit()
            socket.disconnectFromServer()

    def retire_connection(self):
        socket = self.sender()
        if socket in self.connections:
            self.connections.remove(socket)
        socket.deleteLater()

    def close(self):
        if self.server:
            self.server.close()
        for socket in self.connections[:]:
            socket.abort()
        self.lock.unlock()


class Task(QThread):
    completed = Signal(object)

    def __init__(self, action, parent):
        super().__init__(parent)
        self.action = action

    def run(self):
        try:
            self.completed.emit({'value':self.action()})
        except Exception as exc:
            self.completed.emit({'error':str(exc)})


class WindowsWindow(QMainWindow):
    state_changed = Signal()
    service_event = Signal(object)

    def __init__(self, root=ROOT, service_factory=None):
        super().__init__()
        self.job, self.service, self.ready, self.closing = None, None, False, False
        self.tray_managed, self.dirty, self.demo = True, False, False
        self.root,self.native_busy=Path(root),False
        self.setWindowTitle('Monitor Switch · Windows')
        self.resize(680,663)
        self.setMinimumSize(640,600)
        self.shell=Shell('windows')
        self.setCentralWidget(self.shell)
        self.shell.attach_toolbar(self)
        self.status=QLabel('시작 중…')
        self.switch_button,self.return_button=self.shell.mac,self.shell.windows
        self.switch_button.clicked.connect(lambda:self.switch_to('mac'))
        self.return_button.clicked.connect(lambda:self.switch_to('windows'))
        self.usb_toggle,self.usb_status,self.log=self.shell.automatic,self.shell.usb_status,self.shell.log
        self.usb_toggle.toggled.connect(self.auto_changed)
        self.export_button=self.shell.pair
        self.export_button.clicked.connect(self.export_pairing)
        folder=QPushButton('설정 및 검사 파일 폴더 열기')
        folder.setProperty('role','link')
        folder.setIcon(icon('folder',colors()['secondary'],16))
        folder.clicked.connect(lambda:QDesktopServices.openUrl(QUrl.fromLocalFile(str(data_directory()))))
        self.shell.activity_page.layout().addWidget(folder)
        mapping=QPushButton('입력 매핑 파일 열기')
        mapping.setProperty('role','link')
        mapping.clicked.connect(lambda:QDesktopServices.openUrl(QUrl.fromLocalFile(str(data_directory()/'relay-monitors.json'))))
        self.shell.monitor_layout.addWidget(mapping)
        self.shell.message('윈도우 중계를 시작하고 있습니다.')
        self.service_event.connect(self.observed)
        if service_factory is None:
            from .service import RelayService
            service_factory = RelayService
        self.service = service_factory(root, self.service_event.emit)
        self.start_task(self.service.start, self.started)

    def refresh(self):
        available = self.ready and self.job is None and not self.closing and not self.native_busy
        for control in (self.switch_button,self.return_button,self.usb_toggle,self.export_button):
            control.setEnabled(available)
        if self.service and hasattr(self.service,'profiles'):
            self.shell.update_rows([dict(p,enabled=True) for p in self.service.profiles])
        self.shell.update_usb(self.usb_status.text(),self.usb_toggle.isChecked())
        self.state_changed.emit()

    def start_task(self, action, callback=lambda result:None):
        if self.job:
            return
        self.job = Task(action,self)
        self.result, self.callback = None, callback
        self.job.completed.connect(lambda result:setattr(self,'result',result))
        self.job.finished.connect(self.finished)
        self.refresh()
        self.job.start()

    def finished(self):
        job, self.job = self.job, None
        job.deleteLater()
        result = self.result or {'error':'Operation ended without a result.'}
        if 'error' in result:
            self.log.appendPlainText(result['error'])
            self.status.setText('오류 · 설정 및 상태를 확인하세요')
            self.shell.message('작업을 완료하지 못했습니다. 활동 기록을 확인하세요.',True)
            self.closing = False
        else:
            self.callback(result['value'])
        if self.ready:
            self.usb_toggle.blockSignals(True)
            self.usb_toggle.setChecked(self.service.auto_enabled.is_set())
            self.usb_toggle.blockSignals(False)
        self.refresh()

    def started(self, _service):
        self.ready = True
        self.usb_toggle.blockSignals(True)
        self.usb_toggle.setChecked(self.service.auto_enabled.is_set())
        self.usb_toggle.blockSignals(False)
        self.status.setText('실행 중 · 두 컴퓨터가 같은 네트워크에 연결되어 있어야 합니다')
        self.shell.message('윈도우 중계 실행 중 · 현재 입력은 직접 확인하세요')
        for profile in self.service.profiles:
            card=QWidget();layout=QVBoxLayout(card);layout.setContentsMargins(0,0,0,0);layout.setSpacing(8)
            title=label(profile['label'],'group');title.setToolTip(profile['id']);layout.addWidget(title)
            group=module('SettingsGroup');fields=QVBoxLayout(group);fields.setContentsMargins(12,0,12,0);fields.setSpacing(0)
            included=Toggle('전환에 포함');included.setChecked(True);included.setEnabled(False)
            values=[('전환에 포함',included),('제어 방식',label('LG 대체 방식' if profile['mode']=='lg-alt' else 'Standard DDC/CI · VCP 0x60','secondary'))]
            for target,title in [('mac','Mac 입력'),('windows','Windows 입력')]:
                code=profile[target];values.append((title,label(input_name(profile,code)+f' · {code} / 0x{code:02X}','secondary')))
            for index,(title,value) in enumerate(values):
                if index:fields.addWidget(divider())
                row=QWidget();row_layout=QHBoxLayout(row);row_layout.setContentsMargins(0,10,0,10)
                row_layout.addWidget(label(title),1);row_layout.addWidget(value);fields.addWidget(row)
            layout.addWidget(group)
            layout.addWidget(label('이 중계 프로필은 입력 매핑 파일에서 수정할 수 있습니다.','secondary'))
            self.shell.monitor_tabs.addTab(card,'Dell' if profile['role']=='dell' else 'LG')
        self.shell.update_rows([dict(p,enabled=True) for p in self.service.profiles])
        self.shell.monitor_layout.addStretch(1)

    def observed(self, event):
        kind = event['kind']
        if kind == 'usb':
            result = event['result']
            baseline = ' · 기준 상태, 전환 없음' if result.get('baseline') else ''
            self.usb_status.setText('HiFime SA9023 USB Audio · '+result['state']+baseline+
                                   (' · '+result['message'] if result.get('message') else ''))
        elif kind == 'busy':
            self.native_busy=True
            self.shell.message(('Mac' if event['target']=='mac' else 'Windows')+' 전환 명령을 보내는 중입니다.')
            self.status.setText(('맥' if event['target']=='mac' else '윈도우')+'으로 전환 중…')
        elif kind == 'result':
            self.native_busy=False
            self.shell.record_outcome(event['target'],event['result'].get('results',[]))
            self.shell.message(*switch_feedback(event['result'].get('results',[])))
            profiles = {p['id']:p['label'] for p in self.service.profiles}
            rows = event['result'].get('results',[])
            self.status.setText('마지막 요청: '+event['target']+' · '+', '.join(p['status'] for p in rows))
            for row in rows:
                self.log.appendPlainText(profiles.get(row['id'],row['id'])+': '+row['status']+
                                         (' · '+(row.get('message') or row.get('error')) if row.get('message') or row.get('error') else ''))
        elif event.get('message'):
            self.log.appendPlainText(event['message'])
        self.refresh()

    def switch_to(self, target):
        if self.ready and not self.closing:
            self.start_task(lambda:self.service.manual(target))

    def auto_changed(self, enabled):
        if self.job or not self.ready:
            return
        def change():
            self.service.set_auto(enabled)
        self.start_task(change, lambda _value:None)

    def export_pairing(self):
        path, _ = QFileDialog.getSaveFileName(self,'맥 연결 파일 저장',
                                             str(Path.home()/'Desktop/MonitorSwitch-Pairing.json'),'JSON (*.json)')
        if path:
            self.start_task(lambda:self.service.export_pairing(path),
                            lambda _value:self.log.appendPlainText('연결 파일 저장 완료. 이 파일을 맥에서 가져오세요.'))

    def shutdown(self, finished):
        if self.job:
            self.log.appendPlainText('진행 중인 작업이 끝난 뒤 종료하세요.')
            return False
        self.closing = True
        self.start_task(self.service.stop,lambda _value:finished())
        return True

    def closeEvent(self,event):
        if self.tray_managed:
            self.hide();event.ignore()
        else:
            event.accept()


class Resident(QObject):
    def __init__(self, window, startup=None, enable_updates=False):
        super().__init__(window)
        self.window, self.startup = window, startup
        self.quitting = False
        window.tray_managed = True
        self.icon = QSystemTrayIcon(application_icon(sys.platform=='darwin'),self)
        self.icon.setToolTip('Monitor Switch')
        self.panel=QuickPanel('mac' if sys.platform=='darwin' else 'windows',window)
        self.panel.switch_requested.connect(window.switch_to)
        self.panel.settings_requested.connect(self._show_window)
        self.panel.about_to_show.connect(self.refresh)
        self.panel.context_requested.connect(self.show_context)
        self.panel.automatic_requested.connect(lambda checked:window.usb_toggle.setChecked(checked))
        self.panel.more_requested.connect(self.show_more)
        window.shell.quick_button.clicked.connect(self.toggle_panel)
        self.menu=QMenu(window)
        self.status_action=self.menu.addAction('마지막 요청: 없음');self.status_action.setEnabled(False)
        self.menu.addSeparator()
        self.mac=self.menu.addAction('Mac으로 전환',lambda:window.switch_to('mac'))
        self.windows=self.menu.addAction('Windows로 전환',lambda:window.switch_to('windows'))
        self.menu.addSeparator()
        self.automatic=self.menu.addAction('USB 버튼 자동 전환');self.automatic.setCheckable(True)
        self.automatic.triggered.connect(lambda checked:window.usb_toggle.setChecked(checked))
        self.login=self.menu.addAction('로그인 시 자동 실행');self.login.setCheckable(True)
        self.login.triggered.connect(self.change_login);window.shell.login.toggled.connect(self.change_login)
        self.menu.addSeparator()
        self.settings_action=self.menu.addAction('설정 및 상태…',self.show_window)
        self.pair_action=QAction('맥 연결 파일 내보내기…' if isinstance(window,WindowsWindow) else '윈도우 연결 가져오기…',self)
        self.pair_action.triggered.connect(window.export_pairing if isinstance(window,WindowsWindow) else window.import_pairing)
        self.menu.addAction(self.pair_action);self.menu.addSeparator()
        self.quit_action=self.menu.addAction('Monitor Switch 종료',self.quit)
        self.quit_action.setMenuRole(QAction.MenuRole.QuitRole)
        for action,key in [(self.mac,'M'),(self.windows,'W')]:
            action.setShortcut(QKeySequence(('Ctrl+Alt+Meta+' if sys.platform=='darwin' else 'Ctrl+Alt+Shift+')+key))
        self.settings_action.setShortcut(QKeySequence('Ctrl+,'));self.quit_action.setShortcut(QKeySequence('Ctrl+Q'))
        for action in (self.mac,self.windows,self.settings_action,self.quit_action):
            action.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut);window.addAction(action);self.panel.addAction(action)
            action.setToolTip('앱이 활성화되어 있을 때 사용할 수 있는 단축키입니다.')
        for target,action in [('mac',self.mac),('windows',self.windows)]:
            button=window.shell.mac if target=='mac' else window.shell.windows
            button.shortcut_hint=action.shortcut().toString(QKeySequence.SequenceFormat.NativeText);button.update()
        self.more_menu=QMenu(self.panel)
        self.more_menu.addAction(self.login)
        self.more_menu.addAction(self.pair_action)
        self.more_menu.addSeparator()
        self.more_menu.addAction(self.quit_action)
        # Cocoa emits activated inside its status-button mouse-down handler.
        # Open after that native event finishes, so Qt cannot deliver the same
        # press to the new popup as an outside click and immediately close it.
        self.icon.activated.connect(self.activated,Qt.ConnectionType.QueuedConnection)
        self.menu.aboutToShow.connect(self.refresh)
        self.more_menu.aboutToShow.connect(self.refresh)
        window.state_changed.connect(self.refresh)
        self.icon.show()
        self.refresh()
        self.updater = None
        self.update_stage = None
        if enable_updates:
            from .update_ui import UpdateController
            self.updater = UpdateController(self)

    def _panel_anchor(self):
        anchor=self.icon.geometry()
        return anchor if not anchor.isEmpty() else QRect(QCursor.pos(),QSize(1,1))

    def activated(self,reason):
        self.panel.activate_tray(reason,self._panel_anchor())

    def toggle_panel(self):
        self.panel.toggle(self._panel_anchor())

    def show_panel(self):
        self.panel.show_at(self._panel_anchor())

    def show_context(self,point):
        self.refresh();self.menu.popup(point)

    def show_more(self,point):
        self.refresh();self.more_menu.popup(point)

    def show_window(self):
        self.panel.open_settings()

    def _show_window(self):
        self.window.showNormal();self.window.raise_();self.window.activateWindow()

    def show_default(self):
        self.panel.reopen(self._panel_anchor())

    def refresh(self):
        w = self.window
        if isinstance(w,WindowsWindow) and self.quitting and not w.closing:
            self.quitting = False
            if getattr(self, 'update_stage', None) and self.updater:
                self.update_stage = None
                self.updater.pending = None
                self.updater.failed('The helper could not stop safely. Try checking for updates again.')
        ready = w.switch_button.isEnabled() and not self.quitting
        self.mac.setEnabled(ready);self.windows.setEnabled(ready)
        self.automatic.setEnabled(w.usb_toggle.isEnabled() and not self.quitting)
        self.automatic.setChecked(w.usb_toggle.isChecked())
        self.login.setEnabled(self.startup is not None and not self.quitting)
        if self.startup:
            try:
                self.login.setChecked(self.startup.enabled())
            except Exception:
                self.login.setChecked(False)
        w.shell.login.blockSignals(True)
        w.shell.login.setEnabled(self.login.isEnabled())
        w.shell.login.setChecked(self.login.isChecked())
        w.shell.login.blockSignals(False)
        rows=([dict(p,enabled=True) for p in w.service.profiles] if isinstance(w,WindowsWindow) and hasattr(w.service,'profiles')
              else w.config['monitors'] if hasattr(w,'config') else [])
        self.panel.summary.update_rows(rows)
        self.panel.set_last_target(w.shell.last_target)
        self.status_action.setText("마지막 요청: "+(("Mac" if w.shell.last_target=="mac" else "Windows") if w.shell.last_target else "결과 확인 필요" if w.shell.last_outcome else "없음"))
        self.panel.mac.setEnabled(ready);self.panel.windows.setEnabled(ready)
        self.panel.automatic.blockSignals(True)
        self.panel.automatic.setEnabled(self.automatic.isEnabled())
        self.panel.automatic.setChecked(self.automatic.isChecked())
        self.panel.automatic.blockSignals(False)
        busy=bool(w.job or getattr(w,'native_busy',False))
        warning=bool(w.shell.feedback.property('warning'))
        self.panel.feedback.setText('작업 중입니다. 잠시 기다려 주세요.' if busy else w.shell.feedback.text())
        self.panel.feedback.setProperty('warning',warning)
        self.panel.feedback.style().unpolish(self.panel.feedback);self.panel.feedback.style().polish(self.panel.feedback)
        self.panel.feedback.setVisible(busy or warning)
        self.pair_action.setEnabled(w.export_button.isEnabled() if isinstance(w,WindowsWindow) else w.pair_button.isEnabled())
        text=w.usb_status.text()
        w.shell.update_usb(text,w.usb_toggle.isChecked())
        self.panel.auto_help.setText('USB 오디오 중복 · 자동 전환 보류' if 'ambiguous' in text else '활동 기록에서 USB 상태를 확인하세요' if any(v in text.lower() for v in ('error','failed','failure')) else 'HiFime 오디오를 따라 전환')
        self.icon.setToolTip('Monitor Switch · '+('전환 중' if w.job or getattr(w,'native_busy',False) else 'USB 자동 전환 켜짐' if w.usb_toggle.isChecked() else 'USB 자동 전환 꺼짐'))

    def change_login(self, enabled):
        try:
            self.startup.set_enabled(enabled)
        except Exception as exc:
            QMessageBox.warning(self.window,'자동 실행',str(exc))
        self.refresh()

    def quit(self):
        if self.quitting:
            return
        if self.updater and self.updater.job:
            self.updater.cancelling = True
            self.updater.job.cancel()
            self.updater.job.finished.connect(self.quit)
            return
        if isinstance(self.window,WindowsWindow):
            if not self.window.shutdown(self.finish_quit):
                self.show_window()
                return
            self.quitting = True
            self.refresh()
        elif self.window.shutdown():
            self.finish_quit()
        else:
            self.show_window()

    def begin_update(self, stage):
        self.update_stage = stage
        if isinstance(self.window, WindowsWindow):
            if not self.window.shutdown(self.finish_update):
                self.update_stage = None
                return False
            self.quitting = True
            self.refresh()
            return True
        if self.window.shutdown():
            self.finish_update()
            return True
        self.update_stage = None
        return False

    def finish_update(self):
        try:
            from .update_install import spawn_helper
            spawn_helper(self.update_stage)
        except Exception as exc:
            if self.updater:
                self.updater.failed(str(exc))
            self.update_stage = None
            # Native watchers have stopped. Restarting is safer than leaving a
            # partially stopped resident that looks operational.
            self.finish_quit()
            return
        self.finish_quit()

    def finish_quit(self):
        self.quitting = True
        self.panel.dismiss()
        self.icon.hide()
        QApplication.instance().quit()

    def cleanup(self):
        if self.updater:
            self.updater.close()
        self.panel.dismiss()
        self.icon.hide()
        w = self.window
        if isinstance(w,WindowsWindow):
            if w.job:
                w.job.wait(20000)
            w.service.stop()
        else:
            if w.job:
                w.job.wait(32000)
            w.closing = True
            w.stop_usb_watch()


def run(demo=False, background=False, update_health=None):
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName('Monitor Switch')
    app.setOrganizationName('MonitorSwitch')
    from .version import VERSION
    app.setApplicationVersion(VERSION)
    app.setQuitOnLastWindowClosed(False)
    if sys.platform=='darwin':
        app.setWindowIcon(QIcon(str(ASSETS/'app/MonitorSwitch.png')))
    apply_theme(app)
    app.styleHints().colorSchemeChanged.connect(lambda _scheme:apply_theme(app))
    instance = SingleInstance(data_directory(), demo=demo)
    if not instance.acquire(background):
        return 0
    if sys.platform == 'win32' and not demo:
        window = WindowsWindow()
    else:
        from .gui import Window
        window = Window(demo=demo)
    startup = None
    if not demo:
        try:
            startup = LoginStartup(login_command(ROOT,install=True))
            startup.ensure_first_run()
        except Exception as exc:
            window.log.appendPlainText('자동 실행 설정 실패: '+str(exc))
            # Show a reviewable error on a foreground launch; background launch stays quiet.
            if not background:
                window.show()
    resident = Resident(window,startup,enable_updates=not demo)
    instance.show_requested.connect(resident.show_default)
    app.aboutToQuit.connect(resident.cleanup)
    app.aboutToQuit.connect(instance.close)
    if not background:
        resident.show_default()
    if update_health:
        import re
        from .startup import write_json
        if not re.fullmatch('[0-9a-f]{32}', update_health):
            raise ValueError('Invalid update health token.')
        health_state = {'elapsed': False, 'sent': False}
        def report_healthy():
            if health_state['elapsed'] and not health_state['sent'] and update_health_ready(window):
                write_json(data_directory()/f'update-health-{update_health}.json', {'version': VERSION, 'pid': os.getpid()})
                health_state['sent'] = True
        def minimum_startup_elapsed():
            health_state['elapsed'] = True
            report_healthy()
        window.state_changed.connect(report_healthy)
        QTimer.singleShot(3000, minimum_startup_elapsed)
    # References stay alive for the lifetime of QApplication.exec().
    result = app.exec()
    return result
