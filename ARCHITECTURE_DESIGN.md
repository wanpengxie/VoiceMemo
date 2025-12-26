# VoiceFlow 鲁棒性架构设计

## 核心设计原则

1. **单写者状态机** - 只有 Coordinator 能改状态，其他模块只投递事件
2. **会话隔离** - 每次录音生成 session_id，丢弃不匹配的事件
3. **事件驱动** - 状态迁移由事件触发，纯函数式 `(state, event) → (newState, effects)`
4. **音频解耦** - 回调线程只入队，发送线程独立消费

---

## 模块架构

```
┌─────────────────────────────────────────────────────────────┐
│                    RecordingCoordinator                      │
│  ┌─────────────────────────────────────────────────────┐    │
│  │              StateMachine (纯函数)                    │    │
│  │  IDLE → ARMING → RECORDING → STOPPING → IDLE        │    │
│  └─────────────────────────────────────────────────────┘    │
│                           │                                  │
│                     handle(event)                            │
└─────────────────────────────────────────────────────────────┘
        ▲                   │                     ▲
        │ 事件              │ 副作用              │ 事件
        │                   ▼                     │
┌───────┴───────┐   ┌──────────────┐   ┌────────┴────────┐
│ HealthMonitors │   │ AudioPipeline │   │   Transport     │
│ - Permission   │   │ ┌──────────┐  │   │ - WebSocket     │
│ - Device       │   │ │ Capture  │  │   │ - Reconnect     │
│ - Sleep/Wake   │   │ │    ↓     │  │   └─────────────────┘
│ - Network      │   │ │ SPSCQueue│  │
└────────────────┘   │ │    ↓     │  │
                     │ │ Sender   │──┘
                     │ └──────────┘  │
                     └──────────────┘
                            │
                            ▼
                     ┌──────────────┐
                     │   UIModel    │
                     │ (主线程更新)  │
                     └──────────────┘
```

---

## 状态定义

```python
from enum import Enum, auto

class State(Enum):
    IDLE = auto()       # 未录音，资源未占用
    ARMING = auto()     # 初始化中（权限/设备/连接）
    RECORDING = auto()  # 采集 + 发送中
    PAUSED = auto()     # 暂停采集，连接保持
    STOPPING = auto()   # 停止中，flush 队列
    ERROR = auto()      # 错误状态
```

---

## 事件清单

### 用户事件 (U_*)
| 事件 | 说明 |
|------|------|
| `U_START` | 用户按下 Option 键 |
| `U_STOP` | 用户松开 Option 键 |
| `U_TOGGLE` | 开始/停止切换 |
| `U_QUIT` | 退出应用 |

### 系统事件 (S_*)
| 事件 | 来源 | 说明 |
|------|------|------|
| `S_MIC_PERMISSION` | PermissionMonitor | 权限状态变化 |
| `S_DEFAULT_INPUT_CHANGED` | DeviceMonitor | 默认输入设备变化 |
| `S_SYSTEM_WILL_SLEEP` | SleepWakeMonitor | 系统即将休眠 |
| `S_SYSTEM_DID_WAKE` | SleepWakeMonitor | 系统已唤醒 |
| `S_TRANSPORT_CONNECTED` | Transport | WebSocket 连接成功 |
| `S_TRANSPORT_DISCONNECTED` | Transport | 连接断开 |
| `S_AUDIO_STREAM_STARTED` | AudioCapture | 音频流已启动 |
| `S_AUDIO_STREAM_STOPPED` | AudioCapture | 音频流已停止 |

### 错误事件 (E_*)
| 事件 | 说明 |
|------|------|
| `E_MIC_PERMISSION_DENIED` | 麦克风权限被拒绝 |
| `E_AUDIO_DEVICE_GONE` | 音频设备消失 |
| `E_AUDIO_INIT_FAILED` | 音频初始化失败 |
| `E_TRANSPORT_ERROR` | 网络传输错误 |
| `E_SEND_TIMEOUT` | 发送超时 |

---

## 状态迁移表

```
当前状态      + 事件                    → 新状态      + 副作用
─────────────────────────────────────────────────────────────────
IDLE          + U_START                 → ARMING      + 生成session_id; 检查权限; 初始化设备; 建立连接
IDLE          + U_STOP                  → IDLE        + no-op（幂等）
IDLE          + S_MIC_PERMISSION(denied)→ IDLE        + 提示用户授权

ARMING        + (perm_ok && audio_ok && transport_ok) → RECORDING + 开始采集
ARMING        + U_STOP                  → IDLE        + 取消初始化; 释放资源
ARMING        + E_MIC_PERMISSION_DENIED → ERROR       + 清理资源; 提示用户
ARMING        + E_AUDIO_INIT_FAILED     → ERROR       + 清理资源; 提示用户
ARMING        + E_TRANSPORT_ERROR       → IDLE        + 清理资源; 提示用户（允许重试）

RECORDING     + U_STOP                  → STOPPING    + 停止采集; sender进入flush
RECORDING     + S_DEFAULT_INPUT_CHANGED → ARMING      + 软重启（新session）
RECORDING     + S_SYSTEM_WILL_SLEEP     → STOPPING    + 立即停止; 快速flush
RECORDING     + E_TRANSPORT_ERROR       → STOPPING    + 尝试保存本地

STOPPING      + (队列已空 || flush超时) → IDLE        + 关闭连接; 释放资源
STOPPING      + U_STOP                  → STOPPING    + no-op（幂等）

ERROR         + U_START                 → ARMING      + 清理后重试
ERROR         + U_STOP                  → IDLE        + 清理资源
```

