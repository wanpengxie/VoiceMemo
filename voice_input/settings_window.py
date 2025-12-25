"""
设置窗口
使用 AppKit 创建原生 macOS 设置界面
"""

import objc
from AppKit import (
    NSWindow, NSWindowStyleMaskTitled, NSWindowStyleMaskClosable,
    NSBackingStoreBuffered, NSTextField, NSSecureTextField,
    NSButton, NSBezelStyleRounded, NSFont,
    NSMakeRect, NSApp, NSFloatingWindowLevel,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSAlert, NSWarningAlertStyle,
    NSMenu, NSMenuItem,
)
from Foundation import NSObject


def _setup_edit_menu():
    """设置编辑菜单，支持复制粘贴快捷键"""
    menubar = NSMenu.alloc().init()

    # 应用菜单
    app_menu = NSMenu.alloc().init()
    app_menu_item = NSMenuItem.alloc().init()
    app_menu_item.setSubmenu_(app_menu)
    menubar.addItem_(app_menu_item)

    # 编辑菜单
    edit_menu = NSMenu.alloc().initWithTitle_("编辑")
    edit_menu_item = NSMenuItem.alloc().init()
    edit_menu_item.setSubmenu_(edit_menu)

    # 剪切
    cut_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("剪切", "cut:", "x")
    edit_menu.addItem_(cut_item)

    # 复制
    copy_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("复制", "copy:", "c")
    edit_menu.addItem_(copy_item)

    # 粘贴
    paste_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("粘贴", "paste:", "v")
    edit_menu.addItem_(paste_item)

    # 全选
    select_all_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("全选", "selectAll:", "a")
    edit_menu.addItem_(select_all_item)

    menubar.addItem_(edit_menu_item)
    NSApp.setMainMenu_(menubar)

from .settings import settings, DEFAULT_RESOURCE_ID


def _create_label(text, x, y, width):
    """创建标签"""
    label = NSTextField.alloc().initWithFrame_(NSMakeRect(x, y, width, 20))
    label.setStringValue_(text)
    label.setBezeled_(False)
    label.setDrawsBackground_(False)
    label.setEditable_(False)
    label.setSelectable_(False)
    return label


def _create_text_field(x, y, width, height, placeholder="", secure=False):
    """创建输入框"""
    if secure:
        field = NSSecureTextField.alloc().initWithFrame_(NSMakeRect(x, y, width, height))
    else:
        field = NSTextField.alloc().initWithFrame_(NSMakeRect(x, y, width, height))

    field.setPlaceholderString_(placeholder)
    field.setBezeled_(True)
    field.setDrawsBackground_(True)
    field.setEditable_(True)
    field.setSelectable_(True)
    return field


def _show_alert(message):
    """显示提示"""
    alert = NSAlert.alloc().init()
    alert.setMessageText_("提示")
    alert.setInformativeText_(message)
    alert.setAlertStyle_(NSWarningAlertStyle)
    alert.runModal()


