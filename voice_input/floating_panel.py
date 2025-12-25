"""
不抢焦点的浮动面板 - 使用 NSPanel + nonactivatingPanel
"""

import logging
from typing import Optional, Callable

logger = logging.getLogger(__name__)

try:
    from AppKit import (
        NSPanel, NSWindowStyleMaskNonactivatingPanel,
        NSWindowStyleMaskTitled, NSWindowStyleMaskClosable,
        NSBackingStoreBuffered, NSFloatingWindowLevel,
        NSColor, NSTextField, NSFont,
        NSMakeRect, NSApp, NSWindowCollectionBehaviorCanJoinAllSpaces,
        NSWindowCollectionBehaviorFullScreenAuxiliary,
        NSTextAlignmentCenter
    )
    from Foundation import NSObject
    from PyObjCTools import AppHelper
    import objc
    HAS_APPKIT = True
except ImportError:
    HAS_APPKIT = False
    logger.warning("AppKit 未安装，将使用 tkinter 回退方案")


class FloatingPanel:
    """不抢焦点的浮动面板"""

    def __init__(self):
        self.panel: Optional[NSPanel] = None
        self.text_field: Optional[NSTextField] = None
        self._visible = False
        self._pending_text = ""

    def show(self, text: str = "正在录音..."):
        """显示面板（线程安全）"""
        if not HAS_APPKIT:
            logger.warning("AppKit 不可用")
            return

        self._pending_text = text
        # 在主线程执行
        AppHelper.callAfter(self._show_on_main_thread)

    def _show_on_main_thread(self):
        """主线程：显示面板"""
        if self.panel is None:
            self._create_panel()

        self.text_field.setStringValue_(self._pending_text)
        self.panel.orderFrontRegardless()
        self._visible = True

    def _create_panel(self):
        """创建 NSPanel"""
        # 窗口大小和位置
        width, height = 400, 80

        # 获取屏幕尺寸，放在顶部中央
        from AppKit import NSScreen
        screen = NSScreen.mainScreen()
        screen_rect = screen.frame()
        x = (screen_rect.size.width - width) / 2
        y = screen_rect.size.height - height - 100  # 距顶部 100px

        rect = NSMakeRect(x, y, width, height)

        # 创建不抢焦点的面板
        style = (
            NSWindowStyleMaskNonactivatingPanel |
            NSWindowStyleMaskTitled
        )

        self.panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            rect,
            style,
            NSBackingStoreBuffered,
            False
        )

        # 设置面板属性
        self.panel.setTitle_("语音输入")
        self.panel.setLevel_(NSFloatingWindowLevel)
        self.panel.setFloatingPanel_(True)
        self.panel.setBecomesKeyOnlyIfNeeded_(True)
        self.panel.setHidesOnDeactivate_(False)

        # 允许在全屏和所有空间显示
        self.panel.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces |
            NSWindowCollectionBehaviorFullScreenAuxiliary
        )

        # 设置背景颜色
        self.panel.setBackgroundColor_(NSColor.colorWithRed_green_blue_alpha_(
            0.2, 0.2, 0.2, 0.95
        ))

        # 创建文本标签
        text_rect = NSMakeRect(10, 20, width - 20, 40)
        self.text_field = NSTextField.alloc().initWithFrame_(text_rect)
        self.text_field.setStringValue_("正在录音...")
        self.text_field.setEditable_(False)
        self.text_field.setBezeled_(False)
        self.text_field.setDrawsBackground_(False)
        self.text_field.setTextColor_(NSColor.whiteColor())
        self.text_field.setFont_(NSFont.systemFontOfSize_(18))
        self.text_field.setAlignment_(NSTextAlignmentCenter)

        self.panel.contentView().addSubview_(self.text_field)

    def update(self, text: str):
        """更新文本（线程安全）"""
        self._pending_text = text
        if HAS_APPKIT:
            AppHelper.callAfter(self._update_on_main_thread)

    def _update_on_main_thread(self):
        """主线程：更新文本"""
        if self.text_field:
            self.text_field.setStringValue_(self._pending_text)

    def hide(self):
        """隐藏面板（线程安全）"""
        if HAS_APPKIT:
            AppHelper.callAfter(self._hide_on_main_thread)

    def _hide_on_main_thread(self):
        """主线程：隐藏面板"""
        if self.panel:
            self.panel.orderOut_(None)
            self._visible = False

    @property
    def visible(self) -> bool:
        return self._visible


# 如果 AppKit 不可用，提供 tkinter 回退
class TkFloatingPanel:
    """tkinter 回退方案（会抢焦点）"""

    def __init__(self, root):
        self.root = root
        self.window = None
        self.label = None
        self._visible = False

    def show(self, text: str = "正在录音..."):
        if self.window is None:
            self._create_window()
        self.label.config(text=text)
        self.window.deiconify()
        self._visible = True

    def _create_window(self):
        import tkinter as tk
        self.window = tk.Toplevel(self.root)
        self.window.title("语音输入")
        self.window.attributes('-topmost', True)
        self.window.overrideredirect(True)

        self.label = tk.Label(
            self.window,
            text="正在录音...",
            bg='#333333',
            fg='white',
            font=('Arial', 16),
            padx=30,
            pady=20
        )
        self.label.pack()

        # 居中显示
        self.window.update_idletasks()
        screen_width = self.window.winfo_screenwidth()
        win_width = self.window.winfo_width()
        x = (screen_width - win_width) // 2
        self.window.geometry(f"+{x}+100")

    def update(self, text: str):
        if self.label:
            self.label.config(text=text)

    def hide(self):
        if self.window:
            self.window.withdraw()
            self._visible = False

    @property
    def visible(self) -> bool:
        return self._visible


def create_floating_panel(root=None):
    """创建浮动面板（自动选择最佳实现）"""
    if HAS_APPKIT:
        return FloatingPanel()
    else:
        if root is None:
            raise ValueError("tkinter 回退方案需要 root 参数")
        return TkFloatingPanel(root)
