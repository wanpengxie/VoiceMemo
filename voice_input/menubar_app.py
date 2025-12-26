"""
语音输入法 - 菜单栏应用
常驻右上角，按住 Option 键录音

架构说明：
- RecordingCoordinator: 核心状态机，管理录音生命周期
- StatusBarController: UI 控制器，处理菜单栏和状态窗口
- 键盘监听通过 pynput 实现
"""

import threading
import logging
import time
from typing import Optional

from AppKit import (
    NSApplication, NSApp, NSMenu, NSMenuItem,
    NSStatusBar, NSVariableStatusItemLength,
    NSImage, NSApplicationActivationPolicyAccessory,
    NSObject, NSRunLoop, NSDate
)
from PyObjCTools import AppHelper
import objc

from . import config
from .settings import settings
from .settings_window import show_settings_window
from .log_viewer import show_log_viewer
from .ui import type_text, set_clipboard, get_clipboard
from .log_manager import setup_logging
from .history import history_manager
from .coordinator import RecordingCoordinator, CoordinatorCallbacks
from .state_machine import State
from .system_utils import (
    check_accessibility_permission,
    request_accessibility_permission,
    ensure_single_instance
)
from .audio_device_manager import get_device_manager

# 配置日志（保存到 ~/Library/Logs/VoiceMemo/）
setup_logging(level=logging.INFO)
logger = logging.getLogger(__name__)


