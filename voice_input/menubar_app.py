"""
语音输入法 - 菜单栏应用
常驻右上角，按住 Option 键录音
"""

import threading
import logging
import time
import sys
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
from .asr_client import ASRClient
from .audio_recorder import AudioRecorder
from .ui import type_text, set_clipboard, get_clipboard

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger(__name__)


class StatusBarController(NSObject):
    """菜单栏控制器"""

    def init(self):
        self = objc.super(StatusBarController, self).init()
        if self is None:
            return None

        self.status_item = None
        self.status_window = None
        self.is_recording = False
        self.is_option_pressed = False
        self._lock = threading.Lock()
        self.current_text = ""
        self.committed_text = ""
        self.saved_clipboard = ""
        self.asr_client = None
        self.recorder = None
        self.key_listener = None

        return self

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

        # 状态显示
        self.status_menu_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "按住 Option 键说话", None, ""
        )
        self.status_menu_item.setEnabled_(False)
        menu.addItem_(self.status_menu_item)

        menu.addItem_(NSMenuItem.separatorItem())

        # 设置
        settings_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "设置...", "openSettings:", ","
        )
        settings_item.setTarget_(self)
        menu.addItem_(settings_item)

        menu.addItem_(NSMenuItem.separatorItem())

        # 退出
        quit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "退出", "terminate:", "q"
        )
        menu.addItem_(quit_item)

        self.status_item.setMenu_(menu)

    def setupStatusWindow(self):
        """设置状态显示窗口"""
        from .main import StatusBar
        self.status_window = StatusBar()
        self.status_window._setup_window()

    @objc.signature(b'v@:@')
    def openSettings_(self, sender):
        """打开设置窗口"""
        show_settings_window()

    def setupKeyListener(self):
        """设置键盘监听"""
        from pynput import keyboard

        def on_press(key):
            if key == keyboard.Key.alt or key == keyboard.Key.alt_l or key == keyboard.Key.alt_r:
                if not self.is_option_pressed:
                    self.is_option_pressed = True
                    AppHelper.callAfter(self._start_recording)

        def on_release(key):
            if key == keyboard.Key.alt or key == keyboard.Key.alt_l or key == keyboard.Key.alt_r:
                if self.is_option_pressed:
                    self.is_option_pressed = False
                    AppHelper.callAfter(self._stop_recording)

        self.key_listener = keyboard.Listener(
            on_press=on_press,
            on_release=on_release
        )
        self.key_listener.start()

    def _start_recording(self):
        """开始录音"""
        with self._lock:
            if self.is_recording:
                return
            self.is_recording = True

        self.current_text = ""
        self.committed_text = ""
        self.saved_clipboard = get_clipboard() or ""

        # 更新菜单栏图标
        self.status_item.button().setTitle_("🔴")
        self.status_menu_item.setTitle_("录音中...")

        # 显示状态窗口
        if self.status_window:
            self.status_window.show("正在连接...")

        # 后台连接
        threading.Thread(target=self._connect_and_record, daemon=True).start()

    def _connect_and_record(self):
        """连接 ASR 并录音"""
        self.asr_client = ASRClient(
            on_result=self._on_asr_result,
            on_error=self._on_asr_error
        )

        success, error = self.asr_client.connect()
        if not success:
            logger.error(f"ASR 连接失败: {error}")
            AppHelper.callAfter(lambda: self._update_status("连接失败"))
            time.sleep(1)
            AppHelper.callAfter(self._reset)
            return

        if not self.is_recording:
            self.asr_client.close()
            return

        # 启动录音
        self.recorder = AudioRecorder(
            on_audio=self._on_audio_data,
            on_error=self._on_recorder_error
        )

        success, error = self.recorder.start()
        if not success:
            logger.error(f"录音失败: {error}")
            AppHelper.callAfter(lambda: self._update_status("录音失败"))
            time.sleep(1)
            AppHelper.callAfter(self._reset)
            return

        AppHelper.callAfter(lambda: self._update_status("请说话..."))

    def _stop_recording(self):
        """停止录音"""
        with self._lock:
            if not self.is_recording:
                return
            self.is_recording = False

        # 恢复菜单栏图标
        self.status_item.button().setTitle_("🎤")
        self.status_menu_item.setTitle_("按住 Option 键说话")

        # 停止录音
        if self.recorder:
            self.recorder.stop()
            self.recorder = None

        # 发送最后一包
        if self.asr_client:
            self.asr_client.send_audio(b'', is_last=True)

        # 等待最后结果
        time.sleep(0.3)

        # 关闭 ASR
        if self.asr_client:
            self.asr_client.close()
            self.asr_client = None

        # 隐藏状态窗口
        if self.status_window:
            self.status_window.hide()

        # 等待焦点回到原应用
        time.sleep(0.15)

        # 输入文本
        full_text = self.committed_text + self.current_text
        if full_text:
            self._do_input(full_text)

    def _do_input(self, text: str):
        """输入文本"""
        success, error = type_text(text, restore_clipboard=False)
        if success:
            if self.saved_clipboard:
                threading.Timer(0.5, lambda: set_clipboard(self.saved_clipboard)).start()
        else:
            logger.warning(f"输入失败: {error}")

    def _update_status(self, text: str):
        """更新状态显示"""
        if self.status_window:
            self.status_window.update(text)

    def _reset(self):
        """重置状态"""
        self.is_recording = False
        self.status_item.button().setTitle_("🎤")
        self.status_menu_item.setTitle_("按住 Option 键说话")
        if self.recorder:
            self.recorder.stop()
            self.recorder = None
        if self.asr_client:
            self.asr_client.close()
            self.asr_client = None
        if self.status_window:
            self.status_window.hide()

    def _on_audio_data(self, data: bytes):
        """音频数据回调"""
        if self.asr_client and self.is_recording:
            self.asr_client.send_audio(data)

    def _on_asr_result(self, text: str, is_definite: bool):
        """识别结果回调"""
        if is_definite:
            self.committed_text += text
            self.current_text = ""
            display_text = self.committed_text
        else:
            self.current_text = text
            display_text = self.committed_text + self.current_text

        if display_text:
            AppHelper.callAfter(lambda: self._update_status(display_text))

    def _on_asr_error(self, error: str):
        """ASR 错误回调"""
        logger.error(f"ASR 错误: {error}")

    def _on_recorder_error(self, error: str):
        """录音错误回调"""
        logger.error(f"录音错误: {error}")
        AppHelper.callAfter(self._reset)


class MenuBarApp:
    """菜单栏应用"""

    def __init__(self):
        self.controller = None

    def run(self):
        """启动应用"""
        # 创建应用
        app = NSApplication.sharedApplication()
        app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

        # 创建控制器
        self.controller = StatusBarController.alloc().init()
        self.controller.setupStatusBar()
        self.controller.setupStatusWindow()
        self.controller.setupKeyListener()

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


def main():
    """入口"""
    app = MenuBarApp()
    app.run()


if __name__ == "__main__":
    main()