class SettingsWindowController(NSObject):
    """设置窗口控制器"""

    def init(self):
        self = objc.super(SettingsWindowController, self).init()
        if self is None:
            return None

        self.window = None
        self.app_key_field = None
        self.access_key_field = None
        self.resource_id_field = None
        self.on_save_callback = None

        return self

    def showWindow_(self, callback):
        """显示设置窗口"""
        self.on_save_callback = callback

        if self.window is None:
            self.createWindow()

        # 加载当前配置
        self.app_key_field.setStringValue_(settings.app_key)
        self.access_key_field.setStringValue_(settings.access_key)
        self.resource_id_field.setStringValue_(settings.resource_id or DEFAULT_RESOURCE_ID)

        # 显示窗口
        self.window.center()
        self.window.makeKeyAndOrderFront_(None)
        NSApp.activateIgnoringOtherApps_(True)

    def createWindow(self):
        """创建设置窗口"""
        # 窗口尺寸
        width = 450
        height = 280

        # 创建窗口
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, width, height),
            NSWindowStyleMaskTitled | NSWindowStyleMaskClosable,
            NSBackingStoreBuffered,
            False
        )
        self.window.setTitle_("语音输入 - 设置")
        self.window.setLevel_(NSFloatingWindowLevel)
        self.window.setCollectionBehavior_(NSWindowCollectionBehaviorCanJoinAllSpaces)

        content = self.window.contentView()

        # 布局参数
        margin = 20
        label_width = 100
        field_width = width - margin * 2 - label_width - 10
        field_height = 24
        row_height = 50
        current_y = height - 60

        # 标题
        title_label = _create_label("豆包语音识别 API 配置", margin, current_y, width - margin * 2)
        title_label.setFont_(NSFont.boldSystemFontOfSize_(14))
        content.addSubview_(title_label)

        current_y -= row_height

        # App Key
        app_key_label = _create_label("App Key:", margin, current_y, label_width)
        content.addSubview_(app_key_label)

        self.app_key_field = _create_text_field(
            margin + label_width + 10, current_y,
            field_width, field_height,
            placeholder="从火山引擎控制台获取"
        )
        content.addSubview_(self.app_key_field)

        current_y -= row_height

        # Access Key
        access_key_label = _create_label("Access Key:", margin, current_y, label_width)
        content.addSubview_(access_key_label)

        self.access_key_field = _create_text_field(
            margin + label_width + 10, current_y,
            field_width, field_height,
            placeholder="从火山引擎控制台获取",
            secure=True
        )
        content.addSubview_(self.access_key_field)

        current_y -= row_height

        # Resource ID
        resource_id_label = _create_label("Resource ID:", margin, current_y, label_width)
        content.addSubview_(resource_id_label)

        self.resource_id_field = _create_text_field(
            margin + label_width + 10, current_y,
            field_width, field_height,
            placeholder=DEFAULT_RESOURCE_ID
        )
        content.addSubview_(self.resource_id_field)

        current_y -= row_height + 10

        # 按钮
        button_width = 80
        button_height = 32

        # 取消按钮
        cancel_button = NSButton.alloc().initWithFrame_(
            NSMakeRect(width - margin - button_width * 2 - 10, margin, button_width, button_height)
        )
        cancel_button.setTitle_("取消")
        cancel_button.setBezelStyle_(NSBezelStyleRounded)
        cancel_button.setTarget_(self)
        cancel_button.setAction_(objc.selector(self.cancelClicked_, signature=b'v@:@'))
        content.addSubview_(cancel_button)

        # 保存按钮
        save_button = NSButton.alloc().initWithFrame_(
            NSMakeRect(width - margin - button_width, margin, button_width, button_height)
        )
        save_button.setTitle_("保存")
        save_button.setBezelStyle_(NSBezelStyleRounded)
        save_button.setKeyEquivalent_("\r")  # Enter 键触发
        save_button.setTarget_(self)
        save_button.setAction_(objc.selector(self.saveClicked_, signature=b'v@:@'))
        content.addSubview_(save_button)

    @objc.signature(b'v@:@')
    def cancelClicked_(self, sender):
        """取消按钮点击"""
        self.window.close()

    @objc.signature(b'v@:@')
    def saveClicked_(self, sender):
        """保存按钮点击"""
        app_key = self.app_key_field.stringValue()
        access_key = self.access_key_field.stringValue()
        resource_id = self.resource_id_field.stringValue()

        # 验证
        if not app_key:
            _show_alert("请输入 App Key")
            return
        if not access_key:
            _show_alert("请输入 Access Key")
            return

        # 保存
        settings.app_key = app_key
        settings.access_key = access_key
        settings.resource_id = resource_id or DEFAULT_RESOURCE_ID

        # 关闭窗口
        self.window.close()

        # 回调
        if self.on_save_callback:
            self.on_save_callback()


# 全局实例
_settings_window_controller = None


def show_settings_window(callback=None):
    """显示设置窗口"""
    global _settings_window_controller
    _setup_edit_menu()  # 确保编辑菜单可用
    if _settings_window_controller is None:
        _settings_window_controller = SettingsWindowController.alloc().init()
    _settings_window_controller.showWindow_(callback)
