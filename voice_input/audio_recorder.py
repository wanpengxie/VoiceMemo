"""
麦克风录音模块
使用 sounddevice 采集音频，按固定间隔输出 PCM 数据
增强版：更好的错误处理和设备变化检测
"""

import threading
import logging
from typing import Callable, Optional
import numpy as np

from . import config
from .audio_device_manager import get_device_manager

logger = logging.getLogger(__name__)

# 延迟导入 sounddevice，避免导入时崩溃
_sd = None


def _get_sounddevice():
    """延迟导入 sounddevice"""
    global _sd
    if _sd is None:
        import sounddevice as sd
        _sd = sd
    return _sd


def _reset_portaudio():
    """重置 PortAudio - 解决设备占用问题"""
    global _sd
    try:
        if _sd is not None:
            # 尝试终止并重新初始化
            try:
                import _sounddevice
                _sounddevice._terminate()
                _sounddevice._initialize()
                logger.info("PortAudio 已重新初始化")
            except Exception:
                # 如果上面的方法不行，尝试重新导入
                import importlib
                import sounddevice as sd
                importlib.reload(sd)
                _sd = sd
                logger.info("sounddevice 已重新加载")
    except Exception as e:
        logger.warning(f"重置 PortAudio 失败: {e}")


class AudioRecorder:
    """麦克风录音器 - 增强版，更好的错误处理"""

    def __init__(
        self,
        on_audio: Callable[[bytes], None],
        on_error: Optional[Callable[[str], None]] = None
    ):
        """
        Args:
            on_audio: 音频数据回调，每 CHUNK_DURATION_MS 毫秒触发一次
            on_error: 错误回调（可选）
        """
        self.on_audio = on_audio
        self.on_error = on_error or (lambda x: None)
        self._stream = None
        self._stop_event = threading.Event()
        self._error_count = 0
        self._max_errors = 5  # 最大连续错误次数

        # 计算每个 chunk 的采样数
        self.chunk_samples = int(config.AUDIO_RATE * config.CHUNK_DURATION_MS / 1000)

    def _audio_callback(self, indata: np.ndarray, frames: int,
                        time_info, status) -> None:
        """sounddevice 音频回调 - 增强错误处理"""
        try:
            # 检测音频流状态问题
            if status:
                status_str = str(status)
                logger.warning(f"录音状态警告: {status_str}")

                # 检测严重错误
                if "input overflow" in status_str.lower():
                    self._error_count += 1
                elif "input underflow" in status_str.lower():
                    self._error_count += 1
                elif "priming" not in status_str.lower():
                    # priming 是正常的初始化状态，不算错误
                    self._error_count += 1

                # 错误过多，触发重连
                if self._error_count >= self._max_errors:
                    logger.error(f"音频流错误过多 ({self._error_count})，触发错误回调")
                    self._error_count = 0
                    self.on_error("音频设备异常，请检查麦克风连接")
                    return
            else:
                # 正常数据，重置错误计数
                self._error_count = 0

            if not self._stop_event.is_set():
                # 检查数据有效性
                if indata is None or len(indata) == 0:
                    return

                # 转换为 16-bit PCM bytes
                audio_int16 = (indata * 32767).astype(np.int16)
                audio_bytes = audio_int16.tobytes()

                try:
                    self.on_audio(audio_bytes)
                except Exception as e:
                    logger.error(f"音频回调错误: {e}")

        except Exception as e:
            logger.error(f"音频回调处理异常: {e}")
            import traceback
            traceback.print_exc()

    def _get_available_input_devices(self) -> list:
        """获取所有可用的输入设备列表"""
        sd = _get_sounddevice()
        devices = []
        try:
            all_devices = sd.query_devices()
            for i, dev in enumerate(all_devices):
                if dev.get('max_input_channels', 0) > 0:
                    devices.append((i, dev.get('name', f'Device {i}')))
        except Exception as e:
            logger.warning(f"查询设备列表失败: {e}")
        return devices

    def _try_open_device(self, device_id: int = None, device_name: str = None) -> tuple[bool, str]:
        """尝试打开指定设备"""
        sd = _get_sounddevice()
        try:
            self._stream = sd.InputStream(
                device=device_id,
                samplerate=config.AUDIO_RATE,
                channels=config.AUDIO_CHANNEL,
                dtype=np.float32,
                blocksize=self.chunk_samples,
                callback=self._audio_callback
            )
            self._stream.start()
            logger.info(f"音频录音已启动，使用设备: {device_name or device_id or '默认'}")
            return True, ""
        except Exception as e:
            if self._stream:
                try:
                    self._stream.close()
                except Exception:
                    pass
                self._stream = None
            return False, str(e)

    def start(self) -> tuple[bool, str]:
        """
        开始录音 - 增强版，带自动重试和设备切换
        Returns:
            (是否成功, 错误信息)
        """
        if self._stream is not None:
            return True, ""

        self._stop_event.clear()
        self._error_count = 0

        # 先刷新设备列表，确保检测到新连接的设备
        _reset_portaudio()

        sd = _get_sounddevice()
        last_error = ""

        # 第零步：检查设备管理器是否有用户选择的设备
        try:
            device_manager = get_device_manager()
            selected_device = device_manager.get_selected_device()
            selected_id = device_manager.get_selected_device_id()

            if selected_device:
                logger.info(f"使用设备管理器选择的设备: {selected_device.name} (ID: {selected_id})")
                success, error = self._try_open_device(
                    device_id=selected_id,
                    device_name=selected_device.name
                )
                if success:
                    return True, ""
                logger.warning(f"选定设备失败: {error}，尝试备选...")
                last_error = error
        except Exception as e:
            logger.warning(f"读取设备管理器失败: {e}")

        # 第一步：尝试默认设备
        try:
            default_input = sd.query_devices(kind='input')
            default_name = default_input.get('name', 'Unknown')
            logger.info(f"尝试默认音频输入设备: {default_name}")
        except Exception as e:
            logger.warning(f"查询默认音频设备失败: {e}")
            default_name = "Unknown"

        success, error = self._try_open_device(device_name=default_name)
        if success:
            return True, ""
        last_error = error
        logger.warning(f"默认设备失败: {error}")

        # 第二步：重置 PortAudio 后再试默认设备
        logger.info("重置 PortAudio 后重试默认设备...")
        _reset_portaudio()
        import time
        time.sleep(0.1)

        success, error = self._try_open_device(device_name=default_name)
        if success:
            return True, ""
        last_error = error
        logger.warning(f"重置后默认设备仍失败: {error}")

        # 第三步：按优先级尝试其他可用输入设备（优先耳机/外置）
        logger.info("尝试切换到其他可用输入设备...")
        try:
            device_manager = get_device_manager()
            device_manager.refresh_devices()
            devices = device_manager.get_devices()  # 已按优先级排序
            logger.info(f"可用输入设备（按优先级）: {[d.name for d in devices]}")

            for device in devices:
                logger.info(f"尝试设备: {device.name} (ID: {device.id}, 优先级: {device.priority})")
                success, error = self._try_open_device(device_id=device.id, device_name=device.name)
                if success:
                    return True, ""
                logger.warning(f"设备 {device.name} 失败: {error}")
                last_error = error
        except Exception as e:
            logger.warning(f"从设备管理器获取设备失败: {e}，使用原始方法")
            # 回退到原始方法
            available_devices = self._get_available_input_devices()
            logger.info(f"可用输入设备: {available_devices}")

            for device_id, device_name in available_devices:
                logger.info(f"尝试设备: {device_name} (ID: {device_id})")
                success, error = self._try_open_device(device_id=device_id, device_name=device_name)
                if success:
                    return True, ""
                logger.warning(f"设备 {device_name} 失败: {error}")
                last_error = error

        # 所有设备都失败了
        error_str = last_error
        if "PortAudio" in error_str or "portaudio" in error_str.lower() or "-9986" in error_str:
            error = f"所有音频设备都不可用，请检查麦克风连接或重启应用"
        elif "device" in error_str.lower():
            error = f"音频设备错误: {last_error}（请检查耳机/麦克风连接）"
        elif "permission" in error_str.lower() or "denied" in error_str.lower():
            error = "麦克风权限被拒绝，请在系统设置中授权"
        else:
            error = f"录音初始化失败: {last_error}"

        return False, error

    def stop(self) -> None:
        """停止录音 - 确保麦克风被完全释放"""
        self._stop_event.set()

        stream = self._stream
        self._stream = None  # 先置空，防止并发访问

        if stream:
            try:
                # 先尝试中止（比 stop 更彻底）
                if hasattr(stream, 'abort'):
                    stream.abort()
                elif stream.active:
                    stream.stop()
            except Exception as e:
                logger.warning(f"停止音频流异常: {e}")

            try:
                # 关闭流
                stream.close()
            except Exception as e:
                logger.warning(f"关闭音频流异常: {e}")

            # 给系统一点时间释放资源
            import time
            time.sleep(0.05)

        logger.info("音频录音已停止，麦克风已释放")

    def force_release(self) -> None:
        """强制释放所有音频资源"""
        self.stop()

        # 尝试重置 PortAudio（某些情况下需要）
        try:
            sd = _get_sounddevice()
            # 查询设备会触发 PortAudio 重新初始化
            sd.query_devices()
        except Exception as e:
            logger.debug(f"重置音频设备: {e}")

    def is_running(self) -> bool:
        """是否正在录音"""
        try:
            return self._stream is not None and self._stream.active
        except Exception:
            return False
