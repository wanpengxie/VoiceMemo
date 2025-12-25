"""
py2app 打包配置
使用方法:
    python setup.py py2app
"""
import sys
from setuptools import setup

# 找到 PortAudio 库路径
import sounddevice
portaudio_lib = sounddevice._libname

APP = ['run_voice_input.py']
DATA_FILES = []

OPTIONS = {
    'argv_emulation': False,  # 禁用 argv 模拟，避免问题
    'plist': {
        'CFBundleName': 'VoiceInput',
        'CFBundleDisplayName': '语音输入法',
        'CFBundleIdentifier': 'com.doubao.voiceinput',
        'CFBundleVersion': '1.0.0',
        'CFBundleShortVersionString': '1.0',
        'LSUIElement': True,  # 菜单栏应用，不显示 Dock 图标
        'NSMicrophoneUsageDescription': '语音输入需要使用麦克风进行录音',
        'NSAppleEventsUsageDescription': '语音输入需要控制其他应用来输入文字',
        'NSAccessibilityUsageDescription': '语音输入需要辅助功能权限来模拟键盘输入',
    },
    'frameworks': [portaudio_lib],  # 包含 PortAudio 动态库
    'packages': [
        'voice_input',
        'websocket',
        'sounddevice',
        '_sounddevice_data',  # PortAudio 数据
        'numpy',
        'pynput',
    ],
    'includes': [
        'voice_input.config',
        'voice_input.settings',
        'voice_input.settings_window',
        'voice_input.asr_client',
        'voice_input.audio_recorder',
        'voice_input.ui',
        'voice_input.main',
        'voice_input.menubar_app',
    ],
    'excludes': [
        'tkinter',  # 不需要 tkinter
        'matplotlib',
        'scipy',
        'pandas',
    ],
    'iconfile': 'VoiceInput.icns',
}

setup(
    app=APP,
    name='VoiceInput',
    data_files=DATA_FILES,
    options={'py2app': OPTIONS},
    setup_requires=['py2app'],
)
