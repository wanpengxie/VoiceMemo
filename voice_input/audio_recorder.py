"""
麦克风录音模块
使用 sounddevice 采集音频，按固定间隔输出 PCM 数据
"""

import threading
import logging
from typing import Callable, Optional
import numpy as np
import sounddevice as sd

from . import config

logger = logging.getLogger(__name__)


class AudioRecorder:
    """麦克风录音器"""

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
        self._stream: Optional[sd.InputStream] = None
        self._stop_event = threading.Event()

        # 计算每个 chunk 的采样数
        self.chunk_samples = int(config.AUDIO_RATE * config.CHUNK_DURATION_MS / 1000)

    def _audio_callback(self, indata: np.ndarray, frames: int,
                        time_info, status) -> None:
        """sounddevice 音频回调"""
        if status:
            logger.warning(f"录音状态: {status}")

        if not self._stop_event.is_set():
            # 转换为 16-bit PCM bytes
            audio_int16 = (indata * 32767).astype(np.int16)
            audio_bytes = audio_int16.tobytes()


            try:
                self.on_audio(audio_bytes)
            except Exception as e:
                logger.error(f"音频回调错误: {e}")

    def start(self) -> tuple[bool, str]:
        """
        开始录音
        Returns:
            (是否成功, 错误信息)
        """
        if self._stream is not None:
            return True, ""

        self._stop_event.clear()

        try:
            self._stream = sd.InputStream(
                samplerate=config.AUDIO_RATE,
                channels=config.AUDIO_CHANNEL,
                dtype=np.float32,
                blocksize=self.chunk_samples,
                callback=self._audio_callback
            )
            self._stream.start()
            return True, ""
        except sd.PortAudioError as e:
            error = f"打开麦克风失败: {e}（请检查麦克风权限）"
            logger.error(error)
            return False, error
        except Exception as e:
            error = f"录音初始化失败: {e}"
            logger.error(error)
            return False, error

    def stop(self) -> None:
        """停止录音"""
        self._stop_event.set()

        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception as e:
                logger.warning(f"关闭音频流异常: {e}")
            self._stream = None

    def is_running(self) -> bool:
        """是否正在录音"""
        return self._stream is not None and self._stream.active
