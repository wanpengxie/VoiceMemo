"""
UI 工具模块
- 剪贴板操作
- 模拟键盘输入
"""

import subprocess
import logging
from typing import Tuple

logger = logging.getLogger(__name__)


def get_clipboard() -> str:
    """获取剪贴板内容"""
    try:
        result = subprocess.run(
            ['pbpaste'],
            capture_output=True,
            text=True
        )
        return result.stdout
    except Exception as e:
        logger.warning(f"读取剪贴板失败: {e}")
        return ""


def set_clipboard(text: str) -> bool:
    """设置剪贴板内容"""
    try:
        process = subprocess.Popen(
            ['pbcopy'],
            stdin=subprocess.PIPE
        )
        process.communicate(text.encode('utf-8'))
        return process.returncode == 0
    except Exception as e:
        logger.error(f"设置剪贴板失败: {e}")
        return False


def type_text(text: str, restore_clipboard: bool = True) -> tuple[bool, str]:
    """
    使用剪贴板 + Cmd+V 输入文本

    Args:
        text: 要输入的文本
        restore_clipboard: 是否恢复剪贴板原内容

    Returns:
        (是否成功, 错误信息)
    """
    old_clipboard = ""
    if restore_clipboard:
        old_clipboard = get_clipboard()

    try:
        if not set_clipboard(text):
            return False, "设置剪贴板失败"

        from Quartz import (
            CGEventCreateKeyboardEvent,
            CGEventPost,
            CGEventSetFlags,
            kCGHIDEventTap,
            kCGEventFlagMaskCommand,
        )
        import time

        # V 键的 keycode 是 9
        v_keycode = 9

        # 按下 Cmd+V
        event = CGEventCreateKeyboardEvent(None, v_keycode, True)
        CGEventSetFlags(event, kCGEventFlagMaskCommand)
        CGEventPost(kCGHIDEventTap, event)

        time.sleep(0.05)

        # 释放 Cmd+V
        event = CGEventCreateKeyboardEvent(None, v_keycode, False)
        CGEventSetFlags(event, kCGEventFlagMaskCommand)
        CGEventPost(kCGHIDEventTap, event)

        return True, ""

    except Exception as e:
        return False, str(e)

    finally:
        if restore_clipboard and old_clipboard:
            import threading
            def restore():
                import time
                time.sleep(0.3)
                set_clipboard(old_clipboard)
            threading.Thread(target=restore, daemon=True).start()
