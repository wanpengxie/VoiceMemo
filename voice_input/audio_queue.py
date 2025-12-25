"""
音频队列模块 - 单生产者单消费者 (SPSC) 队列

设计原则：
1. 音频回调线程只入队，不阻塞
2. 发送线程独立消费
3. 会话隔离 - 自动丢弃旧 session 的数据
4. 背压控制 - 队列满时丢弃最旧数据
"""
from __future__ import annotations

from collections import deque
from threading import Event, Lock
from dataclasses import dataclass
from typing import List, Tuple, Optional
import logging
import time

logger = logging.getLogger(__name__)


@dataclass
class AudioFrame:
    """音频帧"""
    session_id: str
    data: bytes
    timestamp: float


class AudioQueue:
    """
    单生产者单消费者音频队列

    特点：
    1. put() 非阻塞 - 音频回调线程调用
    2. get_batch() 可阻塞 - 发送线程调用
    3. 满了自动丢弃最旧数据（保持低延迟）
    4. 支持会话 ID 过滤
    """

    def __init__(self, max_duration_ms: int = 500, frame_ms: int = 100):
        """
        Args:
            max_duration_ms: 最大缓存时长（毫秒）
            frame_ms: 每帧时长（毫秒）
        """
        self.max_frames = max(max_duration_ms // frame_ms, 1)
        self._q: deque[AudioFrame] = deque(maxlen=self.max_frames)
        self._ev = Event()
        self._lock = Lock()
        self._closed = False

        # 统计
        self._total_put = 0
        self._total_dropped = 0
        self._total_get = 0

    def put(self, data: bytes, session_id: str) -> bool:
        """
        入队音频数据

        Args:
            data: PCM 音频数据
            session_id: 会话 ID

        Returns:
            True 如果成功入队，False 如果队列已关闭
        """
        if self._closed:
            return False

        frame = AudioFrame(
            session_id=session_id,
            data=data,
            timestamp=time.time()
        )

        with self._lock:
            # deque(maxlen=N) 满了会自动丢弃最左边的
            if len(self._q) >= self.max_frames:
                self._total_dropped += 1
            self._q.append(frame)
            self._total_put += 1

        # 唤醒消费者
        self._ev.set()
        return True

    def get_batch(self, max_items: int = 10, timeout_s: float = 0.05,
                  current_session: Optional[str] = None) -> List[AudioFrame]:
        """
        批量获取音频帧

        Args:
            max_items: 最多获取的帧数
            timeout_s: 等待超时（秒）
            current_session: 当前会话 ID，非空时过滤掉不匹配的帧

        Returns:
            音频帧列表
        """
        if self._closed and not self._q:
            return []

        # 等待数据
        if not self._q:
            self._ev.wait(timeout_s)

        items: List[AudioFrame] = []
        with self._lock:
            while self._q and len(items) < max_items:
                frame = self._q.popleft()

                # 会话过滤
                if current_session and frame.session_id != current_session:
                    continue

                items.append(frame)
                self._total_get += 1

            if not self._q:
                self._ev.clear()

        return items

    def get_combined_pcm(self, max_items: int = 10, timeout_s: float = 0.05,
                         current_session: Optional[str] = None) -> Tuple[bytes, int]:
        """
        获取合并的 PCM 数据

        Args:
            max_items: 最多获取的帧数
            timeout_s: 等待超时（秒）
            current_session: 当前会话 ID

        Returns:
            (合并的 PCM 数据, 帧数)
        """
        frames = self.get_batch(max_items, timeout_s, current_session)
        if not frames:
            return b"", 0

        pcm = b"".join(f.data for f in frames)
        return pcm, len(frames)

    def flush(self, current_session: Optional[str] = None) -> List[AudioFrame]:
        """
        获取所有剩余数据

        Args:
            current_session: 当前会话 ID

        Returns:
            所有剩余的音频帧
        """
        items: List[AudioFrame] = []
        with self._lock:
            while self._q:
                frame = self._q.popleft()
                if current_session and frame.session_id != current_session:
                    continue
                items.append(frame)
                self._total_get += 1
            self._ev.clear()
        return items

    def clear(self) -> int:
        """
        清空队列

        Returns:
            清空的帧数
        """
        with self._lock:
            count = len(self._q)
            self._q.clear()
            self._ev.clear()
        return count

    def close(self):
        """关闭队列"""
        self._closed = True
        self._ev.set()  # 唤醒等待中的消费者

    def is_empty(self) -> bool:
        """队列是否为空"""
        return len(self._q) == 0

    def size(self) -> int:
        """当前队列大小"""
        return len(self._q)

    def stats(self) -> dict:
        """获取统计信息"""
        return {
            "total_put": self._total_put,
            "total_dropped": self._total_dropped,
            "total_get": self._total_get,
            "current_size": len(self._q),
            "max_size": self.max_frames,
            "drop_rate": self._total_dropped / max(self._total_put, 1),
        }


class AudioSender:
    """
    音频发送器 - 独立线程消费队列并发送

    设计：
    1. 独立线程运行
    2. 批量获取 + 合并发送
    3. 会话隔离
    """

    def __init__(self, queue: AudioQueue):
        """
        Args:
            queue: 音频队列
        """
        self.queue = queue
        self._running = False
        self._thread = None
        self._current_session: Optional[str] = None
        self._send_callback = None

    def start(self, session_id: str, send_callback):
        """
        启动发送器

        Args:
            session_id: 当前会话 ID
            send_callback: 发送回调 (pcm_data: bytes) -> bool
        """
        if self._running:
            return

        self._current_session = session_id
        self._send_callback = send_callback
        self._running = True

        import threading
        self._thread = threading.Thread(target=self._sender_loop, daemon=True)
        self._thread.start()
        logger.info(f"AudioSender 已启动, session={session_id[:8]}")

    def stop(self, flush: bool = True, flush_timeout: float = 1.0):
        """
        停止发送器

        Args:
            flush: 是否 flush 剩余数据
            flush_timeout: flush 超时时间
        """
        if not self._running:
            return

        self._running = False

        if flush and self._send_callback:
            # Flush 剩余数据
            start = time.time()
            while time.time() - start < flush_timeout:
                frames = self.queue.flush(self._current_session)
                if not frames:
                    break
                pcm = b"".join(f.data for f in frames)
                if pcm:
                    try:
                        self._send_callback(pcm)
                    except Exception as e:
                        logger.warning(f"Flush 发送失败: {e}")
                        break

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=0.5)

        logger.info("AudioSender 已停止")

    def update_session(self, session_id: str):
        """更新当前会话 ID"""
        self._current_session = session_id

    def _sender_loop(self):
        """发送线程主循环"""
        while self._running:
            try:
                pcm, count = self.queue.get_combined_pcm(
                    max_items=10,
                    timeout_s=0.05,
                    current_session=self._current_session
                )

                if not pcm:
                    continue

                if self._send_callback:
                    try:
                        self._send_callback(pcm)
                    except Exception as e:
                        logger.error(f"发送音频失败: {e}")
                        # 不中断循环，继续尝试

            except Exception as e:
                logger.error(f"发送线程异常: {e}")
                import traceback
                traceback.print_exc()
