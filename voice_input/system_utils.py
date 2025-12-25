"""
系统工具模块
- 权限检测（麦克风、辅助功能）
- 网络检测
- 系统事件监听（休眠/唤醒、设备变化）
"""

import logging
import socket
from typing import Callable, Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# 权限检测
# ═══════════════════════════════════════════════════════════════════════════════

def check_accessibility_permission() -> bool:
    """
    检查辅助功能权限（pynput 全局按键监听需要）
    Returns:
        True 如果已授权
    """
    try:
        from ApplicationServices import AXIsProcessTrusted
        return AXIsProcessTrusted()
    except ImportError:
        # 如果无法导入，假设有权限（旧系统或非标准环境）
        logger.warning("无法导入 ApplicationServices，跳过辅助功能权限检查")
        return True
    except Exception as e:
        logger.error(f"检查辅助功能权限失败: {e}")
        return True  # 出错时假设有权限，让后续逻辑处理


def request_accessibility_permission() -> bool:
    """
    请求辅助功能权限（会弹出系统设置）
    Returns:
        True 如果已授权或请求已发送
    """
    try:
        from ApplicationServices import AXIsProcessTrustedWithOptions
        from Foundation import NSDictionary

        # kAXTrustedCheckOptionPrompt = True 会触发系统弹窗
        options = NSDictionary.dictionaryWithObject_forKey_(True, "AXTrustedCheckOptionPrompt")
        return AXIsProcessTrustedWithOptions(options)
    except ImportError:
        logger.warning("无法导入 ApplicationServices，无法请求辅助功能权限")
        return False
    except Exception as e:
        logger.error(f"请求辅助功能权限失败: {e}")
        return False


def check_microphone_permission() -> str:
    """
    检查麦克风权限状态
    Returns:
        'authorized' - 已授权
        'denied' - 已拒绝
        'not_determined' - 未决定（未询问过）
        'restricted' - 受限（家长控制等）
        'unknown' - 未知状态
    """
    try:
        from AVFoundation import AVCaptureDevice, AVMediaTypeAudio

        status = AVCaptureDevice.authorizationStatusForMediaType_(AVMediaTypeAudio)

        # AVAuthorizationStatus 枚举值
        # 0: NotDetermined
        # 1: Restricted
        # 2: Denied
        # 3: Authorized
        status_map = {
            0: 'not_determined',
            1: 'restricted',
            2: 'denied',
            3: 'authorized',
        }
        return status_map.get(status, 'unknown')
    except ImportError:
        logger.warning("无法导入 AVFoundation，跳过麦克风权限检查")
        return 'authorized'  # 假设有权限
    except Exception as e:
        logger.error(f"检查麦克风权限失败: {e}")
        return 'unknown'


def request_microphone_permission(callback: Optional[Callable[[bool], None]] = None) -> None:
    """
    请求麦克风权限（异步）
    Args:
        callback: 授权结果回调，参数为是否授权
    """
    try:
        from AVFoundation import AVCaptureDevice, AVMediaTypeAudio

        def on_result(granted):
            logger.info(f"麦克风权限请求结果: {'已授权' if granted else '已拒绝'}")
            if callback:
                callback(granted)

        AVCaptureDevice.requestAccessForMediaType_completionHandler_(
            AVMediaTypeAudio,
            on_result
        )
    except ImportError:
        logger.warning("无法导入 AVFoundation，无法请求麦克风权限")
        if callback:
            callback(False)
    except Exception as e:
        logger.error(f"请求麦克风权限失败: {e}")
        if callback:
            callback(False)


def open_accessibility_settings():
    """打开系统辅助功能设置"""
    try:
        from AppKit import NSWorkspace
        from Foundation import NSURL

        url = NSURL.URLWithString_("x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility")
        NSWorkspace.sharedWorkspace().openURL_(url)
    except Exception as e:
        logger.error(f"打开辅助功能设置失败: {e}")


