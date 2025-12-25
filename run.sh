#!/bin/bash

# 语音输入法启动脚本

cd "$(dirname "$0")"

# 检查 Python
if ! command -v python3 &> /dev/null; then
    echo "错误: 需要 Python3"
    exit 1
fi

# 检查依赖
python3 -c "import sounddevice" 2>/dev/null || {
    echo "安装依赖..."
    pip3 install -r requirements.txt
}

# 启动应用
python3 -m voice_input.main
