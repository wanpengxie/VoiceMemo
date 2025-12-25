"""
状态机模块 - 事件驱动的录音状态管理

设计原则：
1. 单写者 - 只有 Coordinator 能改状态
2. 纯函数式 - (state, event) → (newState, effects)
3. 会话隔离 - 每次录音生成 session_id
"""

from enum import Enum, auto
from dataclasses import dataclass, field
from typing import List, Optional, Any, Callable
import uuid
import logging

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# 状态定义
# ═══════════════════════════════════════════════════════════════════════════════

class State(Enum):
    """录音状态"""
    IDLE = auto()       # 未录音，资源未占用
    ARMING = auto()     # 初始化中（权限/设备/连接）
    RECORDING = auto()  # 采集 + 发送中
    STOPPING = auto()   # 停止中，flush 队列
    ERROR = auto()      # 错误状态


# ═══════════════════════════════════════════════════════════════════════════════
# 事件定义
# ═══════════════════════════════════════════════════════════════════════════════

class EventType(Enum):
    """事件类型"""
    # 用户事件 (U_*)
    U_START = auto()        # 用户按下 Option 键
    U_STOP = auto()         # 用户松开 Option 键
    U_QUIT = auto()         # 退出应用

    # 系统事件 (S_*)
    S_MIC_PERMISSION_OK = auto()        # 麦克风权限已授权
    S_AUDIO_READY = auto()              # 音频设备就绪
    S_TRANSPORT_CONNECTED = auto()      # WebSocket 连接成功
    S_TRANSPORT_DISCONNECTED = auto()   # 连接断开
    S_DEFAULT_INPUT_CHANGED = auto()    # 默认输入设备变化
    S_SYSTEM_WILL_SLEEP = auto()        # 系统即将休眠
    S_SYSTEM_DID_WAKE = auto()          # 系统已唤醒
    S_QUEUE_FLUSHED = auto()            # 队列已清空
    S_FLUSH_TIMEOUT = auto()            # Flush 超时
    S_AUTO_RECOVER = auto()             # 自动恢复（ERROR 状态超时后）

    # 错误事件 (E_*)
    E_MIC_PERMISSION_DENIED = auto()    # 麦克风权限被拒绝
    E_ACCESSIBILITY_DENIED = auto()     # 辅助功能权限被拒绝
    E_AUDIO_DEVICE_GONE = auto()        # 音频设备消失
    E_AUDIO_INIT_FAILED = auto()        # 音频初始化失败
    E_TRANSPORT_ERROR = auto()          # 网络传输错误
    E_NETWORK_UNAVAILABLE = auto()      # 网络不可用
    E_ARMING_TIMEOUT = auto()           # ARMING 超时（初始化太慢）


@dataclass
class Event:
    """事件"""
    type: EventType
    session_id: Optional[str] = None  # 关联的会话 ID
    detail: Optional[str] = None      # 额外信息
    data: Any = None                  # 附加数据

    def __str__(self) -> str:
        s = f"Event({self.type.name}"
        if self.session_id:
            s += f", session={self.session_id[:8]}"
        if self.detail:
            s += f", detail={self.detail}"
        return s + ")"


# ═══════════════════════════════════════════════════════════════════════════════
# 副作用定义
# ═══════════════════════════════════════════════════════════════════════════════

class EffectType(Enum):
    """副作用类型"""
    # 资源管理
    CHECK_PERMISSIONS = auto()      # 检查权限
    INIT_AUDIO = auto()             # 初始化音频设备
    CONNECT_TRANSPORT = auto()      # 建立连接
    START_CAPTURE = auto()          # 开始采集
    STOP_CAPTURE = auto()           # 停止采集
    CLOSE_TRANSPORT = auto()        # 关闭连接
    RELEASE_RESOURCES = auto()      # 释放所有资源
    FLUSH_QUEUE = auto()            # Flush 队列

    # UI 更新
    UPDATE_UI = auto()              # 更新 UI 状态
    SHOW_ERROR = auto()             # 显示错误提示
    SHOW_STATUS = auto()            # 显示状态提示

    # 定时器
    ARM_FLUSH_TIMEOUT = auto()      # 设置 Flush 超时
    CANCEL_FLUSH_TIMEOUT = auto()   # 取消 Flush 超时
    ARM_ARMING_TIMEOUT = auto()     # 设置 ARMING 超时
    CANCEL_ARMING_TIMEOUT = auto()  # 取消 ARMING 超时
    ARM_ERROR_RECOVER = auto()      # 设置 ERROR 自动恢复
    CANCEL_ERROR_RECOVER = auto()   # 取消 ERROR 自动恢复

    # 输出
    COMMIT_TEXT = auto()            # 提交识别结果


@dataclass
class Effect:
    """副作用"""
    type: EffectType
    data: Any = None

    def __str__(self) -> str:
        return f"Effect({self.type.name})"


