# VoiceMemo

macOS 语音输入助手，按住 Option 键说话，松开自动输入文字。基于豆包（火山引擎）语音识别 API。

## 功能特性

- 按住 Option 键开始录音，松开自动识别并输入
- 菜单栏常驻，不占用 Dock 位置
- 实时显示识别结果
- 支持历史记录查看
- 支持自定义快捷键（开发中）

## 系统要求

- macOS 10.15 (Catalina) 或更高版本
- Python 3.10+
- 火山引擎账号（用于语音识别 API）

## 快速开始

### 1. 克隆项目

```bash
git clone git@github.com:wanpengxie/VoiceMemo.git
cd VoiceMemo
```

### 2. 安装依赖

```bash
# 推荐使用虚拟环境
python3 -m venv venv
source venv/bin/activate

# 安装依赖
pip install -r requirements.txt
```

### 3. 配置 API 密钥

```bash
# 复制配置模板
cp .env.example .env

# 编辑 .env 文件，填入你的火山引擎密钥
```

**.env 文件内容：**
```bash
# 火山引擎 APP ID
DOUBAO_APP_KEY=your_app_key_here

# 火山引擎 Access Token
DOUBAO_ACCESS_KEY=your_access_key_here

# 资源ID（可选）
# DOUBAO_RESOURCE_ID=volc.bigasr.sauc.duration
```

> 获取密钥：登录 [火山引擎控制台](https://console.volcengine.com/) → 语音技术 → 语音识别

### 4. 运行

```bash
# 开发模式运行
python run_voice_input.py
```

## 打包为 App

### 打包命令

```bash
# 确保在虚拟环境中
source venv/bin/activate

# 清理旧的打包文件
rm -rf build dist

# 打包
python setup.py py2app
```

打包完成后，应用位于 `dist/VoiceMemo.app`

### 安装应用

```bash
# 复制到应用程序目录
cp -r dist/VoiceMemo.app /Applications/
```

## macOS 权限设置

VoiceMemo 需要以下系统权限才能正常工作：

### 1. 麦克风权限（必需）

用于录制语音。

**设置路径：**
```
系统设置 → 隐私与安全性 → 麦克风
```

首次运行时系统会自动弹窗请求权限，点击「允许」即可。

如果之前拒绝过，需要手动开启：
1. 打开「系统设置」
2. 点击「隐私与安全性」
3. 点击「麦克风」
4. 找到 VoiceMemo（或终端/IDE，如果是开发模式）
5. 开启开关

### 2. 辅助功能权限（必需）

用于监听全局快捷键（Option 键）和模拟键盘输入。

**设置路径：**
```
系统设置 → 隐私与安全性 → 辅助功能
```

**设置步骤：**
1. 打开「系统设置」
2. 点击「隐私与安全性」
3. 点击「辅助功能」
4. 点击左下角的锁图标，输入密码解锁
5. 点击「+」按钮
6. 选择 `VoiceMemo.app`（或开发模式下的终端/IDE）
7. 确保开关已开启

> **注意：** 如果修改权限后仍不生效，尝试：
> - 完全退出应用后重新打开
> - 从辅助功能列表中删除应用，再重新添加
> - 重启电脑

### 3. 自动化权限（可选）

用于将文字输入到其他应用。

**设置路径：**
```
系统设置 → 隐私与安全性 → 自动化
```

首次向其他应用输入文字时，系统会自动请求此权限。

## 权限问题排查

| 症状 | 可能原因 | 解决方案 |
|------|----------|----------|
| 按 Option 键无反应 | 缺少辅助功能权限 | 添加到「辅助功能」列表 |
| 提示「麦克风访问被拒绝」 | 缺少麦克风权限 | 在「麦克风」中开启权限 |
| 录音正常但文字无法输入 | 缺少辅助功能权限 | 添加到「辅助功能」列表 |
| 权限已开启但仍不工作 | 权限缓存问题 | 删除后重新添加，或重启电脑 |

### 开发模式权限

在开发模式下运行时，需要给 **终端** 或 **IDE** 授权：

- 使用 Terminal.app 运行：给「终端」授权
- 使用 iTerm2 运行：给「iTerm」授权
- 使用 VS Code 运行：给「Visual Studio Code」授权
- 使用 PyCharm 运行：给「PyCharm」授权

## 使用说明

1. 启动应用后，菜单栏会出现一个图标
2. 点击菜单栏图标可以：
   - 查看应用状态
   - 打开设置
   - 查看历史记录
   - 退出应用
3. **录音方式：** 按住 Option 键说话，松开后自动识别并输入到当前光标位置

## 数据存储位置

| 数据类型 | 存储路径 |
|----------|----------|
| 日志文件 | `~/Library/Logs/VoiceMemo/` |
| 历史记录 | `~/Library/Application Support/VoiceMemo/` |
| 用户设置 | macOS 系统偏好设置 (NSUserDefaults) |

## 常见问题

### Q: 打包时报错找不到 PortAudio？

确保已安装 portaudio：
```bash
brew install portaudio
pip install sounddevice --force-reinstall
```

### Q: 打包后的应用无法打开？

macOS 可能阻止未签名的应用：
1. 右键点击应用 → 选择「打开」
2. 或者：系统设置 → 隐私与安全性 → 点击「仍要打开」

### Q: 识别结果不准确？

- 确保麦克风工作正常
- 尽量在安静环境下使用
- 说话时保持适当距离

### Q: 如何查看日志？

```bash
# 查看应用日志
cat ~/Library/Logs/VoiceMemo/app.log

# 或通过菜单栏图标 → 查看日志
```

## 开发

### 项目结构

```
VoiceMemo/
├── voice_input/          # 核心模块
│   ├── menubar_app.py    # 菜单栏应用主入口
│   ├── asr_client.py     # 语音识别客户端
│   ├── audio_recorder.py # 音频录制
│   ├── coordinator.py    # 状态协调器
│   ├── state_machine.py  # 状态机
│   ├── system_utils.py   # 系统工具（权限检测等）
│   └── ...
├── run_voice_input.py    # 启动脚本
├── setup.py              # 打包配置
├── requirements.txt      # 依赖列表
└── README.md
```

### 本地开发

```bash
# 安装开发依赖
pip install -r requirements.txt

# 运行
python run_voice_input.py
```

## License

MIT
