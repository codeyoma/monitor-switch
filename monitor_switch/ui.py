"""Qt presentation mapped from the user's updated Pencil screens."""
from functools import lru_cache
from pathlib import Path
import sys
import time

from PySide6.QtCore import QByteArray, QEvent, QPoint, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (QColor, QFont, QFontDatabase, QGuiApplication, QIcon,
    QLinearGradient, QPainter, QPalette, QPen, QPixmap)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QComboBox,
    QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QPlainTextEdit,
    QPushButton, QScrollArea, QSizePolicy, QStackedWidget, QStyle, QStyleOptionComboBox,
    QStylePainter, QTabBar, QTabWidget, QToolBar, QToolButton, QVBoxLayout, QWidget)

ASSETS=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent.parent))/'assets'
LIGHT=dict(surface='#FFFFFF',canvas='#F2F2F4',ink='#1D1D1F',secondary='#6E6E73',
    line='#0000001A',accent='#007AFF',soft='#007AFF1A',control='#7878801F',
    material='#ECECEEF5',module='#FFFFFF',hover='#E8E8ED',warning='#995E00')
DARK=dict(surface='#2B2B2D',canvas='#1E1E20',ink='#F5F5F7',secondary='#98989D',
    line='#FFFFFF1A',accent='#0A84FF',soft='#0A84FF2E',control='#78788052',
    material='#1C1C1EF2',module='#FFFFFF12',hover='#38383B',warning='#E5B866')


def switch_feedback(results):
    if not results or any(row.get('status') not in ('sent','skipped') for row in results):
        return '일부 모니터의 전환 결과를 확인해야 합니다. 활동 기록을 확인하세요.', True
    if all(row.get('status') == 'skipped' for row in results):
        return '중복 요청이라 화면 전환 명령을 생략했습니다.', False
    return '전환 명령을 보냈습니다. 실제 화면을 확인하세요.', False


def colors():
    app=QApplication.instance()
    scheme=app.styleHints().colorScheme()
    dark=scheme==Qt.ColorScheme.Dark or (scheme==Qt.ColorScheme.Unknown and
        app.palette().color(QPalette.ColorRole.Window).lightness()<128)
    return DARK if dark else LIGHT


def color(value):
    # Pencil stores eight-digit colors as RGBA; QColor's hex form uses ARGB.
    if len(value)==9:
        return QColor(*[int(value[index:index+2],16) for index in (1,3,5,7)])
    return QColor(value)


def css(value):
    c=color(value)
    return f'rgba({c.red()},{c.green()},{c.blue()},{c.alpha()})'