# ═══════════════════════════════════════════════════════════════════════════════
# ARMING 状态汇合逻辑
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class ArmingState:
    """ARMING 状态的子状态 - 等待所有条件就绪"""
    perm_ok: bool = False          # 权限已检查
    audio_ready: bool = False      # 音频设备就绪
    transport_ready: bool = False  # 连接就绪
    started: bool = False          # 是否已启动（防止重复）

    def check_ready(self) -> bool:
        """检查是否所有条件都就绪"""
        if self.started:
            return False
        if self.perm_ok and self.audio_ready and self.transport_ready:
            self.started = True
            return True
        return False

    def reset(self):
        """重置状态"""
        self.perm_ok = False
        self.audio_ready = False
        self.transport_ready = False
        self.started = False


# ═══════════════════════════════════════════════════════════════════════════════
# 状态机上下文
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class StateContext:
    """状态机上下文 - 保存运行时信息"""
    state: State = State.IDLE
    session_id: Optional[str] = None
    arming: ArmingState = field(default_factory=ArmingState)
    error_message: Optional[str] = None
    current_text: str = ""        # 当前临时识别文本
    committed_text: str = ""      # 已确认的识别文本

    def new_session(self) -> str:
        """创建新会话"""
        self.session_id = str(uuid.uuid4())
        self.arming.reset()
        self.current_text = ""
        self.committed_text = ""
        self.error_message = None
        return self.session_id


# ═══════════════════════════════════════════════════════════════════════════════
# 状态机
# ═══════════════════════════════════════════════════════════════════════════════

