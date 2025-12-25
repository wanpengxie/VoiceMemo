"""
历史记录查看窗口
使用 AppKit 创建原生 macOS 历史记录查看界面
"""

import objc
from AppKit import (
    NSWindow, NSWindowStyleMaskTitled, NSWindowStyleMaskClosable,
    NSWindowStyleMaskResizable, NSWindowStyleMaskMiniaturizable,
    NSBackingStoreBuffered, NSTextField, NSButton, NSBezelStyleRounded,
    NSFont, NSMakeRect, NSApp, NSFloatingWindowLevel,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSScrollView, NSTableView, NSTableColumn,
    NSColor, NSObject, NSTableViewSelectionHighlightStyleRegular,
    NSLineBreakByTruncatingTail,
)
from Foundation import NSObject

from .history import history_manager
from .ui import set_clipboard


class HistoryTableDataSource(NSObject):
    """历史记录表格数据源"""

    def init(self):
        self = objc.super(HistoryTableDataSource, self).init()
        if self is None:
            return None
        self._items = []
        return self

    def reload(self):
        """重新加载数据"""
        self._items = history_manager.get_all()

    def numberOfRowsInTableView_(self, tableView):
        """返回行数"""
        return len(self._items)

    def tableView_objectValueForTableColumn_row_(self, tableView, column, row):
        """返回单元格内容"""
        if row >= len(self._items):
            return ""

        item = self._items[row]
        col_id = column.identifier()

        if col_id == "time":
            return item.get_time_display()
        elif col_id == "text":
            return item.text.replace("\n", " ").strip()

        return ""

    def get_item_at_row(self, row):
        """获取指定行的记录"""
        if 0 <= row < len(self._items):
            return self._items[row]
        return None