class StatusBarController(NSObject):
    """菜单栏控制器"""

    def init(self):
        self = objc.super(StatusBarController, self).init()
        if self is None:
            return None

        self.status_item = None
        self.status_window = None
        self.key_listener = None
        self.is_option_pressed = False
        self.saved_clipboard = ""

        # 保持对动态菜单项的引用，防止被 Python GC 回收导致崩溃
        self._dynamic_menu_items = []

        # 初始化录音协调器
        self.coordinator = RecordingCoordinator(
            callbacks=CoordinatorCallbacks(
                on_state_change=self._on_state_change,
                on_ui_update=self._on_ui_update,
                on_error=self._on_error,
                on_text_commit=self._on_text_commit,
                on_text_update=self._on_text_update,
            )
        )
        self.coordinator.set_main_thread_callback(AppHelper.callAfter)

        return self

    # ═══════════════════════════════════════════════════════════════════════════════
    # 菜单栏设置
    # ═══════════════════════════════════════════════════════════════════════════════

    def setupStatusBar(self):
        """设置菜单栏图标"""
        self.status_item = NSStatusBar.systemStatusBar().statusItemWithLength_(
            NSVariableStatusItemLength
        )

        # 设置图标（使用系统麦克风图标）
        button = self.status_item.button()
        button.setTitle_("🎤")

        # 创建菜单
        menu = NSMenu.alloc().init()
        menu.setDelegate_(self)  # 设置代理以便动态更新

        # 状态显示
        self.status_menu_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "按住 Option 键说话", None, ""
        )
        self.status_menu_item.setEnabled_(False)
        menu.addItem_(self.status_menu_item)

        menu.addItem_(NSMenuItem.separatorItem())

        # 音频设备选择（子菜单）
        device_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "音频输入设备", None, ""
        )
        self.device_submenu = NSMenu.alloc().init()
        self.device_submenu.setDelegate_(self)
        device_item.setSubmenu_(self.device_submenu)
        menu.addItem_(device_item)
        self._device_menu_item = device_item

        menu.addItem_(NSMenuItem.separatorItem())

        # 输入历史（子菜单）
        history_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "输入历史", None, ""
        )
        self.history_submenu = NSMenu.alloc().init()
        self.history_submenu.setDelegate_(self)
        history_item.setSubmenu_(self.history_submenu)
        menu.addItem_(history_item)
        self._history_menu_item = history_item

        menu.addItem_(NSMenuItem.separatorItem())

        # 设置
        settings_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "设置...", "openSettings:", ","
        )
        settings_item.setTarget_(self)
        menu.addItem_(settings_item)

        # 查看日志
        log_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "查看日志...", "openLogViewer:", "l"
        )
        log_item.setTarget_(self)
        menu.addItem_(log_item)

        menu.addItem_(NSMenuItem.separatorItem())

        # 退出
        quit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "退出", "terminate:", "q"
        )
        menu.addItem_(quit_item)

        self.status_item.setMenu_(menu)
        self._main_menu = menu

    def setupStatusWindow(self):
        """设置状态显示窗口"""
        from .main import StatusBar
        self.status_window = StatusBar()
        self.status_window._setup_window()

    def setupKeyListener(self):
        """设置键盘监听"""
        # 先检查辅助功能权限
        has_permission = check_accessibility_permission()
        logger.info(f"辅助功能权限检测: {has_permission}")
        if not has_permission:
            logger.warning("辅助功能权限未授权，尝试请求...")
            request_accessibility_permission()
            # 继续设置监听，权限授权后会自动生效

        from pynput import keyboard

        def on_press(key):
            try:
                if key == keyboard.Key.alt or key == keyboard.Key.alt_l or key == keyboard.Key.alt_r:
                    if not self.is_option_pressed:
                        self.is_option_pressed = True
                        logger.info("pynput: Option 键按下")
                        AppHelper.callAfter(self._on_option_press)
            except Exception as e:
                logger.error(f"on_press 异常: {e}")

        def on_release(key):
            try:
                if key == keyboard.Key.alt or key == keyboard.Key.alt_l or key == keyboard.Key.alt_r:
                    if self.is_option_pressed:
                        self.is_option_pressed = False
                        logger.info("pynput: Option 键松开")
                        AppHelper.callAfter(self._on_option_release)
            except Exception as e:
                logger.error(f"on_release 异常: {e}")

        self.key_listener = keyboard.Listener(
            on_press=on_press,
            on_release=on_release
        )
        self.key_listener.start()
        logger.info(f"键盘监听器已启动: {self.key_listener.is_alive()}")

    def startCoordinator(self):
        """启动录音协调器"""
        # 初始化设备管理器
        # 注意：后台轮询只是补充，主要依赖系统事件通知设备变化
        # 打开菜单时会强制刷新 PortAudio 以获取最新设备列表
        device_manager = get_device_manager()
        device_manager.refresh_devices(force_refresh=True)
        device_manager.start_polling(interval=10.0)  # 低频轮询，避免干扰录音

        self.coordinator.start()

    @objc.signature(b'v@:@')
    def openSettings_(self, sender):
        """打开设置窗口"""
        show_settings_window()

    @objc.signature(b'v@:@')
    def openLogViewer_(self, sender):
        """打开日志查看窗口"""
        show_log_viewer()

    # ═══════════════════════════════════════════════════════════════════════════════
    # 键盘事件处理
    # ═══════════════════════════════════════════════════════════════════════════════

    def _on_option_press(self):
        """Option 键按下"""
        logger.info("_on_option_press 被调用")
        # 保存剪贴板内容（用于后续恢复）
        self.saved_clipboard = get_clipboard() or ""
        self.coordinator.user_start()

    def _on_option_release(self):
        """Option 键松开"""
        logger.info("_on_option_release 被调用")
        self.coordinator.user_stop()

    # ═══════════════════════════════════════════════════════════════════════════════
    # Coordinator 回调
    # ═══════════════════════════════════════════════════════════════════════════════

    def _on_state_change(self, old_state: State, new_state: State):
        """状态变化回调"""
        logger.info(f"状态变化: {old_state.name} → {new_state.name}")

        # 更新菜单栏图标
        if new_state == State.RECORDING:
            self.status_item.button().setTitle_("🔴")
            self.status_menu_item.setTitle_("录音中...")
            if self.status_window:
                self.status_window.show("正在录音...")
        elif new_state == State.ARMING:
            self.status_item.button().setTitle_("🟡")
            self.status_menu_item.setTitle_("正在初始化...")
            if self.status_window:
                self.status_window.show("正在初始化...")
        elif new_state == State.STOPPING:
            self.status_item.button().setTitle_("🟠")
            self.status_menu_item.setTitle_("正在处理...")
        else:  # IDLE or ERROR
            self.status_item.button().setTitle_("🎤")
            self.status_menu_item.setTitle_("按住 Option 键说话")
            if self.status_window:
                self.status_window.hide()

    def _on_ui_update(self, text: Optional[str]):
        """UI 更新回调"""
        if text is None:
            if self.status_window:
                self.status_window.hide()
        else:
            if self.status_window:
                self.status_window.update(text)

    def _on_error(self, message: str):
        """错误回调"""
        logger.error(f"录音错误: {message}")
        if self.status_window:
            self.status_window.update(f"❌ {message}")
            # 2 秒后自动隐藏
            timer = threading.Timer(2.0, lambda: AppHelper.callAfter(
                lambda: self.status_window.hide() if self.status_window else None
            ))
            timer.daemon = True
            timer.start()

    def _on_text_commit(self, text: str):
        """文本提交回调"""
        if not text:
            return

        # 保存到历史记录
        history_manager.add(text)

        # 输入文本
        def do_input():
            success, error = type_text(text, restore_clipboard=False)
            if success:
                # 延迟恢复剪贴板
                if self.saved_clipboard:
                    timer = threading.Timer(0.5, lambda: set_clipboard(self.saved_clipboard))
                    timer.daemon = True
                    timer.start()
            else:
                logger.warning(f"输入失败: {error}")

        # 给系统一点时间把焦点还给原 App
        timer = threading.Timer(0.08, do_input)
        timer.daemon = True
        timer.start()

    def _on_text_update(self, text: str, is_definite: bool):
        """实时文本更新回调"""
        if self.status_window and text:
            self.status_window.update(text)

    # ═══════════════════════════════════════════════════════════════════════════════
    # 历史记录菜单
    # ═══════════════════════════════════════════════════════════════════════════════

    def menuNeedsUpdate_(self, menu):
        """NSMenuDelegate: 菜单即将显示时更新"""
        try:
            if hasattr(self, 'history_submenu') and menu == self.history_submenu:
                self._updateHistoryMenu()
            elif hasattr(self, 'device_submenu') and menu == self.device_submenu:
                self._updateDeviceMenu()
        except Exception as e:
            logger.error(f"更新菜单失败: {e}")

    def _updateHistoryMenu(self):
        """更新历史子菜单"""
        try:
            self.history_submenu.removeAllItems()
            # 清空旧的菜单项引用（历史菜单部分）
            self._dynamic_menu_items = [item for item in self._dynamic_menu_items
                                        if not getattr(item, '_is_history_item', False)]

            recent_items = history_manager.get_recent(10)

            if not recent_items:
                empty_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                    "暂无历史记录", None, ""
                )
                empty_item.setEnabled_(False)
                empty_item._is_history_item = True
                self._dynamic_menu_items.append(empty_item)
                self.history_submenu.addItem_(empty_item)
                return

            # 添加最近 10 条
            for i, item in enumerate(recent_items):
                display_text = f"{item.get_time_display()}  {item.get_display_text(25)}"
                menu_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                    display_text, "copyHistoryItem:", ""
                )
                menu_item.setTarget_(self)
                menu_item.setTag_(i)  # 用 tag 存储索引
                menu_item._is_history_item = True
                self._dynamic_menu_items.append(menu_item)
                self.history_submenu.addItem_(menu_item)

            # 如果有更多历史
            total_count = history_manager.count()
            if total_count > 10:
                self.history_submenu.addItem_(NSMenuItem.separatorItem())

                more_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                    f"查看更多... ({total_count} 条)", "showAllHistory:", ""
                )
                more_item.setTarget_(self)
                more_item._is_history_item = True
                self._dynamic_menu_items.append(more_item)
                self.history_submenu.addItem_(more_item)

        except Exception as e:
            logger.error(f"更新历史菜单失败: {e}")

    @objc.signature(b'v@:@')
    def copyHistoryItem_(self, sender):
        """复制历史记录项到剪贴板"""
        try:
            index = sender.tag()
            item = history_manager.get_by_index(index)
            if item:
                set_clipboard(item.text)
                logger.info(f"已复制历史记录: {item.get_display_text()}")
        except Exception as e:
            logger.error(f"复制历史记录失败: {e}")

    @objc.signature(b'v@:@')
    def showAllHistory_(self, sender):
        """显示所有历史记录窗口"""
        try:
            from .history_window import show_history_window
            show_history_window()
        except Exception as e:
            logger.error(f"打开历史窗口失败: {e}")

    # ═══════════════════════════════════════════════════════════════════════════════
    # 音频设备菜单
    # ═══════════════════════════════════════════════════════════════════════════════

    def _updateDeviceMenu(self):
        """更新设备子菜单"""
        try:
            self.device_submenu.removeAllItems()
            # 清空旧的菜单项引用（设备菜单部分）
            self._dynamic_menu_items = [item for item in self._dynamic_menu_items
                                        if not getattr(item, '_is_device_item', False)]

            device_manager = get_device_manager()
            # 强制刷新设备列表（菜单打开时不会录音，可以安全刷新 PortAudio）
            device_manager.refresh_devices(force_refresh=True)
            devices = device_manager.get_devices()

            if not devices:
                empty_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                    "未检测到音频输入设备", None, ""
                )
                empty_item.setEnabled_(False)
                empty_item._is_device_item = True
                self._dynamic_menu_items.append(empty_item)
                self.device_submenu.addItem_(empty_item)
                return

            # 自动选择选项
            auto_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                "自动（优先耳机/外置）", "selectDevice:", ""
            )
            auto_item.setTarget_(self)
            auto_item.setTag_(-1)  # -1 表示自动选择
            if device_manager.is_auto_select():
                auto_item.setState_(1)  # NSOnState = 1
            auto_item._is_device_item = True
            self._dynamic_menu_items.append(auto_item)
            self.device_submenu.addItem_(auto_item)

            self.device_submenu.addItem_(NSMenuItem.separatorItem())

            # 设备列表
            selected_device = device_manager.get_selected_device()
            for device in devices:
                # 显示名称和优先级标记
                display_name = device.name
                if device.priority >= 100:
                    display_name = f"🎧 {display_name}"
                elif device.is_default:
                    display_name = f"● {display_name}"

                menu_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                    display_name, "selectDevice:", ""
                )
                menu_item.setTarget_(self)
                menu_item.setTag_(device.id)

                # 标记当前选中的设备（非自动模式时）
                if not device_manager.is_auto_select() and selected_device and device.id == selected_device.id:
                    menu_item.setState_(1)  # NSOnState

                menu_item._is_device_item = True
                self._dynamic_menu_items.append(menu_item)
                self.device_submenu.addItem_(menu_item)

            # 刷新按钮
            self.device_submenu.addItem_(NSMenuItem.separatorItem())
            refresh_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                "刷新设备列表", "refreshDevices:", ""
            )
            refresh_item.setTarget_(self)
            refresh_item._is_device_item = True
            self._dynamic_menu_items.append(refresh_item)
            self.device_submenu.addItem_(refresh_item)

        except Exception as e:
            logger.error(f"更新设备菜单失败: {e}")

    @objc.signature(b'v@:@')
    def selectDevice_(self, sender):
        """选择音频设备"""
        try:
            device_id = sender.tag()
            device_manager = get_device_manager()

            if device_id == -1:
                # 自动选择
                device_manager.select_device(None)
                logger.info("已切换到自动设备选择模式")
            else:
                device_manager.select_device(device_id)
                device = device_manager.get_device_by_id(device_id)
                if device:
                    logger.info(f"已选择设备: {device.name}")
        except Exception as e:
            logger.error(f"选择设备失败: {e}")

    @objc.signature(b'v@:@')
    def refreshDevices_(self, sender):
        """手动刷新设备列表"""
        try:
            device_manager = get_device_manager()
            # 强制刷新（用户主动点击时可以安全刷新）
            devices = device_manager.refresh_devices(force_refresh=True)
            logger.info(f"已刷新设备列表，共 {len(devices)} 个设备: {[d.name for d in devices]}")
        except Exception as e:
            logger.error(f"刷新设备列表失败: {e}")

    # ═══════════════════════════════════════════════════════════════════════════════
    # 清理
    # ═══════════════════════════════════════════════════════════════════════════════

    def cleanup(self):
        """清理资源"""
        # 停止设备轮询
        try:
            device_manager = get_device_manager()
            device_manager.stop_polling()
        except Exception:
            pass

        if self.coordinator:
            self.coordinator.stop()
        if self.key_listener:
            try:
                self.key_listener.stop()
            except Exception:
                pass