@lru_cache(maxsize=128)
def icon(name,tint='#6E6E73',size=24):
    raw=(ASSETS/'icons'/f'{name}.svg').read_bytes().replace(b'currentColor',tint.encode())
    renderer=QSvgRenderer(QByteArray(raw));pixmap=QPixmap(size*2,size*2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter=QPainter(pixmap);renderer.render(painter);painter.end()
    pixmap.setDevicePixelRatio(2)
    return QIcon(pixmap)


def font(size,weight=QFont.Weight.Normal):
    result=QFont(QApplication.font());result.setPixelSize(size);result.setWeight(weight)
    return result


def apply_theme(app):
    if not app.property('monitorSwitchFontLoaded'):
        identifier=QFontDatabase.addApplicationFont(str(ASSETS/'fonts/NotoSansKR.ttf'))
        if identifier>=0:app.setFont(QFont(QFontDatabase.applicationFontFamilies(identifier)[0],10))
        app.setProperty('monitorSwitchFontLoaded',True)
    c=colors();palette=app.palette()
    for role,key in [(QPalette.ColorRole.Window,'canvas'),(QPalette.ColorRole.Base,'surface'),
        (QPalette.ColorRole.Text,'ink'),(QPalette.ColorRole.WindowText,'ink'),
        (QPalette.ColorRole.Button,'surface'),(QPalette.ColorRole.ButtonText,'ink'),
        (QPalette.ColorRole.Highlight,'accent')]:palette.setColor(role,color(c[key]))
    palette.setColor(QPalette.ColorRole.HighlightedText,QColor('white'));app.setPalette(palette)
    s={key:css(value) for key,value in c.items()}
    s['opaque_material']=color(c['material']).name()
    app.setStyleSheet(f'''
        QWidget {{ color:{s['ink']}; font-size:13px; }}
        QMainWindow, QWidget#Shell, QWidget#Page {{ background:{s['canvas']}; }}
        QLabel {{ background:transparent; }}
        QLabel[role="secondary"] {{ color:{s['secondary']}; font-size:11px; }}
        QLabel[role="heading"] {{ font-size:22px; font-weight:700; }}
        QLabel[role="group"] {{ font-size:13px; font-weight:600; }}
        QLabel[role="toolbarTitle"] {{ font-size:13px; font-weight:700; }}
        QToolBar {{ background:{s['canvas']}; border:0; border-bottom:1px solid {s['line']}; padding:0; spacing:0; }}
        QFrame#Toolbar {{ background:{s['canvas']}; }}
        QFrame#Navigation {{ background:{s['control']}; border-radius:7px; }}
        QTabBar#NavTabs::tab {{ background:transparent; color:{s['ink']}; padding:3px 8px; margin:0; border:0; border-radius:5px; font-size:12px; }}
        QTabBar#NavTabs::tab:selected {{ background:{s['surface']}; font-weight:600; }}
        QFrame#Module, QFrame#SettingsGroup {{ background:{s['module']}; border:1px solid {s['line']}; border-radius:10px; }}
        QFrame#PopupSurface {{ background:{s['material']}; border:1px solid {s['line']}; border-radius:16px; }}
        QFrame#PopupSurface[opaquePopup="true"] {{ background:{s['opaque_material']}; }}
        QFrame#QuickModule {{ background:{s['module']}; border:0; border-radius:12px; }}
        QFrame#Divider {{ background:{s['line']}; border:0; }}
        QPushButton, QToolButton {{ background:{s['surface']}; border:1px solid {s['line']}; border-radius:6px; padding:4px 12px; }}
        QPushButton:hover, QToolButton:hover {{ background:{s['hover']}; }}
        QPushButton:focus, QToolButton:focus {{ border:1px solid {s['accent']}; }}
        QPushButton:disabled, QToolButton:disabled {{ color:{s['secondary']}; }}
        QPushButton[role="primary"] {{ background:{s['accent']}; color:white; border-color:{s['accent']}; font-weight:500; }}
        QPushButton[role="primary"]:disabled {{ background:{s['control']}; color:{s['secondary']}; border-color:transparent; }}
        QToolButton[role="icon"] {{ background:transparent; border:0; padding:4px; }}
        QToolButton[role="icon"]:hover {{ background:{s['control']}; }}
        QToolButton[role="icon"]:focus {{ border:1px solid {s['accent']}; }}
        QToolButton#QuickSettings {{ padding:0; }}
        QPushButton#QuickFooter {{ background:transparent; border:0; padding:4px 6px; text-align:left; }}
        QPushButton#QuickFooter:hover {{ background:{s['control']}; }}
        QLabel#Feedback {{ color:{s['secondary']}; font-size:11px; padding:0; background:transparent; }}
        QLabel#Feedback[warning="true"] {{ color:{s['warning']}; }}
        QScrollArea {{ background:{s['canvas']}; border:0; }}
        QTabWidget::pane {{ border:0; }}
        QComboBox {{ color:{s['secondary']}; background:transparent; border:0; padding:3px 18px 3px 0; min-height:21px; }}
        QComboBox::drop-down {{ border:0; width:16px; }}
        QComboBox QAbstractItemView {{ background:{s['surface']}; color:{s['ink']}; selection-background-color:{s['accent']}; selection-color:white; }}
        QComboBox QLineEdit {{ background:transparent; border:0; padding:0; color:{s['secondary']}; }}
        QPlainTextEdit {{ background:{s['surface']}; color:{s['ink']}; border:1px solid {s['line']}; border-radius:10px; padding:12px; }}
        QMenu {{ background:{s['material']}; color:{s['ink']}; border:1px solid {s['line']}; border-radius:10px; padding:5px; }}
        QMenu::item {{ padding:3px 22px 3px 24px; border-radius:5px; font-size:13px; }}
        QMenu::item:selected {{ background:{s['accent']}; color:white; }}
        QMenu::item:disabled {{ color:{s['secondary']}; }}
        QMenu::separator {{ height:1px; background:{s['line']}; margin:5px 10px; }}
        QStatusBar {{ background:{s['canvas']}; color:{s['secondary']}; font-size:11px; }}
    ''')
    for widget in app.allWidgets():
        if isinstance(widget,(Destination,DisplayTile,Badge)):widget.update()
        elif widget.property('assetName'):
            value=icon(widget.property('assetName'),c[widget.property('assetRole')],widget.property('assetSize'))
            if isinstance(widget,QLabel):widget.setPixmap(value.pixmap(widget.property('assetSize'),widget.property('assetSize')))
            elif isinstance(widget,QToolButton):widget.setIcon(value)


def identify_icon(widget,name,role,size):
    widget.setProperty('assetName',name);widget.setProperty('assetRole',role);widget.setProperty('assetSize',size)


def label(text,role=None):
    result=QLabel(text);result.setWordWrap(True)
    if role:result.setProperty('role',role)
    return result


def divider():
    result=QFrame();result.setObjectName('Divider');result.setFixedHeight(1)
    return result


def module(name='Module'):
    result=QFrame();result.setObjectName(name)
    return result


class Badge(QWidget):
    def __init__(self,name='monitor',tint='accent',size=24,roundness=6):
        super().__init__();self.name,self.tint,self.roundness=name,tint,roundness
        self.setFixedSize(size,size)

    def paintEvent(self,event):
        c=colors();p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen);p.setBrush(color(c.get(self.tint,self.tint)))
        p.drawRoundedRect(QRectF(self.rect()),self.roundness,self.roundness)
        size=12 if self.width()==24 and self.roundness>6 else 14
        icon(self.name,'#FFFFFF',size).paint(p,(self.width()-size)//2,(self.height()-size)//2,size,size)
        p.end()


class Toggle(QCheckBox):
    def __init__(self,text='',parent=None):
        super().__init__(text,parent);self.setFixedSize(34,25)
        self.setCursor(Qt.CursorShape.PointingHandCursor);self.setAccessibleName(text)

    def hitButton(self,point):return self.rect().contains(point)

    def paintEvent(self,event):
        c=colors();p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.isEnabled():p.setOpacity(.55)
        p.setPen(Qt.PenStyle.NoPen);p.setBrush(color(c['accent'] if self.isChecked() else c['control']))
        p.drawRoundedRect(QRectF(1,3,32,19),9.5,9.5)
        p.setBrush(QColor('white'));p.drawEllipse(QRectF(16 if self.isChecked() else 3,5,15,15))
        if self.hasFocus():
            p.setBrush(Qt.BrushStyle.NoBrush);p.setPen(color(c['accent']));p.drawRoundedRect(QRectF(.5,1,33,23),11,11)
        p.end()


def preference(title,help_text,control,glyph=None,tint='accent'):
    row=QWidget();layout=QHBoxLayout(row);layout.setContentsMargins(0,10,0,10);layout.setSpacing(10)
    if glyph:layout.addWidget(Badge(glyph,tint))
    words=QVBoxLayout();words.setSpacing(2)
    words.addWidget(label(title));help_label=label(help_text,'secondary');words.addWidget(help_label)
    layout.addLayout(words,1);layout.addWidget(control,0,Qt.AlignmentFlag.AlignVCenter)
    return row,help_label


class Destination(QToolButton):
    """An accessible button; the selected outline means last accepted request only."""
    def __init__(self,target,primary=False,compact=False):
        super().__init__();self.target,self.compact=target,compact
        self.selected=False;self.ports='설정된 입력으로 전환';self.shortcut_hint=''
        self.setObjectName('Destination');self.setProperty('primary',primary)
        self.setText('Mac으로 전환' if target=='mac' else 'Windows로 전환')
        self.setAccessibleName(self.text());self.setFixedHeight(56 if compact else 123)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def update_icon(self):self.update()

    def set_selected(self,selected):
        self.selected=selected
        self.setAccessibleDescription('마지막으로 전송된 전환 요청' if selected else self.ports)
        self.update()

    def paintEvent(self,event):
        c=colors();p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.isEnabled():p.setOpacity(.5)
        p.setBrush(color(c['hover'] if self.underMouse() else c['module']))
        p.setPen(QPen(color(c['accent'] if self.selected and not self.compact else c['line']),2 if self.selected else 1)
                 if not self.compact else Qt.PenStyle.NoPen)
        p.drawRoundedRect(QRectF(self.rect()).adjusted(1,1,-1,-1),12,12)
        size=30 if self.compact else 36;x=10 if self.compact else 16;y=(self.height()-size)/2 if self.compact else 16
        p.setPen(Qt.PenStyle.NoPen);p.setBrush(color(c['accent'] if self.selected else c['control']))
        p.drawEllipse(QRectF(x,y,size,size))
        glyph=15 if self.compact else 18
        icon('laptop' if self.target=='mac' else 'monitor','#FFFFFF' if self.selected else c['ink'],glyph).paint(
            p,int(x+(size-glyph)/2),int(y+(size-glyph)/2),glyph,glyph)
        if self.compact:
            text_x=49;p.setFont(font(13,QFont.Weight.DemiBold));p.setPen(color(c['ink']))
            p.drawText(QRectF(text_x,7,self.width()-text_x-6,21),Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter,
                       'Mac' if self.target=='mac' else 'Windows')
            p.setFont(font(11));p.setPen(color(c['secondary']))
            p.drawText(QRectF(text_x,29,self.width()-text_x-6,18),Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter,
                       '마지막 요청' if self.selected else '전환하기')
        else:
            if self.selected:icon('circle-check',c['accent'],18).paint(p,self.width()-34,17,18,18)
            else:
                p.setFont(font(11));p.setPen(color(c['secondary']))
                p.drawText(QRectF(60,20,self.width()-76,20),Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter,self.shortcut_hint)
            p.setFont(font(15,QFont.Weight.DemiBold));p.setPen(color(c['ink']))
            p.drawText(QRectF(16,66,self.width()-32,24),Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter,self.text())
            p.setFont(font(12));p.setPen(color(c['secondary']))
            p.drawText(QRectF(16,92,self.width()-32,20),Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter,
                       p.fontMetrics().elidedText(self.ports,Qt.TextElideMode.ElideRight,self.width()-32))
        if self.hasFocus() and self.window().testAttribute(Qt.WidgetAttribute.WA_KeyboardFocusChange):
            p.setBrush(Qt.BrushStyle.NoBrush);p.setPen(QPen(color(c['accent']),1,Qt.PenStyle.DotLine))
            p.drawRoundedRect(QRectF(self.rect()).adjusted(3,3,-3,-3),10,10)
        p.end()


class MonitorSummary(QFrame):
    def __init__(self,compact=False):
        super().__init__();self.compact=compact;self.setObjectName('QuickModule' if compact else 'Module')
        layout=QVBoxLayout(self);layout.setContentsMargins(12,8 if compact else 0,12,8 if compact else 0);layout.setSpacing(0)
        if compact:
            heading=label('함께 전환할 모니터','secondary');heading.setStyleSheet('font-weight:600')
            layout.addWidget(heading);layout.addSpacing(4)
        self.rows=[]
        for index in range(2):
            if index and not compact:layout.addWidget(divider())
            row=QHBoxLayout();row.setSpacing(9 if compact else 10)
            badge=Badge(roundness=12 if compact else 6);row.addWidget(badge)
            name=label('모니터');name.setWordWrap(False);row.addWidget(name,1)
            state=label('설정 필요','secondary');state.setWordWrap(False)
            row.addWidget(state)
            check=QLabel();check.setFixedSize(14,14);row.addWidget(check)
            container=QWidget();container.setLayout(row);row.setContentsMargins(0,5 if compact else 10,0,5 if compact else 10)
            layout.addWidget(container);self.rows.append((name,state,badge,check))

    def update_rows(self,rows):
        for (name,state,badge,check),row in zip(self.rows,rows):
            name.setText(row.get('label','모니터'))
            configured=bool(row.get('id')) and row.get('mac') is not None and row.get('windows') is not None
            included=row.get('enabled',True) and configured
            badge.tint='accent' if included else 'secondary';badge.update()
            check.setPixmap(icon('check',colors()['accent'],14).pixmap(14,14) if included else QPixmap())
            if not row.get('enabled',True):text='제외'
            elif not configured:text='설정 필요'
            elif self.compact:text=''
            else:text='Mac '+input_name(row,row['mac'])+' · Windows '+input_name(row,row['windows'])
            state.setText(text);state.setVisible(bool(text))


def input_name(row,code):
    mapping=({144:'HDMI 1',145:'HDMI 2',208:'DP 1',209:'DP 2',210:'USB-C'}
             if row.get('mode')=='lg-alt' else {15:'DP 1',16:'DP 2',17:'HDMI 1',18:'HDMI 2',27:'USB-C'})
    return mapping.get(code,'입력 '+str(code))


class PopupCombo(QComboBox):
    def paintEvent(self,event):
        if self.isEditable():return super().paintEvent(event)
        option=QStyleOptionComboBox();self.initStyleOption(option);text=option.currentText;option.currentText=''
        painter=QStylePainter(self);painter.drawComplexControl(QStyle.ComplexControl.CC_ComboBox,option)
        area=self.style().subControlRect(QStyle.ComplexControl.CC_ComboBox,option,QStyle.SubControl.SC_ComboBoxEditField,self)
        painter.setPen(color(colors()['secondary']));painter.drawText(area.adjusted(0,0,-3,0),
            Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter,
            painter.fontMetrics().elidedText(text,Qt.TextElideMode.ElideMiddle,max(0,area.width()-3)))


class DisplayTile(QPushButton):
    def __init__(self,index):
        super().__init__();self.index=index;self.title='Dell P2425' if index==0 else 'LG UltraGear';self.ports='설정 필요'
        self.setText(self.title);self.setAccessibleName(self.title+' 설정 선택');self.setCheckable(True)
        self.setFixedSize(174,132);self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self,event):
        c=colors();p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing)
        width,height=(124,78) if self.index==0 else (156,66)
        x=(self.width()-width)/2;y=5+(78-height)
        if self.isChecked():
            p.setPen(QPen(color(c['accent']),2.5));p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(QRectF(x-4,y-4,width+8,height+8),9,9)
        gradient=QLinearGradient(x,y,x+width,y+height)
        for position,value in ([(0,'#64D2FF'),(.55,'#007AFF'),(1,'#5E5CE6')] if self.index==0 else
                               [(0,'#FFD60A'),(.5,'#FF9F0A'),(1,'#FF375F')]):gradient.setColorAt(position,QColor(value))
        p.setBrush(gradient);p.setPen(QPen(color('#00000026'),1));p.drawRoundedRect(QRectF(x,y,width,height),6,6)
        p.setFont(font(12,QFont.Weight.DemiBold));p.setPen(color(c['ink']))
        p.drawText(QRectF(0,98,self.width(),18),Qt.AlignmentFlag.AlignCenter,self.title)
        p.setFont(font(11));p.setPen(color(c['secondary']))
        p.drawText(QRectF(0,116,self.width(),16),Qt.AlignmentFlag.AlignCenter,self.ports)
        p.end()


