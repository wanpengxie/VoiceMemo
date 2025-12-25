"""
日志查看窗口
使用 AppKit 创建原生 macOS 日志查看界面
"""

import objc
from AppKit import (
    NSWindow, NSWindowStyleMaskTitled, NSWindowStyleMaskClosable,
    NSWindowStyleMaskResizable, NSWindowStyleMaskMiniaturizable,
    NSBackingStoreBuffered, NSTextField, NSButton, NSBezelStyleRounded,
    NSFont, NSMakeRect, NSApp, NSFloatingWindowLevel,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSScrollView, NSTextView, NSBorderlessWindowMask,
    NSColor, NSWorkspace,
)
from Foundation import NSObject, NSURL

from .log_manager import read_log_content, clear_log, get_log_file_path, get_log_dir


class LogViewerController(NSObject):
    """日志查看窗口控制器"""

    def init(self):
        self = objc.super(LogViewerController, self).init()
        if self is None:
            return None

        self.window = None
        self.text_view = None
        self.scroll_view = None

        return self

    def showWindow(self):
        """显示日志窗口"""
        if self.window is None:
            self.createWindow()

        # 加载日志内容
        self.refreshLog()

        # 显示窗口
        self.window.center()
        self.window.makeKeyAndOrderFront_(None)
        NSApp.activateIgnoringOtherApps_(True)

    def createWindow(self):
        """创建日志查看窗口"""
        # 窗口尺寸
        width = 700
        height = 500

        # 创建窗口
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, width, height),
            NSWindowStyleMaskTitled | NSWindowStyleMaskClosable |
            NSWindowStyleMaskResizable | NSWindowStyleMaskMiniaturizable,
            NSBackingStoreBuffered,
            False
        )
        self.window.setTitle_("日志查看器")
        self.window.setLevel_(NSFloatingWindowLevel)
        self.window.setCollectionBehavior_(NSWindowCollectionBehaviorCanJoinAllSpaces)
        self.window.setMinSize_((400, 300))

        content = self.window.contentView()

        # 布局参数
        margin = 10
        button_height = 32
        button_width = 100
        bottom_bar_height = button_height + margin * 2

        # 创建滚动视图
        scroll_frame = NSMakeRect(
            margin,
            bottom_bar_height,
            width - margin * 2,
            height - bottom_bar_height - margin
        )
        self.scroll_view = NSScrollView.alloc().initWithFrame_(scroll_frame)
        self.scroll_view.setHasVerticalScroller_(True)
        self.scroll_view.setHasHorizontalScroller_(True)
        self.scroll_view.setAutohidesScrollers_(True)
        self.scroll_view.setBorderType_(1)  # NSBezelBorder
        self.scroll_view.setAutoresizingMask_(18)  # 宽高自适应

        # 创建文本视图
        text_frame = NSMakeRect(0, 0, scroll_frame.size.width, scroll_frame.size.height)
        self.text_view = NSTextView.alloc().initWithFrame_(text_frame)
        self.text_view.setEditable_(False)
        self.text_view.setSelectable_(True)
        self.text_view.setFont_(NSFont.fontWithName_size_("Menlo", 11))
        self.text_view.setBackgroundColor_(NSColor.textBackgroundColor())
        self.text_view.setTextColor_(NSColor.textColor())
        self.text_view.setAutoresizingMask_(18)

        # 设置文本视图可以水平滚动
        self.text_view.setHorizontallyResizable_(True)
        self.text_view.textContainer().setWidthTracksTextView_(False)
        self.text_view.textContainer().setContainerSize_((10000, 10000))

        self.scroll_view.setDocumentView_(self.text_view)
        content.addSubview_(self.scroll_view)

        # 底部按钮栏
        # 刷新按钮
        refresh_button = NSButton.alloc().initWithFrame_(
            NSMakeRect(margin, margin, button_width, button_height)
        )
        refresh_button.setTitle_("刷新")
        refresh_button.setBezelStyle_(NSBezelStyleRounded)
        refresh_button.setTarget_(self)
        refresh_button.setAction_(objc.selector(self.refreshClicked_, signature=b'v@:@'))
        content.addSubview_(refresh_button)

        # 清空按钮
        clear_button = NSButton.alloc().initWithFrame_(
            NSMakeRect(margin + button_width + 10, margin, button_width, button_height)
        )
        clear_button.setTitle_("清空日志")
        clear_button.setBezelStyle_(NSBezelStyleRounded)
        clear_button.setTarget_(self)
        clear_button.setAction_(objc.selector(self.clearClicked_, signature=b'v@:@'))
        content.addSubview_(clear_button)

        # 打开目录按钮
        open_dir_button = NSButton.alloc().initWithFrame_(
            NSMakeRect(margin + (button_width + 10) * 2, margin, button_width + 20, button_height)
        )
        open_dir_button.setTitle_("打开日志目录")
        open_dir_button.setBezelStyle_(NSBezelStyleRounded)
        open_dir_button.setTarget_(self)
        open_dir_button.setAction_(objc.selector(self.openDirClicked_, signature=b'v@:@'))
        content.addSubview_(open_dir_button)

        # 日志路径标签
        path_label = NSTextField.alloc().initWithFrame_(
            NSMakeRect(margin + (button_width + 10) * 2 + button_width + 30, margin + 6, 300, 20)
        )
        path_label.setStringValue_(str(get_log_file_path()))
        path_label.setBezeled_(False)
        path_label.setDrawsBackground_(False)
        path_label.setEditable_(False)
        path_label.setSelectable_(True)
        path_label.setFont_(NSFont.systemFontOfSize_(10))
        path_label.setTextColor_(NSColor.secondaryLabelColor())
        content.addSubview_(path_label)

    def refreshLog(self):
        """刷新日志内容"""
        content = read_log_content(max_lines=1000)
        self.text_view.setString_(content)

        # 滚动到底部
        self.text_view.scrollRangeToVisible_((len(content), 0))

    @objc.signature(b'v@:@')
    def refreshClicked_(self, sender):
        """刷新按钮点击"""
        self.refreshLog()

    @objc.signature(b'v@:@')
    def clearClicked_(self, sender):
        """清空按钮点击"""
        clear_log()
        self.refreshLog()

    @objc.signature(b'v@:@')
    def openDirClicked_(self, sender):
        """打开日志目录"""
        log_dir = get_log_dir()
        url = NSURL.fileURLWithPath_(str(log_dir))
        NSWorkspace.sharedWorkspace().openURL_(url)


# 全局实例
_log_viewer_controller = None


def show_log_viewer():
    """显示日志查看窗口"""
    global _log_viewer_controller
    if _log_viewer_controller is None:
        _log_viewer_controller = LogViewerController.alloc().init()
    _log_viewer_controller.showWindow()
