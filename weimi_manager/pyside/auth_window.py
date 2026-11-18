from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import __version__ as APP_VERSION
from ..auth_service import AuthContext, AuthService
from .task_manager import TaskManager

AUTH_STYLESHEET = """
QWidget {
    color: #18314B;
    font-family: 'Microsoft YaHei UI', 'Microsoft YaHei', 'Segoe UI';
    font-size: 10pt;
}
QWidget#登录外壳 { background: #F2F5F9; }
QFrame#登录卡片 {
    background: #FFFFFF;
    border: 1px solid #737F8A;
    border-radius: 0px;
}
QLabel#欢迎文字 { color: #54769B; font-size: 9pt; }
QLabel#表单标题 { color: #102B49; font-size: 22pt; font-weight: 700; }
QLabel#字段标题 { color: #17324F; font-size: 10pt; font-weight: 600; }
QLabel#辅助文字 { color: #8794A3; font-size: 8pt; }
QLabel#错误提示 { color: #C23B3B; font-size: 9pt; }
QLineEdit {
    background: #F1F4F8;
    border: 1px solid #D6E0EB;
    border-radius: 0px;
    padding: 0px 12px;
    min-height: 41px;
    color: #18314B;
    selection-background-color: #2B67A5;
    font-size: 10pt;
}
QLineEdit:focus { border: 1px solid #2B67A5; background: #FFFFFF; }
QPushButton {
    background: #F2F5F9;
    border: 1px solid #E1E7EF;
    border-radius: 0px;
    color: #294C70;
    padding: 8px 12px;
    min-height: 24px;
}
QPushButton:hover { background: #E8EFF7; }
QPushButton:disabled { color: #9AA5B2; background: #EDF0F4; }
QPushButton#主按钮 {
    background: #2867A8;
    color: #FFFFFF;
    border: 1px solid #2867A8;
    font-weight: 600;
    min-height: 28px;
}
QPushButton#主按钮:hover { background: #1F568F; }
QPushButton#链接按钮 {
    background: transparent;
    border: none;
    color: #2867A8;
    padding: 2px 0px;
    min-height: 20px;
}
QPushButton#链接按钮:hover { color: #174A80; text-decoration: underline; }
QPushButton#页签按钮 {
    background: #F0F4F8;
    border: none;
    color: #54718F;
    min-height: 28px;
}
QPushButton#页签按钮[active="true"] {
    background: #FFFFFF;
    color: #153756;
    font-weight: 700;
}
QPushButton#最近账户按钮 {
    background: #EFF3F8;
    border: none;
    color: #1D4166;
    min-height: 20px;
    padding: 5px 12px;
}
QCheckBox { color: #36536F; spacing: 6px; font-size: 9pt; }
QCheckBox::indicator { width: 13px; height: 13px; }
"""


