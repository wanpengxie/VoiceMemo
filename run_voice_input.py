#!/usr/bin/env python3
"""
语音输入法 - 菜单栏应用入口
"""
import sys
import os

# 确保模块路径正确
if getattr(sys, 'frozen', False):
    # 打包后运行
    base_path = os.path.dirname(sys.executable)
else:
    # 开发模式
    base_path = os.path.dirname(os.path.abspath(__file__))

sys.path.insert(0, base_path)

from voice_input.menubar_app import main

if __name__ == "__main__":
    main()