class DisplayPicker(QFrame):
    selected=Signal(int)
    def __init__(self):
        super().__init__();self.setObjectName('Module')
        layout=QHBoxLayout(self);layout.setContentsMargins(0,22,0,18);layout.setSpacing(40)
        layout.addStretch(1);self.buttons=[];self.group=QButtonGroup(self)
        for index in range(2):
            tile=DisplayTile(index);self.buttons.append(tile);self.group.addButton(tile,index);layout.addWidget(tile)
        layout.addStretch(1);self.buttons[0].setChecked(True)
        self.group.idToggled.connect(lambda index,checked:self.selected.emit(index) if checked else None)

    def set_index(self,index):
        if 0<=index<len(self.buttons):self.buttons[index].setChecked(True)

    def update_rows(self,rows):
        for tile,row in zip(self.buttons,rows):
            tile.title=row.get('label',tile.title);tile.setAccessibleName(tile.title+' 설정 선택')
            tile.ports=(input_name(row,row.get('mac'))+' ↔ '+input_name(row,row.get('windows'))
                        if row.get('mac') is not None and row.get('windows') is not None else '입력 설정 필요')
            tile.update()


class ProfileStack(QStackedWidget):
    def addTab(self,widget,title):
        widget.setAccessibleName(title+' 모니터 설정')
        return self.addWidget(widget)


