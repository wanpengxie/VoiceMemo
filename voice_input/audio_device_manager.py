"""
音频设备管理模块
- 查询可用输入设备
- 设备优先级（耳机/外置 > 内置）
- 设备变化检测
- 持久化用户选择
"""

import threading
import logging
import subprocess
from typing import List, Optional, Callable, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# 延迟导入 sounddevice
_sd = None


def _get_sounddevice():
    """延迟导入 sounddevice"""
    global _sd
    if _sd is None:
        import sounddevice as sd
        _sd = sd
    return _sd


def _get_macos_audio_inputs() -> List[Tuple[str, bool]]:
    """
    使用 macOS 原生方式获取音频输入设备列表（无缓存问题）
    Returns:
        [(设备名称, 是否默认), ...]
    """
    devices = []
    try:
        # 使用 system_profiler 获取音频设备信息
        result = subprocess.run(
            ['system_profiler', 'SPAudioDataType', '-json'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            import json
            data = json.loads(result.stdout)
            audio_data = data.get('SPAudioDataType', [])

            for item in audio_data:
                # 查找输入设备
                if '_items' in item:
                    for dev in item['_items']:
                        dev_name = dev.get('_name', '')
                        # 检查是否有输入通道
                        coreaudio = dev.get('coreaudio_device_input', 0)
                        if coreaudio and int(coreaudio) > 0:
                            is_default = 'coreaudio_default_audio_input_device' in dev
                            devices.append((dev_name, is_default))
    except Exception as e:
        logger.debug(f"system_profiler 获取设备失败: {e}")

    # 备用方案：使用 CoreAudio API
    if not devices:
        try:
            from AudioToolbox import (
                AudioObjectGetPropertyData,
                AudioObjectGetPropertyDataSize,
                kAudioHardwarePropertyDevices,
                kAudioObjectSystemObject,
                kAudioObjectPropertyScopeGlobal,
                kAudioObjectPropertyElementMain,
                kAudioDevicePropertyDeviceNameCFString,
                kAudioDevicePropertyStreamConfiguration,
                kAudioObjectPropertyScopeInput,
            )
            # 这个方法比较复杂，作为备用
        except ImportError:
            pass

    return devices


def _refresh_portaudio():
    """刷新 PortAudio 设备列表 - 解决设备缓存问题"""
    global _sd
    try:
        # 强制重新加载 sounddevice 模块（最可靠的方法）
        import sys

        # 移除所有相关模块的缓存
        modules_to_remove = [k for k in sys.modules.keys()
                            if 'sounddevice' in k or '_sounddevice' in k]
        for mod in modules_to_remove:
            try:
                del sys.modules[mod]
            except Exception:
                pass

        # 重新导入
        import sounddevice as sd
        _sd = sd
        logger.debug(f"sounddevice 已重新加载，检测到 {len(sd.query_devices())} 个设备")

    except Exception as e:
        logger.warning(f"刷新 PortAudio 失败: {e}")
        # 回退：尝试使用 _terminate/_initialize
        try:
            sd = _get_sounddevice()
            if hasattr(sd, '_terminate') and hasattr(sd, '_initialize'):
                sd._terminate()
                sd._initialize()
        except Exception:
            pass


@dataclass
class AudioDevice:
    """音频输入设备"""
    id: int
    name: str
    channels: int
    is_default: bool = False
    priority: int = 0  # 优先级，数字越大优先级越高

    def __str__(self):
        default_mark = " (默认)" if self.is_default else ""
        return f"{self.name}{default_mark}"


class AudioDeviceManager:
    """
    音频设备管理器

    功能：
    1. 查询可用输入设备
    2. 智能选择设备（优先耳机/外置麦克风）
    3. 支持用户手动选择
    4. 后台轮询设备变化
    """

    # 耳机/外置麦克风的关键词（优先使用）
    PRIORITY_KEYWORDS = [
        'airpods',
        'headphone',
        'headset',
        'earphone',
        'earbud',
        'usb',
        'external',
        'bluetooth',
        '耳机',
        '外置',
        '蓝牙',
    ]

    # 内置麦克风关键词（低优先级）
    BUILTIN_KEYWORDS = [
        'built-in',
        'internal',
        'macbook',
        '内置',
    ]

    def __init__(self):
        self._devices: List[AudioDevice] = []
        self._selected_device_id: Optional[int] = None  # 用户手动选择的设备 ID
        self._lock = threading.Lock()
        self._poll_thread: Optional[threading.Thread] = None
        self._poll_stop = threading.Event()
        self._on_devices_changed: Optional[Callable[[], None]] = None

    def set_on_devices_changed(self, callback: Callable[[], None]):
        """设置设备变化回调"""
        self._on_devices_changed = callback

    def refresh_devices(self, force_refresh: bool = False) -> List[AudioDevice]:
        """
        刷新设备列表

        Args:
            force_refresh: 是否强制刷新 PortAudio（会中断录音，只在菜单打开时使用）
        """
        # 先用 macOS 原生 API 获取真实设备列表（无缓存）
        macos_devices = []
        if force_refresh:
            macos_devices = _get_macos_audio_inputs()
            if macos_devices:
                logger.info(f"macOS 原生检测到 {len(macos_devices)} 个输入设备: {[d[0] for d in macos_devices]}")

        # 刷新 PortAudio
        if force_refresh:
            _refresh_portaudio()

        sd = _get_sounddevice()
        devices = []

        try:
            all_devices = sd.query_devices()
            logger.debug(f"sounddevice 查询到 {len(all_devices)} 个音频设备")
            default_input_id = None

            # 获取默认输入设备 ID
            try:
                default_input = sd.query_devices(kind='input')
                default_input_id = list(all_devices).index(default_input) if default_input else None
            except Exception:
                pass

            for i, dev in enumerate(all_devices):
                max_inputs = dev.get('max_input_channels', 0)
                if max_inputs > 0:
                    name = dev.get('name', f'Device {i}')
                    priority = self._calculate_priority(name)
                    devices.append(AudioDevice(
                        id=i,
                        name=name,
                        channels=max_inputs,
                        is_default=(i == default_input_id),
                        priority=priority
                    ))

            # 按优先级排序（高优先级在前）
            devices.sort(key=lambda d: (-d.priority, -d.is_default, d.name))

            # 如果有 macOS 原生设备列表，用它来过滤掉不存在的设备
            if macos_devices:
                macos_names = {name.lower() for name, _ in macos_devices}
                filtered = []
                for dev in devices:
                    # 检查设备名是否在 macOS 列表中（模糊匹配）
                    dev_name_lower = dev.name.lower()
                    exists = any(
                        macos_name in dev_name_lower or dev_name_lower in macos_name
                        for macos_name in macos_names
                    )
                    if exists:
                        filtered.append(dev)
                    else:
                        logger.info(f"过滤掉不存在的设备: {dev.name}")
                if filtered:  # 只有在过滤后还有设备时才使用过滤结果
                    devices = filtered

        except Exception as e:
            logger.error(f"刷新设备列表失败: {e}")

        with self._lock:
            old_devices = self._devices
            self._devices = devices

            # 检查是否有变化
            changed = self._devices_changed(old_devices, devices)
            if changed:
                old_names = [d.name for d in old_devices]
                new_names = [d.name for d in devices]
                logger.info(f"设备列表变化: {old_names} → {new_names}")
                if self._on_devices_changed:
                    try:
                        self._on_devices_changed()
                    except Exception as e:
                        logger.error(f"设备变化回调异常: {e}")

        logger.debug(f"当前输入设备: {[d.name for d in devices]}")
        return devices

    def _calculate_priority(self, name: str) -> int:
        """计算设备优先级"""
        name_lower = name.lower()

        # 检查是否是高优先级设备（耳机/外置）
        for keyword in self.PRIORITY_KEYWORDS:
            if keyword in name_lower:
                return 100

        # 检查是否是内置设备（低优先级）
        for keyword in self.BUILTIN_KEYWORDS:
            if keyword in name_lower:
                return 10

        # 默认优先级
        return 50

    def _devices_changed(self, old: List[AudioDevice], new: List[AudioDevice]) -> bool:
        """检查设备列表是否变化"""
        if len(old) != len(new):
            return True
        old_ids = {d.id for d in old}
        new_ids = {d.id for d in new}
        return old_ids != new_ids

    def get_devices(self) -> List[AudioDevice]:
        """获取当前设备列表"""
        with self._lock:
            return list(self._devices)

    def get_selected_device(self) -> Optional[AudioDevice]:
        """获取当前选择的设备"""
        with self._lock:
            # 如果用户手动选择了设备
            if self._selected_device_id is not None:
                for d in self._devices:
                    if d.id == self._selected_device_id:
                        return d
                # 用户选择的设备已不存在，清除选择
                self._selected_device_id = None

            # 返回最高优先级的设备
            if self._devices:
                return self._devices[0]

            return None

    def get_selected_device_id(self) -> Optional[int]:
        """获取当前选择的设备 ID（用于录音）"""
        device = self.get_selected_device()
        if device:
            # 如果是默认设备且用户没有手动选择，返回 None 让系统使用默认
            with self._lock:
                if device.is_default and self._selected_device_id is None:
                    return None
                return device.id
        return None

    def select_device(self, device_id: Optional[int]):
        """
        选择设备

        Args:
            device_id: 设备 ID，None 表示使用自动选择（优先耳机）
        """
        with self._lock:
            self._selected_device_id = device_id

        if device_id is None:
            logger.info("设备选择: 自动（优先耳机/外置）")
        else:
            device = self.get_device_by_id(device_id)
            if device:
                logger.info(f"设备选择: {device.name}")

    def get_device_by_id(self, device_id: int) -> Optional[AudioDevice]:
        """根据 ID 获取设备"""
        with self._lock:
            for d in self._devices:
                if d.id == device_id:
                    return d
        return None

    def is_auto_select(self) -> bool:
        """是否处于自动选择模式"""
        with self._lock:
            return self._selected_device_id is None

    def start_polling(self, interval: float = 3.0):
        """
        开始轮询设备变化

        Args:
            interval: 轮询间隔（秒）
        """
        if self._poll_thread and self._poll_thread.is_alive():
            return

        self._poll_stop.clear()

        def poll_loop():
            while not self._poll_stop.wait(interval):
                try:
                    self.refresh_devices()
                except Exception as e:
                    logger.error(f"轮询设备失败: {e}")

        self._poll_thread = threading.Thread(target=poll_loop, daemon=True)
        self._poll_thread.start()
        logger.info(f"设备轮询已启动，间隔 {interval}s")

    def stop_polling(self):
        """停止轮询"""
        self._poll_stop.set()
        if self._poll_thread:
            self._poll_thread.join(timeout=1.0)
            self._poll_thread = None
        logger.info("设备轮询已停止")


# 全局单例
_device_manager: Optional[AudioDeviceManager] = None


def get_device_manager() -> AudioDeviceManager:
    """获取设备管理器单例"""
    global _device_manager
    if _device_manager is None:
        _device_manager = AudioDeviceManager()
        # 初始化时刷新一次
        _device_manager.refresh_devices()
    return _device_manager
