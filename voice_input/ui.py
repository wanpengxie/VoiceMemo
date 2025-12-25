"""
UI 工具模块
- 剪贴板操作
- 模拟键盘输入
"""

import logging
import os
import sys

from .log_manager import write_debug_log as _write_log

logger = logging.getLogger(__name__)


def get_clipboard() -> str:
    """获取剪贴板内容（使用 NSPasteboard）"""
    try:
        from AppKit import NSPasteboard, NSStringPboardType
        pb = NSPasteboard.generalPasteboard()
        return pb.stringForType_(NSStringPboardType) or ""
    except Exception as e:
        logger.warning(f"读取剪贴板失败: {e}")
        return ""


def set_clipboard(text: str) -> bool:
    """设置剪贴板内容（使用 NSPasteboard）"""
    try:
        from AppKit import NSPasteboard, NSStringPboardType
        pb = NSPasteboard.generalPasteboard()
        pb.clearContents()
        return pb.setString_forType_(text, NSStringPboardType)
    except Exception as e:
        logger.error(f"设置剪贴板失败: {e}")
        return False


def type_text(text: str, restore_clipboard: bool = True) -> tuple[bool, str]:
    """
    使用 NSPasteboard + CGEvent 模拟 Cmd+V 输入文本
    """
    _write_log("=" * 50)
    _write_log(f"[ENV] LANG={os.environ.get('LANG', '(未设置)')}")
    _write_log(f"[ENV] sys.executable={sys.executable}")

    old_clipboard = ""
    if restore_clipboard:
        old_clipboard = get_clipboard()

    try:
        _write_log(f"[type_text] 输入文本: {text}")

        # 1. 使用 NSPasteboard 设置剪贴板
        _write_log("[clipboard] 使用 NSPasteboard...")
        from AppKit import NSPasteboard, NSStringPboardType
        pb = NSPasteboard.generalPasteboard()
        pb.clearContents()
        result = pb.setString_forType_(text, NSStringPboardType)
        _write_log(f"[clipboard] setString 返回: {result}")

        # 验证
        content = pb.stringForType_(NSStringPboardType)
        _write_log(f"[clipboard] 验证: {content}")

        if not content:
            _write_log("[clipboard] 剪贴板设置失败!")
            return False, "剪贴板设置失败"

        import time
        time.sleep(0.05)

        # 2. 使用 CGEvent 模拟 Cmd+V
        _write_log("[CGEvent] 开始...")
        from Quartz import (
            CGEventCreateKeyboardEvent,
            CGEventPost,
            CGEventSetFlags,
            kCGHIDEventTap,
            kCGEventFlagMaskCommand,
        )

        v_keycode = 9  # V 键

        # 按下 Cmd+V
        event = CGEventCreateKeyboardEvent(None, v_keycode, True)
        _write_log(f"[CGEvent] event created: {event}")

        if event is None:
            _write_log("[CGEvent] 创建事件失败!")
            return False, "无法创建键盘事件"

        CGEventSetFlags(event, kCGEventFlagMaskCommand)
        CGEventPost(kCGHIDEventTap, event)
        _write_log("[CGEvent] key down posted")

        time.sleep(0.05)

        # 释放 Cmd+V
        event = CGEventCreateKeyboardEvent(None, v_keycode, False)
        CGEventSetFlags(event, kCGEventFlagMaskCommand)
        CGEventPost(kCGHIDEventTap, event)
        _write_log("[CGEvent] key up posted")

        _write_log("[type_text] 完成")
        return True, ""

    except Exception as e:
        _write_log(f"[type_text] 异常: {e}")
        import traceback
        _write_log(traceback.format_exc())
        return False, str(e)

    finally:
        if restore_clipboard and old_clipboard:
            import threading
            def restore():
                import time
                time.sleep(0.3)
                set_clipboard(old_clipboard)
            threading.Thread(target=restore, daemon=True).start()