class HistoryWindowController(NSObject):
    """历史记录窗口控制器"""

    def init(self):
        self = objc.super(HistoryWindowController, self).init()
        if self is None:
            return None

        self.window = None
        self.table_view = None
        self.scroll_view = None
        self.data_source = None
        self.status_label = None

        return self

    def showWindow(self):
        """显示窗口"""
        if self.window is None:
            self.createWindow()

        # 刷新数据
        self.refreshData()

        # 显示窗口
        self.window.center()
        self.window.makeKeyAndOrderFront_(None)
        NSApp.activateIgnoringOtherApps_(True)

    def createWindow(self):
        """创建窗口"""
        # 窗口尺寸
        width = 600
        height = 450

        # 创建窗口
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, width, height),
            NSWindowStyleMaskTitled | NSWindowStyleMaskClosable |
            NSWindowStyleMaskResizable | NSWindowStyleMaskMiniaturizable,
            NSBackingStoreBuffered,
            False
        )
        self.window.setTitle_("输入历史")
        self.window.setLevel_(NSFloatingWindowLevel)
        self.window.setCollectionBehavior_(NSWindowCollectionBehaviorCanJoinAllSpaces)
        self.window.setMinSize_((400, 300))

        content = self.window.contentView()

        # 布局参数
        margin = 10
        button_height = 32
        button_width = 100
        bottom_bar_height = button_height + margin * 2

        # 创建数据源
        self.data_source = HistoryTableDataSource.alloc().init()

        # 创建滚动视图
        scroll_frame = NSMakeRect(
            margin,
            bottom_bar_height,
            width - margin * 2,
            height - bottom_bar_height - margin
        )
        self.scroll_view = NSScrollView.alloc().initWithFrame_(scroll_frame)
        self.scroll_view.setHasVerticalScroller_(True)
        self.scroll_view.setHasHorizontalScroller_(False)
        self.scroll_view.setAutohidesScrollers_(True)
        self.scroll_view.setBorderType_(1)  # NSBezelBorder
        self.scroll_view.setAutoresizingMask_(18)  # 宽高自适应

        # 创建表格视图
        table_frame = NSMakeRect(0, 0, scroll_frame.size.width, scroll_frame.size.height)
        self.table_view = NSTableView.alloc().initWithFrame_(table_frame)
        self.table_view.setDataSource_(self.data_source)
        self.table_view.setDelegate_(self)
        self.table_view.setSelectionHighlightStyle_(NSTableViewSelectionHighlightStyleRegular)
        self.table_view.setRowHeight_(24)
        self.table_view.setDoubleAction_(objc.selector(self.tableDoubleClicked_, signature=b'v@:@'))
        self.table_view.setTarget_(self)

        # 时间列
        time_column = NSTableColumn.alloc().initWithIdentifier_("time")
        time_column.setTitle_("时间")
        time_column.setWidth_(100)
        time_column.setMinWidth_(80)
        time_column.setMaxWidth_(150)
        self.table_view.addTableColumn_(time_column)

        # 内容列
        text_column = NSTableColumn.alloc().initWithIdentifier_("text")
        text_column.setTitle_("内容 (双击复制)")
        text_column.setWidth_(scroll_frame.size.width - 120)
        text_column.setMinWidth_(200)
        self.table_view.addTableColumn_(text_column)

        self.scroll_view.setDocumentView_(self.table_view)
        content.addSubview_(self.scroll_view)

        # 底部按钮栏
        # 复制按钮
        copy_button = NSButton.alloc().initWithFrame_(
            NSMakeRect(margin, margin, button_width, button_height)
        )
        copy_button.setTitle_("复制选中")
        copy_button.setBezelStyle_(NSBezelStyleRounded)
        copy_button.setTarget_(self)
        copy_button.setAction_(objc.selector(self.copyClicked_, signature=b'v@:@'))
        content.addSubview_(copy_button)

        # 刷新按钮
        refresh_button = NSButton.alloc().initWithFrame_(
            NSMakeRect(margin + button_width + 10, margin, button_width, button_height)
        )
        refresh_button.setTitle_("刷新")
        refresh_button.setBezelStyle_(NSBezelStyleRounded)
        refresh_button.setTarget_(self)
        refresh_button.setAction_(objc.selector(self.refreshClicked_, signature=b'v@:@'))
        content.addSubview_(refresh_button)

        # 清空按钮
        clear_button = NSButton.alloc().initWithFrame_(
            NSMakeRect(margin + (button_width + 10) * 2, margin, button_width, button_height)
        )
        clear_button.setTitle_("清空历史")
        clear_button.setBezelStyle_(NSBezelStyleRounded)
        clear_button.setTarget_(self)
        clear_button.setAction_(objc.selector(self.clearClicked_, signature=b'v@:@'))
        content.addSubview_(clear_button)

        # 状态标签
        self.status_label = NSTextField.alloc().initWithFrame_(
            NSMakeRect(margin + (button_width + 10) * 3 + 10, margin + 6, 200, 20)
        )
        self.status_label.setStringValue_("")
        self.status_label.setBezeled_(False)
        self.status_label.setDrawsBackground_(False)
        self.status_label.setEditable_(False)
        self.status_label.setSelectable_(False)
        self.status_label.setFont_(NSFont.systemFontOfSize_(11))
        self.status_label.setTextColor_(NSColor.secondaryLabelColor())
        content.addSubview_(self.status_label)

    def refreshData(self):
        """刷新数据"""
        self.data_source.reload()
        self.table_view.reloadData()
        count = history_manager.count()
        self.status_label.setStringValue_(f"共 {count} 条记录")

    @objc.signature(b'v@:@')
    def tableDoubleClicked_(self, sender):
        """双击表格行"""
        row = self.table_view.clickedRow()
        if row >= 0:
            item = self.data_source.get_item_at_row(row)
            if item:
                set_clipboard(item.text)
                self.status_label.setStringValue_("已复制到剪贴板")

    @objc.signature(b'v@:@')
    def copyClicked_(self, sender):
        """复制按钮点击"""
        row = self.table_view.selectedRow()
        if row >= 0:
            item = self.data_source.get_item_at_row(row)
            if item:
                set_clipboard(item.text)
                self.status_label.setStringValue_("已复制到剪贴板")
        else:
            self.status_label.setStringValue_("请先选择一条记录")

    @objc.signature(b'v@:@')
    def refreshClicked_(self, sender):
        """刷新按钮点击"""
        self.refreshData()

    @objc.signature(b'v@:@')
    def clearClicked_(self, sender):
        """清空按钮点击"""
        history_manager.clear()
        self.refreshData()
        self.status_label.setStringValue_("历史已清空")


# 全局实例
_history_window_controller = None


def show_history_window():
    """显示历史记录窗口"""
    global _history_window_controller
    if _history_window_controller is None:
        _history_window_controller = HistoryWindowController.alloc().init()
    _history_window_controller.showWindow()
