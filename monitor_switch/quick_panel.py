"""Own quick-panel gestures, lifetime, native frame, and placement together."""
import sys
import time

from PySide6.QtCore import QEvent, QPoint, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QPainter
from PySide6.QtWidgets import QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QPushButton, QSystemTrayIcon, QToolButton, QVBoxLayout

from .ui import (Destination, MonitorSummary, Toggle, color, colors, font,
                 icon, identify_icon, label, module, preference)


class QuickFooter(QPushButton):
    def __init__(self):
        super().__init__('설정 및 상태…');self.setObjectName('QuickFooter');self.setFixedHeight(27)

    def paintEvent(self,event):
        c=colors();p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.underMouse():
            p.setPen(Qt.PenStyle.NoPen);p.setBrush(color(c['control']));p.drawRoundedRect(QRectF(self.rect()),6,6)
        p.setFont(font(13));p.setPen(color(c['ink']))
        p.drawText(QRectF(6,0,self.width()-30,self.height()),Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter,self.text())
        icon('chevron-right',c['secondary'],14).paint(p,self.width()-20,(self.height()-14)//2,14,14)
        if self.hasFocus():
            p.setBrush(Qt.BrushStyle.NoBrush);p.setPen(color(c['accent']));p.drawRoundedRect(QRectF(self.rect()).adjusted(1,1,-1,-1),5,5)
        p.end()


class QuickPanel(QFrame):
    switch_requested=Signal(str);settings_requested=Signal();automatic_requested=Signal(bool)
    more_requested=Signal(QPoint)
    context_requested=Signal(QPoint)
    about_to_show=Signal()

    def __init__(self,host,parent=None):
        mac_panel=sys.platform=='darwin'
        flags=(Qt.WindowType.Tool if mac_panel else Qt.WindowType.Popup)|Qt.WindowType.FramelessWindowHint
        if sys.platform in ('darwin','win32'):
            # The rounded surface already draws its shadow. Windows' popup
            # and Cocoa's native shadow outline its transparent padding too.
            flags|=Qt.WindowType.NoDropShadowWindowHint
        super().__init__(parent,flags)
        self.mac_panel=mac_panel
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAccessibleName('Monitor Switch 빠른 전환');self.setWindowTitle('Monitor Switch · 빠른 전환');self._last_hidden=0
        outer=QVBoxLayout(self);outer.setContentsMargins(18,8,18,32)
        self.surface=module('PopupSurface');self.surface.setProperty('opaquePopup',mac_panel)
        self.surface.setFixedWidth(320);outer.addWidget(self.surface)
        shadow=QGraphicsDropShadowEffect(self.surface)
        # Keep the Mac shadow entirely inside the transparent window margins.
        shadow.setBlurRadius(16 if mac_panel else 36);shadow.setOffset(0,8 if mac_panel else 12)
        shadow.setColor(QColor(0,0,0,64));self.surface.setGraphicsEffect(shadow)
        layout=QVBoxLayout(self.surface);layout.setContentsMargins(10,10,10,10);layout.setSpacing(8)
        header=QHBoxLayout();header.setContentsMargins(6,4,6,2);header.setSpacing(8)
        title=label('Monitor Switch');title.setStyleSheet('font-weight:700');header.addWidget(title,1)
        self.settings=QToolButton();self.settings.setObjectName('QuickSettings');self.settings.setProperty('role','icon');self.settings.setIcon(icon('settings',colors()['secondary'],15))
        self.settings.setIconSize(QSize(15,15));self.settings.setFixedSize(18,18)
        identify_icon(self.settings,'settings','secondary',15);self.settings.setAccessibleName('설정 및 상태 열기')
        self.settings.clicked.connect(self.open_settings);header.addWidget(self.settings);layout.addLayout(header)
        targets=QHBoxLayout();targets.setSpacing(8)
        self.mac=Destination('mac',host=='windows',True);self.windows=Destination('windows',host=='mac',True)
        self.mac.clicked.connect(lambda:self.send('mac'));self.windows.clicked.connect(lambda:self.send('windows'))
        targets.addWidget(self.mac);targets.addWidget(self.windows);layout.addLayout(targets)
        self.feedback=label('','secondary');self.feedback.setObjectName('Feedback');self.feedback.hide();layout.addWidget(self.feedback)
        self.summary=MonitorSummary(True);layout.addWidget(self.summary)
        self.automatic=Toggle('USB 버튼 자동 전환');self.automatic.toggled.connect(self.automatic_requested.emit)
        auto_module=module('QuickModule');auto_layout=QVBoxLayout(auto_module);auto_layout.setContentsMargins(12,0,12,0)
        auto,self.auto_help=preference('USB 버튼 자동 전환','HiFime 오디오를 따라 전환',self.automatic)
        auto_layout.addWidget(auto);layout.addWidget(auto_module)
        self.footer=QuickFooter()
        self.footer.clicked.connect(self.open_settings);layout.addWidget(self.footer)
        self.setFixedWidth(356)

    def activate_tray(self, reason, anchor):
        if sys.platform=='darwin':
            if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                          QSystemTrayIcon.ActivationReason.DoubleClick,
                          QSystemTrayIcon.ActivationReason.Context,
                          QSystemTrayIcon.ActivationReason.Unknown):
                # Cocoa can emit several activations for the same gesture.
                # Every supported mouse button opens the panel idempotently.
                self.show_at(anchor)
        elif reason==QSystemTrayIcon.ActivationReason.Context:
            self.dismiss()
            self.context_requested.emit(QCursor.pos())
        elif reason in (QSystemTrayIcon.ActivationReason.Trigger,
                        QSystemTrayIcon.ActivationReason.DoubleClick):
            self.toggle(anchor)

    def toggle(self, anchor):
        if self.isVisible():
            self.dismiss()
        else:
            self.show_at(anchor)

    def reopen(self, anchor):
        if sys.platform=='darwin':
            self.show_at(anchor)
        else:
            self.open_settings()

    def dismiss(self):
        self.hide()

    def set_last_target(self,target):
        self.mac.set_selected(target=='mac');self.windows.set_selected(target=='windows')

    def send(self,target):self.hide();self.switch_requested.emit(target)
    def open_settings(self):self.hide();self.settings_requested.emit()

    def hideEvent(self,event):self._last_hidden=time.monotonic();super().hideEvent(event)

    def keyPressEvent(self,event):
        if event.key()==Qt.Key.Key_Escape:self.hide()
        else:super().keyPressEvent(event)

    def event(self,event):
        if (getattr(self,'mac_panel',False) and event.type()==QEvent.Type.WindowDeactivate
                and self.isVisible()):
            self.hide()
        return super().event(event)

    def mousePressEvent(self,event):
        if self.mac_panel and not self.surface.geometry().contains(event.position().toPoint()):
            self.hide();event.accept()
        else:super().mousePressEvent(event)

    def show_at(self,anchor):
        if sys.platform=='darwin':
            owner=self.parentWidget()
            if owner is not None:owner.hide()
        elif self.isVisible() or time.monotonic()-self._last_hidden<0.15:
            return
        self.about_to_show.emit()
        self.adjustSize();screen=QGuiApplication.screenAt(anchor.center()) or QGuiApplication.primaryScreen();area=screen.availableGeometry()
        x=max(area.left()+4,min(anchor.center().x()-self.width()//2,area.right()-self.width()-4))
        y=anchor.bottom() if anchor.center().y()<area.center().y() else anchor.top()-self.height()
        y=max(area.top(),min(y,area.bottom()-self.height()))
        if self.mac_panel and QGuiApplication.platformName()=='cocoa':
            from .macos import activate_application
            activate_application()
        self.move(x,y);self.show();self.raise_();self.activateWindow()
        (self.windows if self.windows.property('primary') else self.mac).setFocus()