class MenuBarApp:
    """菜单栏应用"""

    # 全局引用，防止被 GC 回收
    _global_controller = None
    _global_app = None

    def __init__(self):
        self.controller = None

    def run(self):
        """启动应用"""
        # 单实例检测
        if not ensure_single_instance():
            logger.warning("已有另一个实例在运行")
            print("VoiceMemo 已在运行中！")
            return

        # 创建应用
        app = NSApplication.sharedApplication()
        app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

        # 创建控制器（保持全局引用，防止被 Python GC 回收导致崩溃）
        self.controller = StatusBarController.alloc().init()
        MenuBarApp._global_controller = self.controller
        MenuBarApp._global_app = self

        self.controller.setupStatusBar()
        self.controller.setupStatusWindow()
        self.controller.setupKeyListener()
        self.controller.startCoordinator()

        print("=" * 50)
        print("语音输入法已启动！")
        print()
        print("菜单栏图标: 🎤")
        print("使用方法: 按住 Option 键说话")
        print()
        print("设置: 点击菜单栏图标 → 设置...")
        print("退出: 点击菜单栏图标 → 退出")
        print("=" * 50)

        # 首次启动时检查配置
        if not settings.is_configured():
            print("\n首次启动，请先配置 API 密钥...")
            # 延迟弹出设置窗口，等应用完全启动
            def show_settings_delayed():
                import time
                time.sleep(0.5)
                AppHelper.callAfter(show_settings_window)
            threading.Thread(target=show_settings_delayed, daemon=True).start()

        # 运行主循环
        AppHelper.runEventLoop()


