# VoiceMemo 鲁棒性分析报告

## 问题汇总

### P0 - 致命问题（应用无法使用）

#### 1. 辅助功能权限未检测
- **现状**：`pynput.keyboard.Listener` 需要辅助功能权限，如果用户拒绝，按键监听静默失败
- **表现**：按 Option 键完全没有反应，用户不知道原因
- **修复**：启动时检测辅助功能权限，未授权则弹窗引导用户到系统设置

#### 2. 麦克风权限未检测
- **现状**：只在 `setup.py` 声明了权限，但没有代码层面的检测
- **表现**：录音时才报错，错误信息不明确
- **修复**：启动时主动检测麦克风权限状态，未授权则提示

---

### P1 - 严重问题（功能失败无反馈）

#### 3. 设备热插拔处理不完善
- **现状**：录音中插拔耳机，`sounddevice` 行为不确定
- **表现**：可能崩溃、可能静默失败、已录音频丢失
- **修复**：监听系统音频设备变化通知，主动停止并提示用户

#### 4. 系统休眠/唤醒状态异常
- **现状**：休眠时如果正在录音，唤醒后状态可能不一致
- **表现**：应用"卡住"，需要强制退出
- **修复**：监听系统休眠/唤醒通知，自动重置状态

#### 5. 启动失败后状态未同步重置
- **现状**：`_start_recording` 设置 `is_recording=True`，但后台线程失败时用 `callAfter` 重置
- **表现**：快速重试时被阻塞
- **修复**：在后台线程中直接重置关键状态，或使用更可靠的状态机

---

### P2 - 重要问题（影响用户体验）

#### 6. 网络连接超时过长
- **现状**：WebSocket 连接超时 5 秒
- **表现**：无网络时用户等 5 秒才知道失败
- **修复**：
  - 先做网络可达性检查（<0.5秒）
  - 缩短连接超时到 3 秒
  - 显示"正在连接..."状态

#### 7. 录音中断网数据丢失
- **现状**：WebSocket 断开后，已录音频全部丢失
- **表现**：用户说了半天话，结果什么都没有
- **修复**：
  - 本地缓存音频数据
  - 断网时尝试重连
  - 重连失败则保存音频到历史（标记为"未识别"）

#### 8. 错误信息不友好
- **现状**：很多错误信息是技术性的（如 "PortAudio -9986"）
- **表现**：用户看不懂
- **修复**：统一错误信息翻译层，给用户友好的提示和建议操作

#### 9. API 配置错误反馈不清晰
- **现状**：API Key 错误时，服务端返回的错误码需要解读
- **表现**：用户不知道是密钥错了还是网络问题
- **修复**：解析常见错误码，给出明确提示（如"API 密钥无效"）

---

### P3 - 一般问题（边缘情况）

#### 10. 多实例冲突
- **现状**：可以同时运行多个 VoiceMemo 实例
- **表现**：按键监听冲突、麦克风争用
- **修复**：使用 PID 文件或 NSDistributedNotificationCenter 检测

#### 11. 采样率不匹配
- **现状**：固定使用 16kHz，某些设备可能不支持
- **表现**：录音失败
- **修复**：查询设备支持的采样率，必要时重采样

#### 12. 某些应用不支持粘贴
- **现状**：使用 Cmd+V 模拟粘贴
- **表现**：在终端等应用中可能无效或行为异常
- **修复**：（较难完美解决）检测焦点应用，对特定应用使用不同策略

#### 13. 系统音频服务重启 (coreaudiod)
- **现状**：无处理
- **表现**：PortAudio 失效
- **修复**：捕获相关错误时，尝试重新初始化 PortAudio

---

## 修复优先级建议

### 第一阶段：核心问题
1. ✅ 辅助功能权限检测
2. ✅ 麦克风权限检测
3. ✅ 状态机重构（解决竞态条件）

### 第二阶段：稳定性
4. ✅ 设备热插拔处理
5. ✅ 休眠唤醒处理
6. ✅ 网络状态检测

### 第三阶段：体验优化
7. ✅ 错误信息友好化
8. ✅ 断网数据保护
9. ✅ 多实例检测

---

## 技术实现要点

### 权限检测 (macOS)

```python
# 麦克风权限检测
from AVFoundation import AVCaptureDevice, AVMediaTypeAudio

def check_microphone_permission() -> str:
    """返回: 'authorized', 'denied', 'not_determined'"""
    status = AVCaptureDevice.authorizationStatusForMediaType_(AVMediaTypeAudio)
    if status == 0:  # AVAuthorizationStatusNotDetermined
        return 'not_determined'
    elif status == 3:  # AVAuthorizationStatusAuthorized
        return 'authorized'
    else:
        return 'denied'

# 辅助功能权限检测
from ApplicationServices import AXIsProcessTrusted

def check_accessibility_permission() -> bool:
    return AXIsProcessTrusted()
```

### 设备变化监听

```python
from Foundation import NSDistributedNotificationCenter

def setup_audio_device_listener():
    center = NSDistributedNotificationCenter.defaultCenter()
    center.addObserver_selector_name_object_(
        self,
        'audioDeviceChanged:',
        'com.apple.audio.defaultInputDeviceChanged',
        None
    )
```

### 休眠唤醒监听

```python
from Cocoa import NSWorkspace

def setup_sleep_wake_listener():
    nc = NSWorkspace.sharedWorkspace().notificationCenter()
    nc.addObserver_selector_name_object_(
        self,
        'willSleep:',
        'NSWorkspaceWillSleepNotification',
        None
    )
    nc.addObserver_selector_name_object_(
        self,
        'didWake:',
        'NSWorkspaceDidWakeNotification',
        None
    )
```

### 网络可达性检查

```python
import socket

def check_network_reachable(host: str = "openspeech.bytedance.com", timeout: float = 0.5) -> bool:
    try:
        socket.create_connection((host, 443), timeout=timeout)
        return True
    except (socket.timeout, socket.error):
        return False
```

---

## 状态机设计建议

当前代码使用 `is_recording` 布尔值管理状态，容易出现竞态。建议改为状态机：

```
┌──────────┐     按 Option     ┌───────────┐    录音成功    ┌──────────┐
│   IDLE   │ ───────────────▶  │ STARTING  │ ────────────▶  │ RECORDING│
└──────────┘                   └───────────┘                └──────────┘
     ▲                              │                            │
     │                              │ 录音失败                    │ 松开 Option
     │                              ▼                            ▼
     │                         ┌───────────┐                ┌──────────┐
     └──────────────────────── │   ERROR   │ ◀──────────── │ STOPPING │
                               └───────────┘    等待结果    └──────────┘
```

每个状态只能转换到特定状态，避免竞态条件。