def open_microphone_settings():
    """打开系统麦克风权限设置"""
    try:
        from AppKit import NSWorkspace
        from Foundation import NSURL

        url = NSURL.URLWithString_("x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone")
        NSWorkspace.sharedWorkspace().openURL_(url)
    except Exception as e:
        logger.error(f"打开麦克风设置失败: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# 网络检测
# ═══════════════════════════════════════════════════════════════════════════════

def check_network_reachable(host: str = "openspeech.bytedance.com",
                           port: int = 443,
                           timeout: float = 0.5) -> bool:
    """
    快速检查网络是否可达
    Args:
        host: 目标主机
        port: 目标端口
        timeout: 超时时间（秒）
    Returns:
        True 如果网络可达
    """
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
        sock.close()
        return True
    except (socket.timeout, socket.error, OSError):
        return False


def check_internet_available(timeout: float = 0.5) -> bool:
    """
    检查是否有互联网连接（使用多个可靠主机）
    """
    # 尝试多个主机，任一成功即可
    hosts = [
        ("openspeech.bytedance.com", 443),  # 豆包服务
        ("www.baidu.com", 443),              # 国内备用
        ("223.5.5.5", 53),                   # 阿里 DNS
    ]

    for host, port in hosts:
        if check_network_reachable(host, port, timeout):
            return True
    return False


# ═══════════════════════════════════════════════════════════════════════════════
# 系统事件监听
# ═══════════════════════════════════════════════════════════════════════════════

class SystemEventListener:
    """系统事件监听器（休眠/唤醒、音频设备变化）"""

    def __init__(self):
        self._on_sleep: Optional[Callable[[], None]] = None
        self._on_wake: Optional[Callable[[], None]] = None
        self._on_audio_device_changed: Optional[Callable[[], None]] = None
        self._observers = []
        self._started = False

    def set_callbacks(self,
                      on_sleep: Optional[Callable[[], None]] = None,
                      on_wake: Optional[Callable[[], None]] = None,
                      on_audio_device_changed: Optional[Callable[[], None]] = None):
        """设置事件回调"""
        self._on_sleep = on_sleep
        self._on_wake = on_wake
        self._on_audio_device_changed = on_audio_device_changed

    def start(self):
        """开始监听系统事件"""
        if self._started:
            return
        self._started = True

        try:
            self._setup_sleep_wake_listener()
        except Exception as e:
            logger.error(f"设置休眠/唤醒监听失败: {e}")

        try:
            self._setup_audio_device_listener()
        except Exception as e:
            logger.error(f"设置音频设备监听失败: {e}")

    def stop(self):
        """停止监听"""
        if not self._started:
            return
        self._started = False

        try:
            from Foundation import NSDistributedNotificationCenter
            from AppKit import NSWorkspace

            # 移除所有观察者
            workspace_nc = NSWorkspace.sharedWorkspace().notificationCenter()
            distributed_nc = NSDistributedNotificationCenter.defaultCenter()

            for observer in self._observers:
                try:
                    workspace_nc.removeObserver_(observer)
                except Exception:
                    pass
                try:
                    distributed_nc.removeObserver_(observer)
                except Exception:
                    pass

            self._observers = []
        except Exception as e:
            logger.error(f"停止系统事件监听失败: {e}")

    def _setup_sleep_wake_listener(self):
        """设置休眠/唤醒监听"""
        from Foundation import NSObject
        from AppKit import NSWorkspace
        import objc

        listener = self

        class SleepWakeObserver(NSObject):
            def willSleep_(self, notification):
                logger.info("系统即将休眠")
                if listener._on_sleep:
                    try:
                        listener._on_sleep()
                    except Exception as e:
                        logger.error(f"休眠回调异常: {e}")

            def didWake_(self, notification):
                logger.info("系统已唤醒")
                if listener._on_wake:
                    try:
                        listener._on_wake()
                    except Exception as e:
                        logger.error(f"唤醒回调异常: {e}")

        observer = SleepWakeObserver.alloc().init()
        self._observers.append(observer)

        nc = NSWorkspace.sharedWorkspace().notificationCenter()

        nc.addObserver_selector_name_object_(
            observer,
            objc.selector(observer.willSleep_, signature=b'v@:@'),
            'NSWorkspaceWillSleepNotification',
            None
        )

        nc.addObserver_selector_name_object_(
            observer,
            objc.selector(observer.didWake_, signature=b'v@:@'),
            'NSWorkspaceDidWakeNotification',
            None
        )

        logger.info("休眠/唤醒监听已启动")

    def _setup_audio_device_listener(self):
        """设置音频设备变化监听"""
        from Foundation import NSObject, NSDistributedNotificationCenter
        import objc

        listener = self

        class AudioDeviceObserver(NSObject):
            def deviceChanged_(self, notification):
                logger.info("音频设备发生变化")
                if listener._on_audio_device_changed:
                    try:
                        listener._on_audio_device_changed()
                    except Exception as e:
                        logger.error(f"音频设备变化回调异常: {e}")

        observer = AudioDeviceObserver.alloc().init()
        self._observers.append(observer)

        center = NSDistributedNotificationCenter.defaultCenter()

        # 监听多个音频相关通知
        notifications = [
            'com.apple.audio.defaultInputDeviceChanged',
            'com.apple.audio.deviceAggregateChangedNotification',
            'com.apple.audio.hardwareConfig',
        ]

        for name in notifications:
            center.addObserver_selector_name_object_(
                observer,
                objc.selector(observer.deviceChanged_, signature=b'v@:@'),
                name,
                None
            )

        logger.info("音频设备变化监听已启动")


# 全局单例
_system_event_listener: Optional[SystemEventListener] = None


def get_system_event_listener() -> SystemEventListener:
    """获取系统事件监听器单例"""
    global _system_event_listener
    if _system_event_listener is None:
        _system_event_listener = SystemEventListener()
    return _system_event_listener


# ═══════════════════════════════════════════════════════════════════════════════
# 错误信息友好化
# ═══════════════════════════════════════════════════════════════════════════════

def friendly_error_message(error: str) -> str:
    """
    将技术性错误信息转换为用户友好的提示
    """
    error_lower = error.lower()

    # PortAudio 相关错误
    if "portaudio" in error_lower or "-9986" in error:
        return "音频设备被占用或异常。请尝试：1) 关闭其他使用麦克风的应用 2) 重启应用"

    if "-9996" in error:
        return "找不到有效的音频输入设备，请检查麦克风是否正确连接"

    if "-9997" in error:
        return "音频设备参数无效，请尝试重启应用"

    if "-9999" in error:
        return "音频设备未初始化，请重启应用"

    # 权限相关
    if "permission" in error_lower or "denied" in error_lower:
        return "权限被拒绝。请在「系统设置 → 隐私与安全性」中授予相应权限"

    # 网络相关
    if "timeout" in error_lower or "timed out" in error_lower:
        return "连接超时，请检查网络连接"

    if "connection refused" in error_lower:
        return "无法连接到服务器，请检查网络设置"

    if "network" in error_lower or "socket" in error_lower:
        return "网络连接异常，请检查网络状态"

    if "ssl" in error_lower or "certificate" in error_lower:
        return "安全连接失败，可能被网络代理或防火墙拦截"

    # API 相关
    if "401" in error or "unauthorized" in error_lower:
        return "API 认证失败，请检查设置中的 App Key 和 Access Key"

    if "403" in error or "forbidden" in error_lower:
        return "访问被拒绝，请检查 API 配额或权限设置"

    if "429" in error or "rate limit" in error_lower:
        return "请求过于频繁，请稍后再试"

    if "500" in error or "502" in error or "503" in error:
        return "服务器暂时不可用，请稍后再试"

    # 设备相关
    if "device" in error_lower and "not found" in error_lower:
        return "找不到音频设备，请检查麦克风/耳机连接"

    if "stream" in error_lower and ("close" in error_lower or "abort" in error_lower):
        return "音频流异常中断，请重试"

    # 如果没有匹配，返回原始错误但加上通用建议
    return f"{error}。如问题持续，请尝试重启应用"


# ═══════════════════════════════════════════════════════════════════════════════
# 单实例检测
# ═══════════════════════════════════════════════════════════════════════════════

_pid_file_path = None
_pid_file_handle = None  # 保持文件句柄，防止 GC 释放锁


def ensure_single_instance(app_name: str = "VoiceInput") -> bool:
    """
    确保只有一个应用实例在运行
    Returns:
        True 如果是唯一实例，False 如果已有实例在运行
    """
    import os
    import fcntl
    from pathlib import Path

    global _pid_file_path, _pid_file_handle

    # PID 文件路径
    pid_dir = Path.home() / "Library" / "Application Support" / app_name
    pid_dir.mkdir(parents=True, exist_ok=True)
    _pid_file_path = pid_dir / "app.pid"

    try:
        # 尝试获取文件锁
        _pid_file_handle = open(_pid_file_path, 'w')
        fcntl.flock(_pid_file_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

        # 写入当前 PID
        _pid_file_handle.write(str(os.getpid()))
        _pid_file_handle.flush()

        # 保持 _pid_file_handle 存活，锁才不会被释放
        # 当进程退出时，锁会自动释放

        logger.info("单实例检测通过")
        return True

    except (IOError, OSError):
        logger.warning("检测到另一个实例正在运行")
        return False


def cleanup_single_instance():
    """清理单实例锁文件"""
    global _pid_file_path
    if _pid_file_path and _pid_file_path.exists():
        try:
            _pid_file_path.unlink()
        except Exception:
            pass