def _setup_exception_handling():
    """设置全局异常处理，防止未捕获异常导致崩溃"""
    import sys
    import faulthandler

    # 启用 faulthandler，在崩溃时输出 traceback
    try:
        faulthandler.enable()
    except Exception:
        pass

    # 保存原始异常处理器
    original_excepthook = sys.excepthook

    def exception_handler(exc_type, exc_value, exc_traceback):
        """全局异常处理"""
        # 记录到日志
        logger.error(
            f"未捕获异常: {exc_type.__name__}: {exc_value}",
            exc_info=(exc_type, exc_value, exc_traceback)
        )

        # 尝试显示错误提示（但不阻止程序继续运行）
        try:
            from AppKit import NSAlert, NSWarningAlertStyle
            alert = NSAlert.alloc().init()
            alert.setMessageText_("程序错误")
            alert.setInformativeText_(f"{exc_type.__name__}: {exc_value}\n\n请查看日志获取详情")
            alert.setAlertStyle_(NSWarningAlertStyle)
            # 不使用 runModal()，避免阻塞
        except Exception:
            pass

        # 调用原始处理器
        if original_excepthook:
            original_excepthook(exc_type, exc_value, exc_traceback)

    sys.excepthook = exception_handler


def main():
    """入口"""
    # 设置全局异常处理
    _setup_exception_handling()

    try:
        app = MenuBarApp()
        app.run()
    except KeyboardInterrupt:
        logger.info("用户中断，程序退出")
    except Exception as e:
        logger.error(f"程序异常退出: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