class Shell(QWidget):
    def __init__(self,host):
        super().__init__();self.setObjectName('Shell');self.last_target=None;self.last_outcome=None
        outer=QVBoxLayout(self);outer.setContentsMargins(0,0,0,0);outer.setSpacing(0)
        self.toolbar=QFrame();self.toolbar.setObjectName('Toolbar');self.toolbar.setFixedHeight(52)
        bar=QHBoxLayout(self.toolbar);bar.setContentsMargins(16,0,16,0);bar.setSpacing(0)
        leading=QWidget();lead=QHBoxLayout(leading);lead.setContentsMargins(0,0,0,0)
        lead.addWidget(label('Monitor Switch','toolbarTitle'));lead.addStretch(1);bar.addWidget(leading,1)
        navigation=QFrame();navigation.setObjectName('Navigation');navigation.setFixedSize(240,27);nav=QHBoxLayout(navigation)
        nav.setContentsMargins(2,2,2,2);nav.setSpacing(0)
        self.navigation=QTabBar();self.navigation.setObjectName('NavTabs');self.navigation.setDrawBase(False)
        self.navigation.setExpanding(True);self.navigation.setFont(font(12))
        self.navigation.setElideMode(Qt.TextElideMode.ElideNone);self.navigation.setUsesScrollButtons(False)
        for name in ('화면 전환','모니터 설정','활동 기록'):self.navigation.addTab(name)
        nav.addWidget(self.navigation);bar.addWidget(navigation,0,Qt.AlignmentFlag.AlignVCenter)
        trailing=QWidget();tail=QHBoxLayout(trailing);tail.setContentsMargins(0,0,0,0);tail.addStretch(1)
        self.quick_button=QToolButton();self.quick_button.setProperty('role','icon')
        self.quick_button.setIcon(icon('ellipsis',colors()['secondary'],18));identify_icon(self.quick_button,'ellipsis','secondary',18)
        self.quick_button.setAccessibleName('빠른 전환 열기');self.quick_button.setToolTip('빠른 전환 열기')
        tail.addWidget(self.quick_button);bar.addWidget(trailing,1);outer.addWidget(self.toolbar)
        self.pages=QStackedWidget();self.navigation.currentChanged.connect(lambda index:self.pages.setCurrentIndex(index))
        outer.addWidget(self.pages,1)
        self.home=QWidget();self.home.setObjectName('Page');home=QVBoxLayout(self.home)
        home.setContentsMargins(28,24,28,28);home.setSpacing(24)
        words=QVBoxLayout();words.setSpacing(4);words.addWidget(label('어디로 전환할까요?','heading'))
        subtitle=label('포함된 모니터의 입력을 함께 바꿉니다.');subtitle.setStyleSheet(f"color:{colors()['secondary']}")
        words.addWidget(subtitle);home.addLayout(words)
        buttons=QHBoxLayout();buttons.setSpacing(12)
        self.mac=Destination('mac',host=='windows');self.windows=Destination('windows',host=='mac')
        buttons.addWidget(self.mac);buttons.addWidget(self.windows);home.addLayout(buttons)
        displays=QVBoxLayout();displays.setSpacing(8);displays.addWidget(label('전환할 모니터','group'))
        self.summary=MonitorSummary();displays.addWidget(self.summary);home.addLayout(displays)
        general=QVBoxLayout();general.setSpacing(8);general.addWidget(label('일반','group'))
        preferences=module();prefs=QVBoxLayout(preferences);prefs.setContentsMargins(12,0,12,0);prefs.setSpacing(0)
        self.automatic=Toggle('USB 버튼 자동 전환')
        auto,self.auto_help=preference('USB 버튼 자동 전환','HiFime 오디오가 연결된 컴퓨터를 따라갑니다.',self.automatic,'usb','#FF9500')
        prefs.addWidget(auto);prefs.addWidget(divider())
        self.login=Toggle('로그인 시 자동 실행');self.login.setEnabled(False)
        login,_=preference('로그인 시 자동 실행','창 없이 메뉴 막대 또는 트레이에서 시작합니다.',self.login,'power','#8E8E93')
        prefs.addWidget(login);general.addWidget(preferences);home.addLayout(general)
        footer=QHBoxLayout();footer.setSpacing(8)
        self.pair=QPushButton('윈도우 연결 가져오기…' if host=='mac' else '맥 연결 파일 내보내기…')
        footer.addWidget(self.pair);footer.addStretch(1)
        info=QLabel();info.setPixmap(icon('info',colors()['secondary'],13).pixmap(13,13));identify_icon(info,'info','secondary',13)
        footer.addWidget(info)
        self.feedback=label('현재 입력은 모니터에서 직접 확인하세요','secondary');self.feedback.setObjectName('Feedback')
        self.feedback.setWordWrap(False)
        self.feedback.setAlignment(Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter);self.feedback.setMaximumWidth(310)
        footer.addWidget(self.feedback);home.addLayout(footer);home.addStretch(1);self.pages.addWidget(self.home)
        self.monitor_page=QScrollArea();self.monitor_page.setWidgetResizable(True)
        content=QWidget();content.setObjectName('Page');self.monitor_page.setWidget(content)
        self.monitor_layout=QVBoxLayout(content);self.monitor_layout.setContentsMargins(28,24,28,28);self.monitor_layout.setSpacing(24)
        self.picker=DisplayPicker();self.monitor_layout.addWidget(self.picker)
        self.monitor_tabs=ProfileStack();self.monitor_tabs.setObjectName('ProfileTabs');self.monitor_layout.addWidget(self.monitor_tabs)
        self.picker.selected.connect(self.monitor_tabs.setCurrentIndex);self.monitor_tabs.currentChanged.connect(self.picker.set_index)
        self.pages.addWidget(self.monitor_page)
        self.activity_page=QWidget();self.activity_page.setObjectName('Page');activity=QVBoxLayout(self.activity_page)
        activity.setContentsMargins(28,24,28,28);activity.setSpacing(16)
        activity.addWidget(label('활동 기록','heading'))
        activity.addWidget(label('명령 전송 결과와 오류를 모니터별로 확인합니다.','secondary'))
        self.usb_status=label('HiFime USB · 자동 전환 꺼짐','secondary');activity.addWidget(self.usb_status)
        self.log=QPlainTextEdit();self.log.setReadOnly(True);self.log.setMaximumBlockCount(300);self.log.setAccessibleName('활동 기록')
        activity.addWidget(self.log,1);self.pages.addWidget(self.activity_page)

    def attach_toolbar(self,window):
        self.layout().removeWidget(self.toolbar)
        toolbar=QToolBar('Monitor Switch',window);toolbar.setObjectName('MonitorSwitchToolbar')
        toolbar.setMovable(False);toolbar.setFloatable(False);toolbar.setContextMenuPolicy(Qt.ContextMenuPolicy.PreventContextMenu)
        self.toolbar.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Fixed)
        toolbar.addWidget(self.toolbar);window.addToolBar(toolbar)
        if sys.platform=='darwin':window.setUnifiedTitleAndToolBarOnMac(True)
        self.native_toolbar=toolbar

    def set_page(self,index):self.pages.setCurrentIndex(index);self.navigation.setCurrentIndex(index)

    def update_rows(self,rows):
        self.summary.update_rows(rows);self.picker.update_rows(rows)
        for target,button in [('mac',self.mac),('windows',self.windows)]:
            captions=[('Dell' if index==0 else 'LG')+' '+input_name(row,row.get(target))
                      for index,row in enumerate(rows) if row.get('enabled',True)]
            button.ports=' · '.join(captions) or '포함된 모니터 없음';button.update()

    def record_outcome(self,target,results):
        if target not in ('mac','windows'):return
        attempted=[r for r in results if r.get('status')!='skipped']
        if not attempted:return
        self.last_outcome='sent' if all(r.get('status')=='sent' for r in attempted) else 'uncertain'
        self.last_target=target if self.last_outcome=='sent' else None
        self.mac.set_selected(self.last_target=='mac');self.windows.set_selected(self.last_target=='windows')

    def message(self,text,warning=False):
        self.feedback.setWordWrap(warning or len(text)>38)
        self.feedback.setText(text);self.feedback.setProperty('warning',warning)
        self.feedback.style().unpolish(self.feedback);self.feedback.style().polish(self.feedback)

    def update_usb(self,text,enabled):
        if not enabled:message='자동 전환 꺼짐 · 수동 전환을 사용할 수 있습니다.'
        elif any(value in text.lower() for value in ('error','failed','failure','실패','오류')):message='USB 상태 확인 필요 · 활동 기록을 확인하세요.'
        elif 'ambiguous' in text:message='같은 USB 오디오가 여러 개입니다. 자동 전환을 보류합니다.'
        elif 'absent' in text:message='HiFime 오디오가 다른 컴퓨터에 연결되어 있습니다.'
        elif 'present' in text:message='HiFime 오디오가 이 컴퓨터에 연결되어 있습니다.'
        else:message='HiFime USB 알림을 기다리는 중입니다.'
        self.auto_help.setText(message)


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
        self.setAccessibleName('Monitor Switch 빠른 전환');self.setWindowTitle('Monitor Switch · 빠른 전환');self.last_hidden=0
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

    def set_last_target(self,target):
        self.mac.set_selected(target=='mac');self.windows.set_selected(target=='windows')

    def send(self,target):self.hide();self.switch_requested.emit(target)
    def open_settings(self):self.hide();self.settings_requested.emit()

    def hideEvent(self,event):self.last_hidden=time.monotonic();super().hideEvent(event)

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
        self.adjustSize();screen=QGuiApplication.screenAt(anchor.center()) or QGuiApplication.primaryScreen();area=screen.availableGeometry()
        x=max(area.left()+4,min(anchor.center().x()-self.width()//2,area.right()-self.width()-4))
        y=anchor.bottom() if anchor.center().y()<area.center().y() else anchor.top()-self.height()
        y=max(area.top(),min(y,area.bottom()-self.height()))
        if self.mac_panel and QGuiApplication.platformName()=='cocoa':
            from .macos import activate_application
            activate_application()
        self.move(x,y);self.show();self.raise_();self.activateWindow()
        (self.windows if self.windows.property('primary') else self.mac).setFocus()
