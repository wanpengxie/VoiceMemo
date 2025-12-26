"""
录音协调器 - 核心控制模块

设计原则：
1. 单写者 - 只有 Coordinator 能改状态
2. 事件驱动 - 所有模块通过 post_event 投递事件
3. 主线程执行副作用 - 确保 UI 操作在主线程
"""
from __future__ import annotations

import threading
import logging
import time
from queue import Queue
from typing import Callable, Optional, Any
from dataclasses import dataclass

import numpy as np

from .state_machine import (
    State, StateContext, StateMachine,
    Event, EventType, Effect, EffectType
)
from .audio_queue import AudioQueue, AudioSender
from .audio_recorder import AudioRecorder
from .asr_client import ASRClient
from .system_utils import (
    check_microphone_permission, request_microphone_permission,
    check_accessibility_permission, request_accessibility_permission,
    check_network_reachable, get_system_event_listener,
    friendly_error_message, open_microphone_settings
)
from . import config

logger = logging.getLogger(__name__)


@dataclass
class CoordinatorCallbacks:
    """协调器回调接口"""
    on_state_change: Optional[Callable[[State, State], None]] = None  # (old, new)
    on_ui_update: Optional[Callable[[Optional[str]], None]] = None    # 更新状态显示
    on_error: Optional[Callable[[str], None]] = None                  # 显示错误
    on_text_commit: Optional[Callable[[str], None]] = None            # 提交识别文本
    on_text_update: Optional[Callable[[str, bool], None]] = None      # 实时文本更新


