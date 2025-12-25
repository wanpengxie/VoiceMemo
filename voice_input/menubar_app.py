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
from .log_viewer import show_log_viewer
from .asr_client import ASRClient
from .audio_recorder import AudioRecorder
from .ui import type_text, set_clipboard, get_clipboard
from .log_manager import setup_logging

# 配置日志（保存到 ~/Library/Logs/VoiceInput/）
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
        self.is_recording = False
        self.is_option_pressed = False
        self._lock = threading.Lock()
        self.current_text = ""
        self.committed_text = ""
        self.saved_clipboard = ""
        self.asr_client = None
        self.recorder = None
        self.key_listener = None

        # 计费/资源保护：避免长时间占用麦克风/ASR
        self._idle_timer: Optional[threading.Timer] = None
        self._recording_timeout_timer: Optional[threading.Timer] = None
        self._last_interaction_ts = time.time()

        # 会话/收尾控制（防止快速连按导致竞态）
        self._session_id = 0
        self._finalize_event: Optional[threading.Event] = None
        self._finalize_wait_session_id: Optional[int] = None
        self._stopping = False

        return self

    def _touch_activity(self):
        """记录一次交互，并重置 idle 关停定时器"""
        self._last_interaction_ts = time.time()
        self._reset_idle_timer()

    def _reset_idle_timer(self):
        """60 秒无交互：确保彻底关麦/断开连接"""
        try:
            if self._idle_timer:
                self._idle_timer.cancel()
        except Exception:
            pass

        def on_idle():
            AppHelper.callAfter(self._idle_shutdown_if_needed)

        self._idle_timer = threading.Timer(60.0, on_idle)
        self._idle_timer.daemon = True
        self._idle_timer.start()

    def _idle_shutdown_if_needed(self):
        """主线程：空闲时确保资源释放"""
        now = time.time()
        if self.is_recording:
            return
        if now - self._last_interaction_ts < 60.0:
            return
        if self.recorder or self.asr_client:
            logger.info("空闲 60s，自动关闭麦克风/ASR 连接")
            self._reset()

    def _cancel_recording_timeout(self):
        try:
            if self._recording_timeout_timer:
                self._recording_timeout_timer.cancel()
        except Exception:
            pass
        self._recording_timeout_timer = None

    def _arm_recording_timeout(self, session_id: int):
        """录音最长 60 秒，避免忘记松开 Option 导致持续计费"""
        self._cancel_recording_timeout()

        def on_timeout():
            def stop_if_needed():
                if self.is_recording and self._session_id == session_id:
                    logger.warning("录音超时（60s），自动停止以避免持续计费")
                    self.is_option_pressed = False
                    self._stop_recording()
            AppHelper.callAfter(stop_if_needed)

        self._recording_timeout_timer = threading.Timer(60.0, on_timeout)
        self._recording_timeout_timer.daemon = True
        self._recording_timeout_timer.start()

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

    def setupStatusWindow(self):
        """设置状态显示窗口"""
        from .main import StatusBar
        self.status_window = StatusBar()
        self.status_window._setup_window()

    @objc.signature(b'v@:@')
    def openSettings_(self, sender):
        """打开设置窗口"""
        show_settings_window()

    @objc.signature(b'v@:@')
    def openLogViewer_(self, sender):
        """打开日志查看窗口"""
        show_log_viewer()

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
        self._touch_activity()
        with self._lock:
            if self.is_recording:
                return
            self.is_recording = True
            self._stopping = False
            self._session_id += 1
            session_id = self._session_id

        self.current_text = ""
        self.committed_text = ""
        self.saved_clipboard = get_clipboard() or ""

        # 更新菜单栏图标
        self.status_item.button().setTitle_("🔴")
        self.status_menu_item.setTitle_("录音中...")

        # 显示状态窗口
        if self.status_window:
            self.status_window.show("正在连接...")

        # 录音超时保护（60s）
        self._arm_recording_timeout(session_id)

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
        self._touch_activity()
        with self._lock:
            if not self.is_recording:
                return
            self.is_recording = False
            self._stopping = True
            session_id = self._session_id

        # 恢复菜单栏图标
        self.status_item.button().setTitle_("🎤")
        self.status_menu_item.setTitle_("按住 Option 键说话")

        # 停止超时计时器
        self._cancel_recording_timeout()

        # 停止录音
        if self.recorder:
            self.recorder.stop()
            self.recorder = None

        # 异步收尾：避免主线程 sleep 卡顿，同时尽快断开连接避免持续计费
        self._finalize_event = threading.Event()
        self._finalize_wait_session_id = session_id

        threading.Thread(
            target=self._finalize_stop_and_input,
            args=(session_id,),
            daemon=True
        ).start()

    def _finalize_stop_and_input(self, session_id: int):
        """
        子线程：发送 last 包，最多等待一小段时间拿最终结果，然后关闭 ASR 并粘贴
        """
        try:
            # 发送最后一包（尽量不阻塞主线程）
            if self.asr_client:
                try:
                    self.asr_client.send_audio(b"", is_last=True)
                except Exception:
                    pass

            # 等待最终结果（最多 0.8s，超时则用当前累积文本）
            if self._finalize_event:
                self._finalize_event.wait(timeout=0.8)

            # 关闭 ASR（确保 stop 后尽快断开）
            if self.asr_client:
                try:
                    self.asr_client.close()
                except Exception:
                    pass
                self.asr_client = None

            # 隐藏状态窗口（主线程）
            if self.status_window:
                AppHelper.callAfter(self.status_window.hide)

            # 给系统一点时间把焦点还给原 App（避免在主线程 sleep）
            time.sleep(0.08)

            # 防止用户立刻又开始下一次录音导致粘贴错会话
            if self._session_id != session_id:
                return

            full_text = self.committed_text + self.current_text
            if full_text:
                AppHelper.callAfter(lambda: self._do_input(full_text))
        finally:
            if self._finalize_wait_session_id == session_id:
                self._finalize_wait_session_id = None
            self._stopping = False

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
        self._stopping = False
        self._cancel_recording_timeout()
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

        # stop 收尾阶段：一旦收到 definitive 或任意更新，就不必再继续等待
        if self._stopping and self._finalize_wait_session_id == self._session_id:
            if self._finalize_event and (is_definite or text):
                self._finalize_event.set()

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
