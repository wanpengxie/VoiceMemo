"""
豆包语音识别配置
优先从 GUI 设置读取，开发时兼容 .env 文件
"""

import os
from pathlib import Path


def _load_env_file():
    """加载 .env 文件（仅开发时使用）"""
    env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        with open(env_path, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def _get_config_value(settings_value: str, env_key: str, default: str = "") -> str:
    """
    获取配置值
    优先级：GUI设置 > 环境变量 > 默认值
    """
    if settings_value:
        return settings_value
    return os.environ.get(env_key, default)


# 开发时加载 .env
_load_env_file()

# WebSocket 地址（双向流式优化版）
WSS_URL = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async"

# 音频配置
AUDIO_FORMAT = "pcm"
AUDIO_RATE = 16000
AUDIO_BITS = 16
AUDIO_CHANNEL = 1
CHUNK_DURATION_MS = 100

# 静音检测配置
SILENCE_THRESHOLD = 500       # 静音阈值（RMS 能量值，低于此值视为静音）
SILENCE_TIMEOUT_S = 60.0      # 静音超时时间（秒），持续静音超过此时间自动停止


def get_app_key() -> str:
    """获取 App Key"""
    from .settings import settings
    return _get_config_value(settings.app_key, "DOUBAO_APP_KEY")


def get_access_key() -> str:
    """获取 Access Key"""
    from .settings import settings
    return _get_config_value(settings.access_key, "DOUBAO_ACCESS_KEY")


def get_resource_id() -> str:
    """获取 Resource ID"""
    from .settings import settings
    return _get_config_value(
        settings.resource_id,
        "DOUBAO_RESOURCE_ID",
        "volc.bigasr.sauc.duration"
    )


# 兼容旧代码的属性访问
@property
def _app_key():
    return get_app_key()


@property
def _access_key():
    return get_access_key()


@property
def _resource_id():
    return get_resource_id()


# 模块级别的动态属性（通过函数访问）
APP_KEY = property(lambda self: get_app_key())
ACCESS_KEY = property(lambda self: get_access_key())
RESOURCE_ID = property(lambda self: get_resource_id())


def validate_config() -> tuple[bool, str]:
    """
    验证配置是否完整
    Returns:
        (是否有效, 错误信息)
    """
    if not get_app_key():
        return False, "缺少 App Key，请在设置中配置"
    if not get_access_key():
        return False, "缺少 Access Key，请在设置中配置"
    return True, ""
