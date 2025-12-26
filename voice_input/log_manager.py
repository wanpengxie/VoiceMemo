"""
日志管理模块
- 统一日志配置
- 日志保存到 ~/Library/Logs/VoiceMemo/
"""

import logging
import os
from datetime import datetime
from pathlib import Path


# 应用名称
APP_NAME = "VoiceMemo"

# 日志目录 (macOS 标准应用日志目录)
LOG_DIR = Path.home() / "Library" / "Logs" / APP_NAME

# 日志文件路径
LOG_FILE = LOG_DIR / "app.log"

# 确保日志目录存在
LOG_DIR.mkdir(parents=True, exist_ok=True)


def setup_logging(level: int = logging.INFO) -> None:
    """
    配置全局日志
    - 输出到控制台
    - 同时保存到文件
    """
    # 日志格式
    formatter = logging.Formatter(
        '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # 根日志器
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # 清除已有的处理器（避免重复）
    root_logger.handlers.clear()

    # 控制台处理器
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # 文件处理器
    file_handler = logging.FileHandler(LOG_FILE, encoding='utf-8')
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    # 记录启动信息
    logger = logging.getLogger(__name__)
    logger.info(f"日志系统初始化完成，日志文件: {LOG_FILE}")


def get_log_file_path() -> Path:
    """获取日志文件路径"""
    return LOG_FILE


def get_log_dir() -> Path:
    """获取日志目录"""
    return LOG_DIR


def write_debug_log(msg: str) -> None:
    """
    写入调试日志（用于 type_text 等需要详细调试的场景）
    """
    debug_log_file = LOG_DIR / "debug.log"
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
    with open(debug_log_file, "a", encoding="utf-8") as f:
        f.write(f"{timestamp} {msg}\n")


def read_log_content(max_lines: int = 500) -> str:
    """
    读取日志内容（用于日志查看窗口）

    Args:
        max_lines: 最多读取的行数（从末尾开始）

    Returns:
        日志内容字符串
    """
    if not LOG_FILE.exists():
        return "暂无日志"

    try:
        with open(LOG_FILE, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        # 取最后 max_lines 行
        if len(lines) > max_lines:
            lines = lines[-max_lines:]

        return ''.join(lines)
    except Exception as e:
        return f"读取日志失败: {e}"


def clear_log() -> bool:
    """
    清空日志文件

    Returns:
        是否成功
    """
    try:
        with open(LOG_FILE, 'w', encoding='utf-8') as f:
            f.write("")

        logger = logging.getLogger(__name__)
        logger.info("日志已清空")
        return True
    except Exception as e:
        return False