class BrandPanel(QFrame):
    """左侧品牌区：深海军蓝、墨绿色几何装饰和高对比度中文信息。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("品牌介绍区")
        self.setMinimumWidth(370)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(36, 34, 36, 30)
        layout.setSpacing(0)

        brand_row = QHBoxLayout()
        brand_row.setSpacing(16)
        logo = QLabel("澜")
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setFixedSize(58, 58)
        logo.setStyleSheet(
            "background:#2867A8;color:#FFFFFF;font-size:25pt;font-weight:800;border:0px;"
        )
        brand_text = QVBoxLayout()
        brand_text.setSpacing(2)
        name = QLabel("澜琅")
        name.setStyleSheet("color:#FFFFFF;font-size:20pt;font-weight:800;")
        subtitle = QLabel("店铺管理")
        subtitle.setStyleSheet("color:#C7D5E3;font-size:9pt;letter-spacing:2px;")
        brand_text.addWidget(name)
        brand_text.addWidget(subtitle)
        brand_row.addWidget(logo)
        brand_row.addLayout(brand_text)
        brand_row.addStretch(1)
        layout.addLayout(brand_row)
        layout.addStretch(4)

        kicker = QLabel("多店经营，从容有序")
        kicker.setStyleSheet("color:#B6C9D9;font-size:9pt;")
        headline = QLabel("把复杂的店铺，\n放进清晰的日常。")
        headline.setWordWrap(True)
        headline.setStyleSheet("color:#FFFFFF;font-size:25pt;font-weight:800;line-height:1.35;")
        description = QLabel(
            "集中管理微信小店、订单与商品；\n登录状态可随迁移，换电脑也能继续工作。"
        )
        description.setWordWrap(True)
        description.setStyleSheet("color:#D2DFEA;font-size:9pt;line-height:1.7;")
        layout.addWidget(kicker)
        layout.addSpacing(16)
        layout.addWidget(headline)
        layout.addSpacing(18)
        layout.addWidget(description)
        layout.addSpacing(24)

        feature_row = QHBoxLayout()
        feature_row.setSpacing(7)
        for text in ("本地加密", "账号隔离", "一键迁移"):
            label = QLabel(text)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setMinimumWidth(72)
            label.setStyleSheet("background:#244B70;color:#F2F7FC;padding:8px 10px;font-size:8pt;")
            feature_row.addWidget(label)
        feature_row.addStretch(1)
        layout.addLayout(feature_row)
        layout.addStretch(2)

        steps = QHBoxLayout()
        steps.setSpacing(18)
        for number, title, detail in (
            ("01", "账户验证", "登录与找回密码"),
            ("02", "卡密授权", "验证使用期限"),
            ("03", "店铺工作台", "开始多店经营"),
        ):
            column = QVBoxLayout()
            column.setSpacing(5)
            number_label = QLabel(number)
            number_label.setStyleSheet("color:#AFC5D8;font-size:8pt;")
            title_label = QLabel(title)
            title_label.setStyleSheet("color:#FFFFFF;font-size:9pt;font-weight:700;")
            detail_label = QLabel(detail)
            detail_label.setStyleSheet("color:#9EB4C8;font-size:7pt;")
            column.addWidget(number_label)
            column.addWidget(title_label)
            column.addWidget(detail_label)
            steps.addLayout(column)
        steps.addStretch(1)
        layout.addLayout(steps)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 固定事件名称。
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#17324F"))
        width = self.width()
        height = self.height()

        # 右上角墨绿色几何块，保留品牌层次但不干扰文字阅读。
        top_shape = QPainterPath()
        top_shape.moveTo(width * 0.62, 0)
        top_shape.lineTo(width, 0)
        top_shape.lineTo(width, height * 0.29)
        top_shape.lineTo(width * 0.92, height * 0.28)
        top_shape.lineTo(width * 0.92, height * 0.05)
        top_shape.lineTo(width * 0.61, height * 0.05)
        top_shape.closeSubpath()
        painter.fillPath(top_shape, QColor("#285F55"))

        # 左下角斜切装饰，避免与主标题、功能徽标重叠。
        bottom_shape = QPainterPath()
        bottom_shape.moveTo(0, height * 0.58)
        bottom_shape.lineTo(width * 0.08, height * 0.59)
        bottom_shape.lineTo(width * 0.08, height * 0.91)
        bottom_shape.lineTo(width * 0.39, height * 0.91)
        bottom_shape.lineTo(width * 0.31, height)
        bottom_shape.lineTo(0, height)
        bottom_shape.closeSubpath()
        painter.fillPath(bottom_shape, QColor("#285F55"))
        painter.end()
        super().paintEvent(event)


class AuthWindow(QWidget):
    authenticated = Signal(object)
    closed = Signal()

    def __init__(
        self,
        auth_service: AuthService,
        task_manager: TaskManager,
        *,
        locked_context: AuthContext | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self.auth_service = auth_service
        self.task_manager = task_manager
        self.locked_context = locked_context
        self._context: AuthContext | None = locked_context
        self._callbacks: dict[
            str, tuple[Callable[[Any], None], Callable[[str], None], QPushButton | None]
        ] = {}
        self._restore_attempted = False
        self._authenticated_successfully = False
        self._recovery_email = ""
        self._pending_registration_email = ""
        self._resend_seconds = 0
        self._resend_timer = QTimer(self)
        self._resend_timer.setInterval(1000)
        self._resend_timer.timeout.connect(self._tick_resend)

        self.setObjectName("登录外壳")
        self.setWindowTitle(f"澜琅 · 登录 {APP_VERSION}")
        self.setMinimumSize(980, 680)
        self.resize(1080, 752)
        self.setStyleSheet(AUTH_STYLESHEET)
        self._build_ui()
        self.task_manager.task_completed.connect(self._on_task_completed)
        self.task_manager.task_failed.connect(self._on_task_failed)
        self._refresh_recent_accounts()
        if locked_context is not None:
            self._show_form("license")
            self._set_license_context(locked_context)
            self._set_error("")

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(0)
        self.brand_panel = BrandPanel(self)
        self.brand_panel.setFixedWidth(420)
        root.addWidget(self.brand_panel, 0)

        right = QWidget(self)
        right.setStyleSheet("background:#FFFFFF;")
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(36, 16, 36, 10)
        right_layout.setSpacing(14)
        right_layout.addStretch(1)

        self.card = QFrame(right)
        self.card.setObjectName("登录卡片")
        self.card.setFixedWidth(312)
        self.card.setMinimumHeight(598)
        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(34, 28, 34, 28)
        card_layout.setSpacing(0)

        self.form_stack = QStackedWidget(self.card)
        self.form_stack.addWidget(self._build_login_page())
        self.form_stack.addWidget(self._build_register_page())
        self.form_stack.addWidget(self._build_verify_page())
        self.form_stack.addWidget(self._build_recovery_page())
        self.form_stack.addWidget(self._build_license_page())
        card_layout.addWidget(self.form_stack)

        card_center = QHBoxLayout()
        card_center.addStretch(1)
        card_center.addWidget(self.card)
        card_center.addStretch(1)
        right_layout.addLayout(card_center)
        self.footer_label = QLabel("继续使用即表示同意《服务条款》与《隐私政策》")
        self.footer_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.footer_label.setStyleSheet("color:#98A3AF;font-size:8pt;")
        right_layout.addWidget(self.footer_label)
        right_layout.addStretch(1)
        root.addWidget(right, 1)

    @staticmethod
    def _label(text: str, object_name: str = "字段标题") -> QLabel:
        label = QLabel(text)
        label.setObjectName(object_name)
        return label

    @staticmethod
    def _input(placeholder: str, password: bool = False) -> QLineEdit:
        edit = QLineEdit()
        edit.setPlaceholderText(placeholder)
        if password:
            edit.setEchoMode(QLineEdit.EchoMode.Password)
        edit.setClearButtonEnabled(not password)
        return edit

    @staticmethod
    def _link(text: str, callback: Callable[[], None]) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("链接按钮")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(callback)
        return button

    def _build_login_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._label("欢迎回来", "欢迎文字"))
        layout.addSpacing(12)
        layout.addWidget(self._label("登录澜琅", "表单标题"))
        layout.addSpacing(20)

        tabs = QHBoxLayout()
        tabs.setSpacing(0)
        self.login_tab = QPushButton("登录")
        self.login_tab.setObjectName("页签按钮")
        self.login_tab.setProperty("active", True)
        self.register_tab = QPushButton("注册")
        self.register_tab.setObjectName("页签按钮")
        self.register_tab.setProperty("active", False)
        self.login_tab.clicked.connect(lambda: self._show_form("login"))
        self.register_tab.clicked.connect(lambda: self._show_form("register"))
        tabs.addWidget(self.login_tab)
        tabs.addWidget(self.register_tab)
        layout.addLayout(tabs)
        layout.addSpacing(20)

        layout.addWidget(self._label("账户名"))
        layout.addSpacing(7)
        self.username_input = self._input("请输入账户名")
        layout.addWidget(self.username_input)
        layout.addSpacing(13)
        layout.addWidget(self._label("密码"))
        layout.addSpacing(7)
        self.password_input = self._input("请输入密码", password=True)
        self.password_input.returnPressed.connect(self._login)
        layout.addWidget(self.password_input)
        layout.addSpacing(12)

        options = QHBoxLayout()
        self.remember_checkbox = QCheckBox("记住登录状态")
        self.remember_checkbox.setChecked(True)
        options.addWidget(self.remember_checkbox)
        options.addStretch(1)
        options.addWidget(self._link("忘记密码？", lambda: self._show_form("recovery")))
        layout.addLayout(options)
        layout.addSpacing(8)
        hint = QLabel("仅保存 Windows 加密登录令牌，不保存明文密码。")
        hint.setObjectName("辅助文字")
        layout.addWidget(hint)
        layout.addSpacing(13)

        self.login_error = QLabel("")
        self.login_error.setObjectName("错误提示")
        self.login_error.setWordWrap(True)
        layout.addWidget(self.login_error)
        self.login_button = QPushButton("登录")
        self.login_button.setObjectName("主按钮")
        self.login_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.login_button.clicked.connect(self._login)
        layout.addWidget(self.login_button)
        layout.addSpacing(15)
        layout.addWidget(QLabel("最近账户"))
        layout.addSpacing(6)
        self.recent_accounts_layout = QHBoxLayout()
        self.recent_accounts_layout.setSpacing(6)
        self.recent_accounts_layout.addStretch(1)
        layout.addLayout(self.recent_accounts_layout)
        layout.addStretch(1)
        return page

    def _build_register_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._label("创建账户", "欢迎文字"))
        layout.addSpacing(10)
        layout.addWidget(self._label("注册澜琅", "表单标题"))
        layout.addSpacing(20)
        self.register_email = self._input("请输入常用邮箱")
        self.register_username = self._input("设置登录账户名")
        self.register_password = self._input("至少 8 位，建议包含字母和数字", True)
        self.register_confirm = self._input("请再次输入密码", True)
        for label, edit in (
            ("邮箱地址", self.register_email),
            ("账户名", self.register_username),
            ("设置密码", self.register_password),
            ("确认密码", self.register_confirm),
        ):
            layout.addWidget(self._label(label))
            layout.addSpacing(5)
            layout.addWidget(edit)
            layout.addSpacing(10)
        self.register_error = QLabel("")
        self.register_error.setObjectName("错误提示")
        self.register_error.setWordWrap(True)
        layout.addWidget(self.register_error)
        self.register_button = QPushButton("发送邮箱验证码")
        self.register_button.setObjectName("主按钮")
        self.register_button.clicked.connect(self._register)
        layout.addWidget(self.register_button)
        layout.addSpacing(10)
        layout.addWidget(
            self._link("已有账户？返回登录", lambda: self._show_form("login")),
            alignment=Qt.AlignmentFlag.AlignCenter,
        )
        layout.addStretch(1)
        return page

    def _build_verify_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._label("邮箱验证", "欢迎文字"))
        layout.addSpacing(12)
        layout.addWidget(self._label("验证账户", "表单标题"))
        layout.addSpacing(12)
        self.verify_email_label = QLabel("")
        self.verify_email_label.setWordWrap(True)
        self.verify_email_label.setStyleSheet("color:#5B7188;font-size:9pt;")
        layout.addWidget(self.verify_email_label)
        layout.addSpacing(18)
        layout.addWidget(self._label("邮箱验证码"))
        layout.addSpacing(6)
        self.verify_code = self._input("请输入收到的验证码")
        layout.addWidget(self.verify_code)
        layout.addSpacing(12)
        self.verify_error = QLabel("")
        self.verify_error.setObjectName("错误提示")
        self.verify_error.setWordWrap(True)
        layout.addWidget(self.verify_error)
        self.verify_button = QPushButton("验证并进入澜琅")
        self.verify_button.setObjectName("主按钮")
        self.verify_button.clicked.connect(self._verify_registration)
        layout.addWidget(self.verify_button)
        layout.addSpacing(12)
        self.resend_button = self._link("重新发送验证码", self._resend_registration_code)
        layout.addWidget(self.resend_button, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addStretch(1)
        layout.addWidget(
            self._link("返回登录", lambda: self._show_form("login")),
            alignment=Qt.AlignmentFlag.AlignCenter,
        )
        return page

    def _build_recovery_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._label("账户安全", "欢迎文字"))
        layout.addSpacing(12)
        layout.addWidget(self._label("找回密码", "表单标题"))
        layout.addSpacing(14)
        layout.addWidget(self._label("账户名或绑定邮箱"))
        layout.addSpacing(6)
        self.recovery_identifier = self._input("请输入账户名或邮箱")
        layout.addWidget(self.recovery_identifier)
        layout.addSpacing(10)
        code_row = QHBoxLayout()
        self.recovery_code = self._input("邮箱验证码")
        self.recovery_send_button = QPushButton("获取验证码")
        self.recovery_send_button.clicked.connect(self._send_recovery_code)
        code_row.addWidget(self.recovery_code, 1)
        code_row.addWidget(self.recovery_send_button)
        layout.addLayout(code_row)
        layout.addSpacing(10)
        layout.addWidget(self._label("新密码"))
        layout.addSpacing(6)
        self.recovery_password = self._input("至少 8 位", True)
        layout.addWidget(self.recovery_password)
        layout.addSpacing(10)
        layout.addWidget(self._label("确认新密码"))
        layout.addSpacing(6)
        self.recovery_confirm = self._input("请再次输入新密码", True)
        layout.addWidget(self.recovery_confirm)
        layout.addSpacing(10)
        self.recovery_error = QLabel("")
        self.recovery_error.setObjectName("错误提示")
        self.recovery_error.setWordWrap(True)
        layout.addWidget(self.recovery_error)
        self.recovery_button = QPushButton("重置密码")
        self.recovery_button.setObjectName("主按钮")
        self.recovery_button.clicked.connect(self._reset_password)
        layout.addWidget(self.recovery_button)
        layout.addStretch(1)
        layout.addWidget(
            self._link("返回登录", lambda: self._show_form("login")),
            alignment=Qt.AlignmentFlag.AlignCenter,
        )
        return page

    def _build_license_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._label("账户授权", "欢迎文字"))
        layout.addSpacing(12)
        self.license_title = self._label("激活澜琅", "表单标题")
        layout.addWidget(self.license_title)
        layout.addSpacing(14)
        self.license_description = QLabel(
            "当前账户尚未获得有效的软件使用授权。\n请输入管理员发放的授权码。"
        )
        self.license_description.setWordWrap(True)
        self.license_description.setStyleSheet("color:#63778C;font-size:9pt;line-height:1.6;")
        layout.addWidget(self.license_description)
        layout.addSpacing(24)
        layout.addWidget(self._label("授权码"))
        layout.addSpacing(7)
        self.license_code = self._input("请输入授权码")
        layout.addWidget(self.license_code)
        layout.addSpacing(14)
        self.license_error = QLabel("")
        self.license_error.setObjectName("错误提示")
        self.license_error.setWordWrap(True)
        layout.addWidget(self.license_error)
        self.license_button = QPushButton("验证并继续使用")
        self.license_button.setObjectName("主按钮")
        self.license_button.clicked.connect(self._redeem_license)
        layout.addWidget(self.license_button)
        layout.addSpacing(12)
        self.license_account_label = QLabel("")
        self.license_account_label.setStyleSheet("color:#8493A3;font-size:8pt;")
        layout.addWidget(self.license_account_label)
        layout.addStretch(1)
        if self.locked_context is None:
            layout.addWidget(
                self._link("返回登录", lambda: self._show_form("login")),
                alignment=Qt.AlignmentFlag.AlignCenter,
            )
        return page

    def _show_form(self, name: str) -> None:
        indexes = {"login": 0, "register": 1, "verify": 2, "recovery": 3, "license": 4}
        self.form_stack.setCurrentIndex(indexes[name])
        self._set_error("")
        if hasattr(self, "login_tab"):
            self.login_tab.setProperty("active", name == "login")
            self.register_tab.setProperty("active", name == "register")
            self.login_tab.style().unpolish(self.login_tab)
            self.login_tab.style().polish(self.login_tab)
            self.register_tab.style().unpolish(self.register_tab)
            self.register_tab.style().polish(self.register_tab)

    def _set_error(self, message: str) -> None:
        for name in (
            "login_error",
            "register_error",
            "verify_error",
            "recovery_error",
            "license_error",
        ):
            label = getattr(self, name, None)
            if label is not None:
                label.setText(message if name == self._active_error_label() else "")

    def _active_error_label(self) -> str:
        index = self.form_stack.currentIndex() if hasattr(self, "form_stack") else 0
        return {
            0: "login_error",
            1: "register_error",
            2: "verify_error",
            3: "recovery_error",
            4: "license_error",
        }.get(index, "login_error")

    def _show_error(self, message: str) -> None:
        label = getattr(self, self._active_error_label(), None)
        if label is not None:
            label.setText(message)

    def _set_busy(
        self, busy: bool, button: QPushButton | None = None, text: str = "请稍候…"
    ) -> None:
        if button is None:
            button = {
                0: getattr(self, "login_button", None),
                1: getattr(self, "register_button", None),
                2: getattr(self, "verify_button", None),
                3: getattr(self, "recovery_button", None),
                4: getattr(self, "license_button", None),
            }.get(self.form_stack.currentIndex())
        if button is not None:
            if busy:
                button.setProperty("原始文字", button.text())
                button.setText(text)
                button.setEnabled(False)
            else:
                original = button.property("原始文字")
                if original:
                    button.setText(str(original))
                button.setEnabled(True)

    def _submit_async(
        self,
        key: str,
        function: Callable[..., Any],
        on_success: Callable[[Any], None],
        on_error: Callable[[str], None],
        button: QPushButton | None = None,
    ) -> None:
        self._set_busy(True, button)
        try:
            task_id = self.task_manager.submit(function, key=key)
        except Exception as exc:
            self._set_busy(False, button)
            on_error(str(exc))
            return
        self._callbacks[task_id] = (on_success, on_error, button)

    def _on_task_completed(self, task_id: str, result: object) -> None:
        callback = self._callbacks.pop(task_id, None)
        if callback is None:
            return
        success, _error, button = callback
        self._set_busy(False, button)
        success(result)

    def _on_task_failed(self, task_id: str, message: str) -> None:
        callback = self._callbacks.pop(task_id, None)
        if callback is None:
            return
        _success, error, button = callback
        self._set_busy(False, button)
        error(message)

    def _login(self) -> None:
        username = self.username_input.text().strip()
        password = self.password_input.text()
        remember = self.remember_checkbox.isChecked()
        self.login_error.clear()
        self._submit_async(
            "auth:login",
            lambda: self.auth_service.login(username, password, remember),
            self._handle_auth_context,
            self._show_error,
            self.login_button,
        )

    def _register(self) -> None:
        email = self.register_email.text().strip()
        username = self.register_username.text().strip()
        password = self.register_password.text()
        confirm = self.register_confirm.text()
        if password != confirm:
            self._show_error("两次输入的密码不一致")
            return
        self.register_error.clear()
        self._submit_async(
            "auth:register",
            lambda: self.auth_service.begin_registration(email, username, password),
            lambda _result: self._registration_started(email),
            self._show_error,
            self.register_button,
        )

    def _registration_started(self, email: str) -> None:
        self._pending_registration_email = email
        self.verify_email_label.setText(
            f"验证码已发送至：{email}\n请在邮箱中查收，并在下方输入验证码。"
        )
        self._show_form("verify")
        self._start_resend_cooldown()

    def _verify_registration(self) -> None:
        email = self._pending_registration_email
        code = self.verify_code.text().strip()
        self._submit_async(
            "auth:verify_registration",
            lambda: self.auth_service.complete_registration(email, code, True),
            self._handle_auth_context,
            self._show_error,
            self.verify_button,
        )

    def _resend_registration_code(self) -> None:
        email = self._pending_registration_email
        if not email:
            self._show_error("注册邮箱信息已失效，请重新注册")
            return
        self._submit_async(
            "auth:resend_signup",
            lambda: self.auth_service.resend_registration_code(email),
            lambda _result: self._start_resend_cooldown(),
            self._show_error,
            self.resend_button,
        )

    def _send_recovery_code(self) -> None:
        identifier = self.recovery_identifier.text().strip()
        self._submit_async(
            "auth:send_recovery",
            lambda: self.auth_service.send_recovery_code(identifier),
            self._recovery_code_sent,
            self._show_error,
            self.recovery_send_button,
        )

    def _recovery_code_sent(self, email: str) -> None:
        self._recovery_email = str(email)
        self._show_error(f"验证码已发送至 {email}")
        self._start_resend_cooldown()

    def _reset_password(self) -> None:
        if self.recovery_password.text() != self.recovery_confirm.text():
            self._show_error("两次输入的新密码不一致")
            return
        email = self._recovery_email or self.recovery_identifier.text().strip()
        self._submit_async(
            "auth:reset_password",
            lambda: self.auth_service.reset_password(
                email, self.recovery_code.text().strip(), self.recovery_password.text()
            ),
            lambda _result: self._password_reset_done(),
            self._show_error,
            self.recovery_button,
        )

    def _password_reset_done(self) -> None:
        self._show_form("login")
        self.login_password_clear()
        self._show_error("密码已重置，请使用新密码登录")

    def login_password_clear(self) -> None:
        self.password_input.clear()

    def _redeem_license(self) -> None:
        if self._context is None:
            self._show_error("当前账户会话无效，请重新登录")
            return
        code = self.license_code.text().strip()
        self._submit_async(
            "auth:redeem_license",
            lambda: self.auth_service.redeem(self._context, code),
            self._handle_auth_context,
            self._show_error,
            self.license_button,
        )

    def _handle_auth_context(self, context: AuthContext) -> None:
        self._context = context
        if context.license.active:
            self._authenticated_successfully = True
            self.authenticated.emit(context)
            self.close()
            return
        self._show_form("license")
        self._set_license_context(context)
        self.license_error.setText(context.license.status_text or "当前账户尚未获得有效授权")

    def _set_license_context(self, context: AuthContext) -> None:
        self._context = context
        if context.license.expires_at:
            detail = (
                f"账户：{context.username}\n"
                f"授权状态：{context.license.status_text}\n"
                f"到期时间：{context.license.expires_at}"
            )
        else:
            detail = (
                f"账户：{context.username}\n授权状态：{context.license.status_text or '未激活'}"
            )
        self.license_account_label.setText(detail)
        if context.license.active:
            self.license_title.setText("授权已恢复")
            self.license_description.setText("授权验证成功，点击下方按钮即可继续使用。")
            self.license_button.setText("进入澜琅工作台")
            try:
                self.license_button.clicked.disconnect()
            except RuntimeError:
                pass
            self.license_button.clicked.connect(lambda: self._finish_locked_auth(context))
        else:
            self.license_title.setText("授权已到期" if context.license.expires_at else "激活澜琅")
            self.license_button.setText("验证并继续使用")
            try:
                self.license_button.clicked.disconnect()
            except RuntimeError:
                pass
            self.license_button.clicked.connect(self._redeem_license)

    def _finish_locked_auth(self, context: AuthContext) -> None:
        self._authenticated_successfully = True
        self.authenticated.emit(context)
        self.close()

    def _refresh_recent_accounts(self) -> None:
        layout = self.recent_accounts_layout
        while layout.count() > 1:
            item = layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        accounts = self.auth_service.store.list_accounts()[:4]
        for account in accounts:
            button = QPushButton(account.username)
            button.setObjectName("最近账户按钮")
            button.clicked.connect(
                lambda _checked=False, name=account.username: self.username_input.setText(name)
            )
            layout.insertWidget(layout.count() - 1, button)
        self.recent_accounts_layout.update()

    def _start_resend_cooldown(self) -> None:
        self._resend_seconds = 60
        self._resend_timer.start()
        self._tick_resend()

    def _tick_resend(self) -> None:
        if self._resend_seconds <= 0:
            self._resend_timer.stop()
            if hasattr(self, "resend_button"):
                self.resend_button.setText("重新发送验证码")
                self.resend_button.setEnabled(True)
            if hasattr(self, "recovery_send_button"):
                self.recovery_send_button.setText("获取验证码")
                self.recovery_send_button.setEnabled(True)
            return
        if hasattr(self, "resend_button"):
            self.resend_button.setText(f"{self._resend_seconds} 秒后可重试")
            self.resend_button.setEnabled(False)
        if hasattr(self, "recovery_send_button"):
            self.recovery_send_button.setText(f"{self._resend_seconds} 秒")
            self.recovery_send_button.setEnabled(False)
        self._resend_seconds -= 1

    def showEvent(self, event) -> None:  # noqa: N802 - Qt 固定事件名称。
        super().showEvent(event)
        if self._restore_attempted or self.locked_context is not None:
            return
        self._restore_attempted = True
        accounts = self.auth_service.store.list_accounts()
        if not accounts:
            return
        self._submit_async(
            "auth:restore",
            lambda: self.auth_service.restore_account(accounts[0]),
            self._handle_auth_context,
            lambda message: self._restore_failed(message),
            self.login_button,
        )

    def _restore_failed(self, message: str) -> None:
        self._show_form("login")
        self._show_error(message)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 固定事件名称。
        if not self._authenticated_successfully:
            self.closed.emit()
        super().closeEvent(event)