class StateMachine:
    """
    纯函数式状态机

    特点：
    1. handle() 是纯函数，不产生副作用
    2. 返回新状态和需要执行的副作用列表
    3. 调用者负责执行副作用
    """

    @staticmethod
    def handle(ctx: StateContext, event: Event) -> tuple[StateContext, List[Effect]]:
        """
        处理事件

        Args:
            ctx: 当前状态上下文
            event: 输入事件

        Returns:
            (新状态上下文, 副作用列表)
        """
        # 会话隔离：如果事件带有 session_id，检查是否匹配
        if event.session_id and ctx.session_id and event.session_id != ctx.session_id:
            logger.debug(f"忽略过期事件: {event} (当前 session: {ctx.session_id[:8]})")
            return ctx, []

        state = ctx.state
        etype = event.type
        effects: List[Effect] = []

        # ═══════════════════════════════════════════════════════════════
        # IDLE 状态
        # ═══════════════════════════════════════════════════════════════
        if state == State.IDLE:
            if etype == EventType.U_START:
                # 开始录音 → ARMING
                session_id = ctx.new_session()
                ctx.state = State.ARMING
                logger.info(f"IDLE → ARMING, 新会话: {session_id[:8]}")
                effects = [
                    Effect(EffectType.UPDATE_UI, "正在初始化..."),
                    Effect(EffectType.ARM_ARMING_TIMEOUT, 5.0),  # 5 秒超时
                    Effect(EffectType.CHECK_PERMISSIONS),
                    Effect(EffectType.INIT_AUDIO),
                    Effect(EffectType.CONNECT_TRANSPORT),
                ]

            elif etype == EventType.U_STOP:
                # 幂等 - 保持 IDLE
                pass

            elif etype == EventType.E_MIC_PERMISSION_DENIED:
                # 提示用户授权
                effects = [Effect(EffectType.SHOW_ERROR, "请在系统设置中授权麦克风权限")]

            elif etype == EventType.E_ACCESSIBILITY_DENIED:
                # 提示用户授权
                effects = [Effect(EffectType.SHOW_ERROR, "请在系统设置中授权辅助功能权限")]

        # ═══════════════════════════════════════════════════════════════
        # ARMING 状态
        # ═══════════════════════════════════════════════════════════════
        elif state == State.ARMING:
            if etype == EventType.U_STOP:
                # 用户取消 → IDLE
                ctx.state = State.IDLE
                ctx.session_id = None
                logger.info("ARMING → IDLE (用户取消)")
                effects = [
                    Effect(EffectType.RELEASE_RESOURCES),
                    Effect(EffectType.UPDATE_UI, "已取消"),
                ]

            elif etype == EventType.S_MIC_PERMISSION_OK:
                ctx.arming.perm_ok = True
                if ctx.arming.check_ready():
                    ctx.state = State.RECORDING
                    logger.info("ARMING → RECORDING")
                    effects = [
                        Effect(EffectType.CANCEL_ARMING_TIMEOUT),
                        Effect(EffectType.START_CAPTURE),
                        Effect(EffectType.UPDATE_UI, "请说话..."),
                    ]

            elif etype == EventType.S_AUDIO_READY:
                ctx.arming.audio_ready = True
                if ctx.arming.check_ready():
                    ctx.state = State.RECORDING
                    logger.info("ARMING → RECORDING")
                    effects = [
                        Effect(EffectType.CANCEL_ARMING_TIMEOUT),
                        Effect(EffectType.START_CAPTURE),
                        Effect(EffectType.UPDATE_UI, "请说话..."),
                    ]

            elif etype == EventType.S_TRANSPORT_CONNECTED:
                ctx.arming.transport_ready = True
                if ctx.arming.check_ready():
                    ctx.state = State.RECORDING
                    logger.info("ARMING → RECORDING")
                    effects = [
                        Effect(EffectType.CANCEL_ARMING_TIMEOUT),
                        Effect(EffectType.START_CAPTURE),
                        Effect(EffectType.UPDATE_UI, "请说话..."),
                    ]

            elif etype == EventType.E_ARMING_TIMEOUT:
                # 初始化超时 → IDLE（允许重试）
                ctx.state = State.IDLE
                ctx.session_id = None
                logger.warning("ARMING → IDLE (初始化超时)")
                effects = [
                    Effect(EffectType.RELEASE_RESOURCES),
                    Effect(EffectType.SHOW_ERROR, "初始化超时，请重试"),
                ]

            elif etype == EventType.E_MIC_PERMISSION_DENIED:
                # 权限被拒 → ERROR
                ctx.state = State.ERROR
                ctx.error_message = "麦克风权限被拒绝"
                logger.info("ARMING → ERROR (权限被拒)")
                effects = [
                    Effect(EffectType.CANCEL_ARMING_TIMEOUT),
                    Effect(EffectType.RELEASE_RESOURCES),
                    Effect(EffectType.SHOW_ERROR, "请在系统设置中授权麦克风权限"),
                    Effect(EffectType.ARM_ERROR_RECOVER, 3.0),  # 3 秒后自动恢复到 IDLE
                ]

            elif etype == EventType.E_AUDIO_INIT_FAILED:
                # 音频初始化失败 → ERROR
                ctx.state = State.ERROR
                ctx.error_message = event.detail or "音频初始化失败"
                logger.info(f"ARMING → ERROR ({ctx.error_message})")
                effects = [
                    Effect(EffectType.CANCEL_ARMING_TIMEOUT),
                    Effect(EffectType.RELEASE_RESOURCES),
                    Effect(EffectType.SHOW_ERROR, ctx.error_message),
                    Effect(EffectType.ARM_ERROR_RECOVER, 3.0),  # 3 秒后自动恢复到 IDLE
                ]

            elif etype == EventType.E_TRANSPORT_ERROR:
                # 连接错误 → IDLE（允许重试）
                ctx.state = State.IDLE
                ctx.session_id = None
                logger.info("ARMING → IDLE (连接错误)")
                effects = [
                    Effect(EffectType.RELEASE_RESOURCES),
                    Effect(EffectType.SHOW_ERROR, event.detail or "连接失败，请重试"),
                ]

            elif etype == EventType.E_NETWORK_UNAVAILABLE:
                # 网络不可用 → IDLE
                ctx.state = State.IDLE
                ctx.session_id = None
                logger.info("ARMING → IDLE (网络不可用)")
                effects = [
                    Effect(EffectType.RELEASE_RESOURCES),
                    Effect(EffectType.SHOW_ERROR, "网络不可用，请检查网络连接"),
                ]

        # ═══════════════════════════════════════════════════════════════
        # RECORDING 状态
        # ═══════════════════════════════════════════════════════════════
        elif state == State.RECORDING:
            if etype == EventType.U_STOP:
                # 用户停止 → STOPPING
                ctx.state = State.STOPPING
                logger.info("RECORDING → STOPPING")
                effects = [
                    Effect(EffectType.STOP_CAPTURE),
                    Effect(EffectType.FLUSH_QUEUE),
                    Effect(EffectType.ARM_FLUSH_TIMEOUT, 1.0),  # 1 秒超时
                    Effect(EffectType.UPDATE_UI, "正在处理..."),
                ]

            elif etype == EventType.S_DEFAULT_INPUT_CHANGED:
                # 设备变化 → 软重启（回到 ARMING）
                old_session = ctx.session_id
                new_session = ctx.new_session()
                ctx.state = State.ARMING
                logger.info(f"RECORDING → ARMING (设备变化), 旧会话: {old_session[:8]}, 新会话: {new_session[:8]}")
                effects = [
                    Effect(EffectType.STOP_CAPTURE),
                    Effect(EffectType.CLOSE_TRANSPORT),
                    Effect(EffectType.UPDATE_UI, "音频设备变化，正在重新连接..."),
                    Effect(EffectType.INIT_AUDIO),
                    Effect(EffectType.CONNECT_TRANSPORT),
                ]

            elif etype == EventType.S_SYSTEM_WILL_SLEEP:
                # 系统休眠 → STOPPING
                ctx.state = State.STOPPING
                logger.info("RECORDING → STOPPING (系统休眠)")
                effects = [
                    Effect(EffectType.STOP_CAPTURE),
                    Effect(EffectType.FLUSH_QUEUE),
                    Effect(EffectType.ARM_FLUSH_TIMEOUT, 0.5),  # 快速 flush
                    Effect(EffectType.UPDATE_UI, "系统休眠，正在保存..."),
                ]

            elif etype == EventType.E_TRANSPORT_ERROR:
                # 传输错误 → STOPPING
                ctx.state = State.STOPPING
                logger.info("RECORDING → STOPPING (传输错误)")
                effects = [
                    Effect(EffectType.STOP_CAPTURE),
                    Effect(EffectType.FLUSH_QUEUE),
                    Effect(EffectType.ARM_FLUSH_TIMEOUT, 0.5),
                    Effect(EffectType.SHOW_ERROR, event.detail or "网络连接中断"),
                ]

            elif etype == EventType.E_AUDIO_DEVICE_GONE:
                # 设备消失 → STOPPING
                ctx.state = State.STOPPING
                logger.info("RECORDING → STOPPING (设备消失)")
                effects = [
                    Effect(EffectType.FLUSH_QUEUE),
                    Effect(EffectType.ARM_FLUSH_TIMEOUT, 0.5),
                    Effect(EffectType.SHOW_ERROR, "音频设备已断开"),
                ]

        # ═══════════════════════════════════════════════════════════════
        # STOPPING 状态
        # ═══════════════════════════════════════════════════════════════
        elif state == State.STOPPING:
            if etype == EventType.S_QUEUE_FLUSHED or etype == EventType.S_FLUSH_TIMEOUT:
                # 队列清空或超时 → IDLE
                ctx.state = State.IDLE
                full_text = ctx.committed_text + ctx.current_text
                logger.info(f"STOPPING → IDLE, 识别结果长度: {len(full_text)}")
                effects = [
                    Effect(EffectType.CANCEL_FLUSH_TIMEOUT),
                    Effect(EffectType.CLOSE_TRANSPORT),
                    Effect(EffectType.RELEASE_RESOURCES),
                    Effect(EffectType.COMMIT_TEXT, full_text),
                    Effect(EffectType.UPDATE_UI, None),  # 隐藏 UI
                ]
                ctx.session_id = None

            elif etype == EventType.U_STOP:
                # 幂等 - 保持 STOPPING
                pass

        # ═══════════════════════════════════════════════════════════════
        # ERROR 状态
        # ═══════════════════════════════════════════════════════════════
        elif state == State.ERROR:
            if etype == EventType.U_START:
                # 重试 → ARMING
                session_id = ctx.new_session()
                ctx.state = State.ARMING
                logger.info(f"ERROR → ARMING (重试), 新会话: {session_id[:8]}")
                effects = [
                    Effect(EffectType.CANCEL_ERROR_RECOVER),
                    Effect(EffectType.RELEASE_RESOURCES),
                    Effect(EffectType.UPDATE_UI, "正在重试..."),
                    Effect(EffectType.ARM_ARMING_TIMEOUT, 5.0),
                    Effect(EffectType.CHECK_PERMISSIONS),
                    Effect(EffectType.INIT_AUDIO),
                    Effect(EffectType.CONNECT_TRANSPORT),
                ]

            elif etype == EventType.S_AUTO_RECOVER:
                # 自动恢复 → IDLE（清理后可以重新开始）
                ctx.state = State.IDLE
                ctx.session_id = None
                ctx.error_message = None
                logger.info("ERROR → IDLE (自动恢复)")
                effects = [
                    Effect(EffectType.RELEASE_RESOURCES),
                    Effect(EffectType.UPDATE_UI, None),
                ]

            elif etype == EventType.S_SYSTEM_DID_WAKE:
                # 系统唤醒 → IDLE（恢复正常状态）
                ctx.state = State.IDLE
                ctx.session_id = None
                ctx.error_message = None
                logger.info("ERROR → IDLE (系统唤醒)")
                effects = [
                    Effect(EffectType.CANCEL_ERROR_RECOVER),
                    Effect(EffectType.RELEASE_RESOURCES),
                    Effect(EffectType.UPDATE_UI, None),
                ]

            elif etype == EventType.U_STOP:
                # 重置 → IDLE
                ctx.state = State.IDLE
                ctx.session_id = None
                ctx.error_message = None
                logger.info("ERROR → IDLE")
                effects = [
                    Effect(EffectType.RELEASE_RESOURCES),
                    Effect(EffectType.UPDATE_UI, None),
                ]

        return ctx, effects