### ARMING 汇合逻辑（解决竞态）

```python
class ArmingState:
    perm_ok: bool = False
    audio_ready: bool = False
    transport_ready: bool = False
    started: bool = False  # 防止重复启动

    def check_ready(self) -> bool:
        if self.started:
            return False
        if self.perm_ok and self.audio_ready and self.transport_ready:
            self.started = True
            return True
        return False
```

---

## 音频管道设计

### SPSC 队列（单生产者单消费者）

```python
from collections import deque
from threading import Event

class AudioQueue:
    def __init__(self, max_duration_ms: int = 500, frame_ms: int = 100):
        self.max_frames = max_duration_ms // frame_ms
        self._q = deque(maxlen=self.max_frames)  # 满了自动丢最旧
        self._ev = Event()
        self._closed = False
        self._dropped_count = 0

    def put(self, frame: bytes, session_id: str) -> None:
        """音频回调线程调用 - 必须非阻塞"""
        if self._closed:
            return
        # deque(maxlen=N) 满了会自动丢弃最左边的
        if len(self._q) >= self.max_frames:
            self._dropped_count += 1
        self._q.append((session_id, frame))
        self._ev.set()

    def get_batch(self, max_items: int = 10, timeout_s: float = 0.05):
        """发送线程调用"""
        if not self._q:
            self._ev.wait(timeout_s)
        items = []
        while self._q and len(items) < max_items:
            items.append(self._q.popleft())
        if not self._q:
            self._ev.clear()
        return items

    def close(self):
        self._closed = True
        self._ev.set()
```

### 背压策略

| 策略 | 触发条件 | 行为 |
|------|----------|------|
| **硬上限丢弃** | 队列 > 500ms | 丢弃最旧数据，保持低延迟 |
| **断线缓存** | 连接断开 | 最多缓存 500ms，超过丢弃 |
| **Flush 超时** | STOPPING 状态 | 最多等 1s，超时丢弃剩余 |

### 发送线程

```python
def sender_loop(coordinator, queue, transport):
    while not coordinator.is_stopping:
        items = queue.get_batch(max_items=10, timeout_s=0.05)
        if not items:
            continue

        # 过滤旧 session 的数据
        current_session = coordinator.session_id
        items = [(sid, data) for sid, data in items if sid == current_session]
        if not items:
            continue

        # 合并发送
        pcm = b"".join(data for _, data in items)
        try:
            transport.send_audio(pcm, session_id=current_session)
        except Exception as e:
            coordinator.post_event(Event("E_TRANSPORT_ERROR", detail=str(e)))
```

---

## Monitors 实现

### PermissionMonitor

```python
from AVFoundation import AVCaptureDevice, AVMediaTypeAudio

class PermissionMonitor:
    def check(self) -> str:
        status = AVCaptureDevice.authorizationStatusForMediaType_(AVMediaTypeAudio)
        return {0: 'not_determined', 1: 'restricted', 2: 'denied', 3: 'authorized'}.get(status, 'unknown')

    def request(self, callback):
        AVCaptureDevice.requestAccessForMediaType_completionHandler_(AVMediaTypeAudio, callback)
```

### DeviceMonitor

```python
from Foundation import NSDistributedNotificationCenter

class DeviceMonitor:
    def start(self, on_change):
        center = NSDistributedNotificationCenter.defaultCenter()
        center.addObserver_selector_name_object_(
            self, 'deviceChanged:',
            'com.apple.audio.defaultInputDeviceChanged', None
        )
```

### SleepWakeMonitor

```python
from AppKit import NSWorkspace

class SleepWakeMonitor:
    def start(self, on_sleep, on_wake):
        nc = NSWorkspace.sharedWorkspace().notificationCenter()
        nc.addObserver_selector_name_object_(self, 'willSleep:', 'NSWorkspaceWillSleepNotification', None)
        nc.addObserver_selector_name_object_(self, 'didWake:', 'NSWorkspaceDidWakeNotification', None)
```

---

## 错误处理策略

| 错误类型 | 策略 | 用户提示 |
|----------|------|----------|
| 权限被拒 | 停止，不重试 | 引导用户去系统设置 |
| 设备消失 | 尝试切换默认设备 | "音频设备已断开" |
| 网络超时 | 重试 3 次，间隔递增 | "网络连接中..." |
| 服务端错误 | 解析错误码，给出建议 | "API 密钥无效" 等 |
| 未知错误 | 记录日志，回到 IDLE | "发生错误，请重试" |

---

## 实现优先级

### Phase 1: 核心重构
1. `RecordingCoordinator` + `StateMachine`
2. `AudioQueue` (SPSC)
3. 发送线程分离

### Phase 2: Monitors
4. `PermissionMonitor`
5. `DeviceMonitor`
6. `SleepWakeMonitor`

### Phase 3: 完善
7. 错误信息友好化
8. 日志和指标
9. 单实例检测