class RecordingCoordinator:
    """
    录音协调器

    职责：
    1. 管理状态机
    2. 协调各模块（音频、ASR、监控）
    3. 执行副作用
    4. 保证线程安全
    """

    def __init__(self, callbacks: Optional[CoordinatorCallbacks] = None):
        """
        Args:
            callbacks: 回调接口
        """
        self.callbacks = callbacks or CoordinatorCallbacks()

        # 状态机
        self._ctx = StateContext()
        self._ctx_lock = threading.Lock()

        # 事件队列
        self._event_queue: Queue[Event] = Queue()
        self._event_thread: Optional[threading.Thread] = None
        self._running = False

        # 音频管道（增加缓存时长以应对 ARMING 初始化延迟）
        self._audio_queue = AudioQueue(max_duration_ms=2000, frame_ms=100)
        self._audio_sender: Optional[AudioSender] = None
        self._recorder: Optional[AudioRecorder] = None
        self._asr_client: Optional[ASRClient] = None
        self._resource_lock = threading.Lock()  # 保护 _recorder 和 _asr_client

        # 计时器
        self._flush_timer: Optional[threading.Timer] = None
        self._silence_check_timer: Optional[threading.Timer] = None  # 静音检测定时器
        self._idle_timer: Optional[threading.Timer] = None
        self._arming_timeout_timer: Optional[threading.Timer] = None
        self._error_recover_timer: Optional[threading.Timer] = None

        # 静音检测（时间戳方案）
        self._last_voice_time: float = 0.0  # 最后检测到声音的时间
        self._silence_check_session: Optional[str] = None  # 静音检测绑定的会话

        # 系统事件监听
        self._system_listener = get_system_event_listener()

        # 主线程回调（用于在主线程执行副作用）
        self._main_thread_callback: Optional[Callable[[Callable], None]] = None

    def set_main_thread_callback(self, callback: Callable[[Callable], None]):
        """
        设置主线程执行回调

        Args:
            callback: 在主线程执行给定函数的方法（如 AppHelper.callAfter）
        """
        self._main_thread_callback = callback

    def start(self):
        """启动协调器"""
        if self._running:
            return

        self._running = True

        # 启动事件处理线程
        self._event_thread = threading.Thread(target=self._event_loop, daemon=True)
        self._event_thread.start()

        # 启动系统事件监听
        self._system_listener.set_callbacks(
            on_sleep=lambda: self.post_event(Event(EventType.S_SYSTEM_WILL_SLEEP)),
            on_wake=lambda: self.post_event(Event(EventType.S_SYSTEM_DID_WAKE)),
            on_audio_device_changed=lambda: self.post_event(Event(EventType.S_DEFAULT_INPUT_CHANGED)),
        )
        self._system_listener.start()

        # 启动空闲检测
        self._reset_idle_timer()

        logger.info("RecordingCoordinator 已启动")

    def stop(self):
        """停止协调器"""
        if not self._running:
            return

        self._running = False

        # 取消所有计时器
        self._cancel_timer(self._flush_timer)
        self._cancel_timer(self._silence_check_timer)
        self._cancel_timer(self._idle_timer)
        self._cancel_timer(self._arming_timeout_timer)
        self._cancel_timer(self._error_recover_timer)

        # 停止系统监听
        self._system_listener.stop()

        # 释放资源
        self._release_resources()

        # 等待事件线程结束
        self.post_event(Event(EventType.U_QUIT))
        if self._event_thread and self._event_thread.is_alive():
            self._event_thread.join(timeout=1.0)

        logger.info("RecordingCoordinator 已停止")

    def post_event(self, event: Event):
        """
        投递事件

        Args:
            event: 事件
        """
        if not event.session_id:
            event.session_id = self._ctx.session_id
        self._event_queue.put(event)

    @property
    def state(self) -> State:
        """当前状态"""
        return self._ctx.state

    @property
    def session_id(self) -> Optional[str]:
        """当前会话 ID"""
        return self._ctx.session_id

    @property
    def is_recording(self) -> bool:
        """是否正在录音"""
        return self._ctx.state in (State.ARMING, State.RECORDING)

    # ═══════════════════════════════════════════════════════════════════════════════
    # 用户操作接口
    # ═══════════════════════════════════════════════════════════════════════════════

    def user_start(self):
        """用户开始录音"""
        logger.info("user_start() 被调用")
        self.post_event(Event(EventType.U_START))

    def user_stop(self):
        """用户停止录音"""
        logger.info("user_stop() 被调用")
        self.post_event(Event(EventType.U_STOP))

    # ═══════════════════════════════════════════════════════════════════════════════
    # 事件处理
    # ═══════════════════════════════════════════════════════════════════════════════

    def _event_loop(self):
        """事件处理主循环"""
        while self._running:
            try:
                event = self._event_queue.get(timeout=0.1)
            except Exception:
                continue

            if event.type == EventType.U_QUIT:
                break

            self._handle_event(event)

    def _handle_event(self, event: Event):
        """处理单个事件"""
        logger.info(f"处理事件: {event.type.name}, 当前状态: {self._ctx.state.name}")
        with self._ctx_lock:
            old_state = self._ctx.state
            self._ctx, effects = StateMachine.handle(self._ctx, event)
            new_state = self._ctx.state
        logger.info(f"事件处理完成: {old_state.name} → {new_state.name}, 副作用数: {len(effects)}")

        # 状态变化通知
        if old_state != new_state and self.callbacks.on_state_change:
            self._run_on_main_thread(
                lambda: self.callbacks.on_state_change(old_state, new_state)
            )

        # 执行副作用
        for effect in effects:
            self._execute_effect(effect)

    def _execute_effect(self, effect: Effect):
        """执行副作用"""
        etype = effect.type
        data = effect.data

        try:
            if etype == EffectType.CHECK_PERMISSIONS:
                self._check_permissions()

            elif etype == EffectType.INIT_AUDIO:
                self._init_audio()

            elif etype == EffectType.CONNECT_TRANSPORT:
                self._connect_transport()

            elif etype == EffectType.START_CAPTURE:
                self._start_capture()

            elif etype == EffectType.STOP_CAPTURE:
                self._stop_capture()

            elif etype == EffectType.CLOSE_TRANSPORT:
                self._close_transport()

            elif etype == EffectType.RELEASE_RESOURCES:
                self._release_resources()

            elif etype == EffectType.FLUSH_QUEUE:
                self._flush_queue()

            elif etype == EffectType.UPDATE_UI:
                if self.callbacks.on_ui_update:
                    self._run_on_main_thread(
                        lambda: self.callbacks.on_ui_update(data)
                    )

            elif etype == EffectType.SHOW_ERROR:
                if self.callbacks.on_error:
                    self._run_on_main_thread(
                        lambda: self.callbacks.on_error(data)
                    )

            elif etype == EffectType.COMMIT_TEXT:
                if data and self.callbacks.on_text_commit:
                    self._run_on_main_thread(
                        lambda: self.callbacks.on_text_commit(data)
                    )

            elif etype == EffectType.ARM_FLUSH_TIMEOUT:
                self._arm_flush_timeout(data)

            elif etype == EffectType.CANCEL_FLUSH_TIMEOUT:
                self._cancel_timer(self._flush_timer)

            elif etype == EffectType.ARM_ARMING_TIMEOUT:
                self._arm_arming_timeout(data)

            elif etype == EffectType.CANCEL_ARMING_TIMEOUT:
                self._cancel_timer(self._arming_timeout_timer)

            elif etype == EffectType.ARM_ERROR_RECOVER:
                self._arm_error_recover(data)

            elif etype == EffectType.CANCEL_ERROR_RECOVER:
                self._cancel_timer(self._error_recover_timer)

        except Exception as e:
            logger.error(f"执行副作用失败 {effect}: {e}")
            import traceback
            traceback.print_exc()

    def _run_on_main_thread(self, func: Callable):
        """在主线程执行函数"""
        if self._main_thread_callback:
            self._main_thread_callback(func)
        else:
            func()

    # ═══════════════════════════════════════════════════════════════════════════════
    # 副作用实现
    # ═══════════════════════════════════════════════════════════════════════════════

    def _check_permissions(self):
        """检查权限"""
        # 捕获当前 session_id，防止异步完成时 session 已变化
        captured_session = self._ctx.session_id

        def check():
            # 检查辅助功能权限
            if not check_accessibility_permission():
                logger.warning("辅助功能权限未授权")
                request_accessibility_permission()
                self.post_event(Event(EventType.E_ACCESSIBILITY_DENIED, session_id=captured_session))
                return

            # 检查麦克风权限
            mic_status = check_microphone_permission()
            if mic_status == 'authorized':
                self.post_event(Event(EventType.S_MIC_PERMISSION_OK, session_id=captured_session))
            elif mic_status == 'not_determined':
                def on_result(granted):
                    if granted:
                        self.post_event(Event(EventType.S_MIC_PERMISSION_OK, session_id=captured_session))
                    else:
                        self.post_event(Event(EventType.E_MIC_PERMISSION_DENIED, session_id=captured_session))
                request_microphone_permission(on_result)
            else:
                self.post_event(Event(EventType.E_MIC_PERMISSION_DENIED, session_id=captured_session))

        threading.Thread(target=check, daemon=True).start()

    def _init_audio(self):
        """初始化音频设备"""
        # 捕获当前 session_id，防止异步完成时 session 已变化
        captured_session = self._ctx.session_id

        def init():
            try:
                recorder = AudioRecorder(
                    on_audio=lambda data: self._on_audio_data(data, captured_session),
                    on_error=self._on_recorder_error
                )
                success, error = recorder.start()
                if success:
                    # 检查 session 是否还匹配，避免覆盖新会话的资源
                    with self._resource_lock:
                        if self._ctx.session_id == captured_session:
                            self._recorder = recorder
                        else:
                            # session 已变化，释放资源
                            logger.info(f"音频初始化完成但 session 已变化，释放资源")
                            recorder.force_release()
                            return
                    self.post_event(Event(EventType.S_AUDIO_READY, session_id=captured_session))
                else:
                    recorder.force_release()
                    self.post_event(Event(
                        EventType.E_AUDIO_INIT_FAILED,
                        detail=friendly_error_message(error),
                        session_id=captured_session
                    ))
            except Exception as e:
                self.post_event(Event(
                    EventType.E_AUDIO_INIT_FAILED,
                    detail=str(e),
                    session_id=captured_session
                ))

        threading.Thread(target=init, daemon=True).start()

    def _connect_transport(self):
        """建立 ASR 连接"""
        # 捕获当前 session_id，防止异步完成时 session 已变化
        captured_session = self._ctx.session_id

        def connect():
            # 先检查网络
            if not check_network_reachable():
                self.post_event(Event(EventType.E_NETWORK_UNAVAILABLE, session_id=captured_session))
                return

            try:
                asr_client = ASRClient(
                    on_result=lambda text, is_def: self._on_asr_result(text, is_def, captured_session),
                    on_error=self._on_asr_error
                )
                success, error = asr_client.connect()
                if success:
                    with self._resource_lock:
                        self._asr_client = asr_client
                    self.post_event(Event(EventType.S_TRANSPORT_CONNECTED, session_id=captured_session))
                else:
                    try:
                        asr_client.close()
                    except Exception:
                        pass
                    self.post_event(Event(
                        EventType.E_TRANSPORT_ERROR,
                        detail=friendly_error_message(error),
                        session_id=captured_session
                    ))
            except Exception as e:
                self.post_event(Event(
                    EventType.E_TRANSPORT_ERROR,
                    detail=str(e),
                    session_id=captured_session
                ))

        threading.Thread(target=connect, daemon=True).start()

    def _start_capture(self):
        """开始音频采集和发送"""
        session_id = self._ctx.session_id
        if not session_id:
            return

        # 启动发送器
        self._audio_sender = AudioSender(self._audio_queue)
        self._audio_sender.start(
            session_id=session_id,
            send_callback=self._send_audio_to_asr
        )

        # 启动静音超时保护（持续静音超过指定时间自动停止）
        self._arm_silence_timeout(config.SILENCE_TIMEOUT_S)

        logger.info(f"音频采集已启动, session={session_id[:8]}")

    def _stop_capture(self):
        """停止音频采集"""
        self._cancel_timer(self._silence_check_timer)
        self._silence_check_session = None

        with self._resource_lock:
            recorder = self._recorder
        if recorder:
            try:
                recorder.stop()
            except Exception as e:
                logger.warning(f"停止录音异常: {e}")

        logger.info("音频采集已停止")

    def _close_transport(self):
        """关闭 ASR 连接"""
        if self._audio_sender:
            self._audio_sender.stop(flush=False)
            self._audio_sender = None

        with self._resource_lock:
            asr_client = self._asr_client
            self._asr_client = None
        if asr_client:
            try:
                asr_client.close()
            except Exception as e:
                logger.warning(f"关闭 ASR 连接异常: {e}")

        logger.info("ASR 连接已关闭")

    def _release_resources(self):
        """释放所有资源"""
        self._stop_capture()
        self._close_transport()

        with self._resource_lock:
            recorder = self._recorder
            self._recorder = None
        if recorder:
            try:
                recorder.force_release()
            except Exception as e:
                logger.warning(f"释放录音资源异常: {e}")

        self._audio_queue.clear()
        logger.info("所有资源已释放")

    def _flush_queue(self):
        """Flush 音频队列"""
        if self._audio_sender:
            self._audio_sender.stop(flush=True, flush_timeout=0.5)
            self._audio_sender = None

        # 发送 last 包
        with self._resource_lock:
            asr_client = self._asr_client
        if asr_client:
            try:
                asr_client.send_audio(b"", is_last=True)
            except Exception as e:
                logger.warning(f"发送 last 包失败: {e}")

    # ═══════════════════════════════════════════════════════════════════════════════
    # 音频处理
    # ═══════════════════════════════════════════════════════════════════════════════

    def _on_audio_data(self, data: bytes, session_id: str):
        """音频数据回调 - 支持 ARMING 状态缓存"""
        # 会话不匹配，丢弃
        if self._ctx.session_id != session_id:
            return

        state = self._ctx.state

        # ARMING 或 RECORDING 状态都接收音频
        if state == State.ARMING or state == State.RECORDING:
            self._audio_queue.put(data, session_id)

            # 在 RECORDING 状态下检测音频能量，有声音时更新时间戳
            if state == State.RECORDING and len(data) >= 2 and len(data) % 2 == 0:
                try:
                    # 将 PCM bytes 转换为 int16 数组计算能量
                    audio_array = np.frombuffer(data, dtype=np.int16)
                    # 使用均方根 (RMS) 作为能量指标
                    rms = np.sqrt(np.mean(audio_array.astype(np.float32) ** 2))
                    if rms > config.SILENCE_THRESHOLD:
                        # 检测到声音，更新最后说话时间
                        self._last_voice_time = time.monotonic()
                except Exception as e:
                    logger.debug(f"音频能量检测异常: {e}")
        # 其他状态（IDLE, STOPPING, ERROR）丢弃

    def _send_audio_to_asr(self, pcm: bytes) -> bool:
        """发送音频到 ASR"""
        with self._resource_lock:
            asr_client = self._asr_client
        if not asr_client:
            return False
        try:
            success = asr_client.send_audio(pcm)
            if not success:
                # 连接已断开，触发错误事件
                logger.warning("发送音频失败: 连接已断开")
                self.post_event(Event(EventType.E_TRANSPORT_ERROR, detail="连接已断开"))
            return success
        except Exception as e:
            logger.error(f"发送音频失败: {e}")
            self.post_event(Event(EventType.E_TRANSPORT_ERROR, detail=str(e)))
            return False

    def _on_recorder_error(self, error: str):
        """录音错误回调"""
        logger.error(f"录音错误: {error}")
        self.post_event(Event(
            EventType.E_AUDIO_DEVICE_GONE,
            detail=friendly_error_message(error)
        ))

    # ═══════════════════════════════════════════════════════════════════════════════
    # ASR 处理
    # ═══════════════════════════════════════════════════════════════════════════════

    def _on_asr_result(self, text: str, is_definite: bool, session_id: str):
        """ASR 结果回调"""
        if self._ctx.session_id != session_id:
            return

        with self._ctx_lock:
            if is_definite:
                self._ctx.committed_text += text
                self._ctx.current_text = ""
            else:
                self._ctx.current_text = text

            display_text = self._ctx.committed_text + self._ctx.current_text

        if self.callbacks.on_text_update:
            self._run_on_main_thread(
                lambda: self.callbacks.on_text_update(display_text, is_definite)
            )

        # 如果在 STOPPING 状态，收到 definitive 结果后可以立即完成
        if self._ctx.state == State.STOPPING and is_definite:
            self.post_event(Event(EventType.S_QUEUE_FLUSHED))

    def _on_asr_error(self, error: str):
        """ASR 错误回调"""
        logger.error(f"ASR 错误: {error}")
        self.post_event(Event(
            EventType.E_TRANSPORT_ERROR,
            detail=friendly_error_message(error)
        ))

    # ═══════════════════════════════════════════════════════════════════════════════
    # 计时器
    # ═══════════════════════════════════════════════════════════════════════════════

    def _arm_flush_timeout(self, timeout_s: float):
        """设置 Flush 超时"""
        self._cancel_timer(self._flush_timer)

        def on_timeout():
            self.post_event(Event(EventType.S_FLUSH_TIMEOUT))

        self._flush_timer = threading.Timer(timeout_s, on_timeout)
        self._flush_timer.daemon = True
        self._flush_timer.start()

    def _arm_silence_timeout(self, timeout_s: float):
        """启动静音检测（基于时间戳方案，定期检查是否超时）"""
        self._cancel_timer(self._silence_check_timer)

        # 绑定当前会话，防止误停新会话
        captured_session = self._ctx.session_id
        self._silence_check_session = captured_session

        # 初始化最后说话时间
        self._last_voice_time = time.monotonic()

        def check_silence():
            # 会话已变化，不处理
            if self._silence_check_session != captured_session:
                return

            # 检查是否仍在 RECORDING 状态
            if self._ctx.state != State.RECORDING:
                return

            now = time.monotonic()
            silent_duration = now - self._last_voice_time

            if silent_duration >= timeout_s:
                # 静音超时，触发停止
                logger.warning(f"静音超时 ({silent_duration:.1f}s >= {timeout_s}s)，自动停止")
                self.post_event(Event(EventType.U_STOP, session_id=captured_session))
            else:
                # 未超时，继续定期检查（每秒检查一次）
                self._silence_check_timer = threading.Timer(1.0, check_silence)
                self._silence_check_timer.daemon = True
                self._silence_check_timer.start()

        # 启动定期检查（1秒后开始第一次检查）
        self._silence_check_timer = threading.Timer(1.0, check_silence)
        self._silence_check_timer.daemon = True
        self._silence_check_timer.start()

    def _reset_idle_timer(self):
        """重置空闲检测计时器"""
        self._cancel_timer(self._idle_timer)

        def on_idle():
            # 在锁保护下读取状态
            with self._ctx_lock:
                is_idle = self._ctx.state == State.IDLE
            if is_idle:
                # 空闲状态下释放可能残留的资源
                self._release_resources()
            self._reset_idle_timer()

        self._idle_timer = threading.Timer(60.0, on_idle)
        self._idle_timer.daemon = True
        self._idle_timer.start()

    def _arm_arming_timeout(self, timeout_s: float):
        """设置 ARMING 超时（初始化太慢）"""
        self._cancel_timer(self._arming_timeout_timer)

        def on_timeout():
            logger.warning(f"ARMING 超时 ({timeout_s}s)")
            self.post_event(Event(EventType.E_ARMING_TIMEOUT))

        self._arming_timeout_timer = threading.Timer(timeout_s, on_timeout)
        self._arming_timeout_timer.daemon = True
        self._arming_timeout_timer.start()

    def _arm_error_recover(self, timeout_s: float):
        """设置 ERROR 状态自动恢复"""
        self._cancel_timer(self._error_recover_timer)

        def on_timeout():
            logger.info(f"ERROR 状态自动恢复 ({timeout_s}s 后)")
            self.post_event(Event(EventType.S_AUTO_RECOVER))

        self._error_recover_timer = threading.Timer(timeout_s, on_timeout)
        self._error_recover_timer.daemon = True
        self._error_recover_timer.start()

    def _cancel_timer(self, timer: Optional[threading.Timer]):
        """安全取消计时器"""
        if timer:
            try:
                timer.cancel()
            except Exception:
                pass
