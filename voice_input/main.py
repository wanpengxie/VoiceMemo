"""
语音输入法 - 简洁版
按住 Option 键录音，实时显示，松手自动输入
"""

import threading
import logging
import time
from typing import Optional
from pynput import keyboard

from . import config
from .asr_client import ASRClient
from .audio_recorder import AudioRecorder
from .ui import type_text, set_clipboard, get_clipboard

logger = logging.getLogger(__name__)


class StatusBar:
    """简洁的状态条 - 使用 Cocoa NSWindow"""

    def __init__(self):
        self.window = None
        self.text_field = None
        self._app = None
        # 不在这里创建窗口，等 run() 时再创建

    def _setup_window(self):
        """创建 typeless 风格语音输入气泡窗口"""
        try:
            from AppKit import (
                NSWindow, NSWindowStyleMaskBorderless,
                NSBackingStoreBuffered,
                NSColor, NSTextField, NSFont, NSMakeRect,
                NSScreen, NSTextAlignmentLeft,
                NSWindowCollectionBehaviorCanJoinAllSpaces,
                NSWindowCollectionBehaviorStationary,
                NSView, NSImage, NSImageView
            )
            from Quartz import (
                kCGMaximumWindowLevelKey, CGWindowLevelForKey,
                kCACornerCurveContinuous,
                CGColorCreateGenericRGB
            )

            # ═══════════════════════════════════════════════════════════════
            # Typeless 风格气泡 - 参照设计稿
            # 最小宽度360px, 最小高度56px(两行), 字体14px
            # ═══════════════════════════════════════════════════════════════

            self._card_width = 360
            self._card_height = 56  # 两行高度
            corner_radius = 16  # 更圆润的气泡

            # 左侧麦克风区域尺寸
            self._mic_area_size = 40
            self._mic_icon_size = 20
            self._ring_outer_size = 36
            self._ring_inner_size = 28

            # 阴影空间
            shadow_padding = 16
            window_width = self._card_width + shadow_padding * 2
            window_height = self._card_height + shadow_padding * 2

            # typeless 配色
            # 气泡背景: rgb(187, 217, 251) 浅蓝色
            bg_color = NSColor.colorWithRed_green_blue_alpha_(
                187/255.0, 217/255.0, 251/255.0, 1.0
            )
            # 文本颜色: rgb(23, 23, 23) 深灰
            text_color = NSColor.colorWithRed_green_blue_alpha_(
                23/255.0, 23/255.0, 23/255.0, 1.0
            )
            # 麦克风圆环颜色: rgb(30, 67, 188) 深蓝
            ring_color = NSColor.colorWithRed_green_blue_alpha_(
                30/255.0, 67/255.0, 188/255.0, 1.0
            )
            # 麦克风图标颜色: rgb(0, 99, 245) 蓝色
            mic_color = NSColor.colorWithRed_green_blue_alpha_(
                0/255.0, 99/255.0, 245/255.0, 1.0
            )

            # ═══════════════════════════════════════════════════════════════
            # 窗口（初始位置，后续会跟随光标）
            # ═══════════════════════════════════════════════════════════════

            screen = NSScreen.mainScreen()
            screen_frame = screen.frame()
            x = (screen_frame.size.width - window_width) / 2
            y = (screen_frame.size.height - window_height) / 2

            self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
                NSMakeRect(x, y, window_width, window_height),
                NSWindowStyleMaskBorderless,
                NSBackingStoreBuffered,
                False
            )

            max_level = CGWindowLevelForKey(kCGMaximumWindowLevelKey)
            self.window.setLevel_(max_level)
            self.window.setOpaque_(False)
            self.window.setBackgroundColor_(NSColor.clearColor())
            self.window.setIgnoresMouseEvents_(True)
            self.window.setHasShadow_(False)
            self.window.setCollectionBehavior_(
                NSWindowCollectionBehaviorCanJoinAllSpaces |
                NSWindowCollectionBehaviorStationary
            )

            # ═══════════════════════════════════════════════════════════════
            # 气泡卡片
            # ═══════════════════════════════════════════════════════════════

            content_view = self.window.contentView()
            content_view.setWantsLayer_(True)

            card_view = NSView.alloc().initWithFrame_(
                NSMakeRect(shadow_padding, shadow_padding, self._card_width, self._card_height)
            )
            card_view.setWantsLayer_(True)
            layer = card_view.layer()

            layer.setBackgroundColor_(bg_color.CGColor())
            layer.setCornerRadius_(corner_radius)
            if hasattr(layer, 'setCornerCurve_'):
                layer.setCornerCurve_(kCACornerCurveContinuous)

            # 柔和阴影
            layer.setShadowOpacity_(0.08)
            layer.setShadowRadius_(12)
            layer.setShadowOffset_((2.83, -2.83))  # 45度角阴影
            layer.setShadowColor_(CGColorCreateGenericRGB(0, 0, 0, 1))

            content_view.addSubview_(card_view)

            # ═══════════════════════════════════════════════════════════════
            # 左侧：麦克风区域（圆环 + 图标）
            # ═══════════════════════════════════════════════════════════════

            mic_area_x = 12
            mic_area_y = (self._card_height - self._mic_area_size) / 2

            # 外圈脉动环 (opacity 0.04)
            ring_outer_x = mic_area_x + (self._mic_area_size - self._ring_outer_size) / 2
            ring_outer_y = mic_area_y + (self._mic_area_size - self._ring_outer_size) / 2

            self._ring_outer = NSView.alloc().initWithFrame_(
                NSMakeRect(ring_outer_x, ring_outer_y, self._ring_outer_size, self._ring_outer_size)
            )
            self._ring_outer.setWantsLayer_(True)
            ring_outer_layer = self._ring_outer.layer()
            ring_outer_layer.setBackgroundColor_(ring_color.colorWithAlphaComponent_(0.04).CGColor())
            ring_outer_layer.setCornerRadius_(self._ring_outer_size / 2)
            card_view.addSubview_(self._ring_outer)

            # 内圈脉动环 (opacity 0.05)
            ring_inner_x = mic_area_x + (self._mic_area_size - self._ring_inner_size) / 2
            ring_inner_y = mic_area_y + (self._mic_area_size - self._ring_inner_size) / 2

            self._ring_inner = NSView.alloc().initWithFrame_(
                NSMakeRect(ring_inner_x, ring_inner_y, self._ring_inner_size, self._ring_inner_size)
            )
            self._ring_inner.setWantsLayer_(True)
            ring_inner_layer = self._ring_inner.layer()
            ring_inner_layer.setBackgroundColor_(ring_color.colorWithAlphaComponent_(0.05).CGColor())
            ring_inner_layer.setCornerRadius_(self._ring_inner_size / 2)
            card_view.addSubview_(self._ring_inner)

            # 麦克风图标核心 (小圆点代替图标)
            mic_core_size = 20
            mic_core_x = mic_area_x + (self._mic_area_size - mic_core_size) / 2
            mic_core_y = mic_area_y + (self._mic_area_size - mic_core_size) / 2

            self._mic_core = NSView.alloc().initWithFrame_(
                NSMakeRect(mic_core_x, mic_core_y, mic_core_size, mic_core_size)
            )
            self._mic_core.setWantsLayer_(True)
            mic_core_layer = self._mic_core.layer()
            mic_core_layer.setBackgroundColor_(mic_color.CGColor())
            mic_core_layer.setCornerRadius_(mic_core_size / 2)
            card_view.addSubview_(self._mic_core)

            # 保存圆环layer引用用于动画
            self._ring_outer_layer = ring_outer_layer
            self._ring_inner_layer = ring_inner_layer
            self._mic_core_layer = mic_core_layer

            # ═══════════════════════════════════════════════════════════════
            # 右侧：文本区域 - 14px字体，左对齐
            # ═══════════════════════════════════════════════════════════════

            text_x = mic_area_x + self._mic_area_size + 12
            text_width = self._card_width - text_x - 16
            text_height = self._card_height - 16  # 上下各留8px

            self.text_field = NSTextField.alloc().initWithFrame_(
                NSMakeRect(text_x, 8, text_width, text_height)
            )
            self.text_field.setStringValue_("准备就绪")
            self.text_field.setEditable_(False)
            self.text_field.setBezeled_(False)
            self.text_field.setDrawsBackground_(False)
            self.text_field.setFont_(NSFont.systemFontOfSize_(14))
            self.text_field.setTextColor_(text_color)
            self.text_field.setAlignment_(NSTextAlignmentLeft)

            # 启用多行换行
            cell = self.text_field.cell()
            cell.setWraps_(True)
            cell.setLineBreakMode_(0)  # NSLineBreakByWordWrapping
            self.text_field.setMaximumNumberOfLines_(0)  # 不限制行数
            self.text_field.setPreferredMaxLayoutWidth_(text_width)  # 设置换行宽度

            card_view.addSubview_(self.text_field)

            # 保存引用
            self._card_view = card_view
            self._indicator_layer = mic_core_layer  # 兼容旧代码
            self._shadow_padding = shadow_padding

            logger.info("typeless 风格气泡窗口创建成功")

        except Exception as e:
            logger.error(f"创建状态条失败: {e}")
            import traceback
            traceback.print_exc()
            self.window = None

        logger.info(f"_setup_window 完成, window={self.window is not None}")

    def _get_text_caret_position(self):
        """获取文本输入光标位置（使用 Accessibility API）"""
        try:
            from ApplicationServices import (
                AXUIElementCreateSystemWide,
                AXUIElementCopyAttributeValue,
                kAXFocusedUIElementAttribute,
                kAXSelectedTextRangeAttribute,
                kAXBoundsForRangeParameterizedAttribute,
                AXUIElementCopyParameterizedAttributeValue,
                AXValueGetValue,
                kAXValueCGRectType
            )
            from Quartz import CGRect

            # 获取系统级 accessibility 元素
            system_wide = AXUIElementCreateSystemWide()

            # 获取当前焦点元素
            err, focused_element = AXUIElementCopyAttributeValue(
                system_wide, kAXFocusedUIElementAttribute, None
            )
            if err != 0 or focused_element is None:
                logger.warning(f"[光标] 获取焦点元素失败: err={err}")
                return None

            # 获取焦点元素的角色，帮助调试
            err_role, role = AXUIElementCopyAttributeValue(focused_element, "AXRole", None)
            if err_role == 0:
                logger.info(f"[光标] 焦点元素角色: {role}")
            
            # ═══════════════════════════════════════════════════════════════
            # 策略 1: 标准 Cocoa 应用 (kAXSelectedTextRangeAttribute)
            # ═══════════════════════════════════════════════════════════════
            
            # 获取选中文本范围（光标位置）
            err, selected_range = AXUIElementCopyAttributeValue(
                focused_element, kAXSelectedTextRangeAttribute, None
            )
            
            if err == 0 and selected_range is not None:
                logger.info(f"[光标] 策略1: 获取到 selected_range")
                # 尝试获取光标位置的屏幕坐标
                # 某些应用可能支持 kAXSelectedTextRangeAttribute 但不支持 kAXBoundsForRangeParameterizedAttribute
                err, bounds_value = AXUIElementCopyParameterizedAttributeValue(
                    focused_element,
                    kAXBoundsForRangeParameterizedAttribute,
                    selected_range,
                    None
                )
                
                if err == 0 and bounds_value is not None:
                     # 解析 CGRect
                    # PyObjC 的 AXValueGetValue 返回 (boolean, value)
                    success, rect = AXValueGetValue(bounds_value, kAXValueCGRectType, None)
                    if success:
                        # Accessibility API 返回的是左上角坐标系，需要转换为 Cocoa 坐标系（左下角）
                        from AppKit import NSScreen
                        # 必须使用主屏幕（screens[0]）的高度进行坐标转换，因为 Cocoa 坐标系原点在主屏幕左下角
                        primary_screen_height = NSScreen.screens()[0].frame().size.height

                        # rect.origin.y 是从屏幕顶部算起的，转换为从底部算起
                        cocoa_y = primary_screen_height - rect.origin.y - rect.size.height
                        logger.info(f"[光标] 策略1成功: ({rect.origin.x}, {cocoa_y})")
                        return (rect.origin.x, cocoa_y, rect.size.width, rect.size.height)
                    else:
                        logger.info("[光标] 策略1: AXValueGetValue 解析失败")
            
            # ═══════════════════════════════════════════════════════════════
            # 策略 2: Electron/WebKit 应用 (AXSelectedTextMarkerRange)
            # VS Code, Chrome 等使用此属性
            # ═══════════════════════════════════════════════════════════════
            
            # 定义常量（PyObjC 可能未包含）
            kAXSelectedTextMarkerRangeAttribute = "AXSelectedTextMarkerRange"
            kAXBoundsForTextMarkerRangeParameterizedAttribute = "AXBoundsForTextMarkerRange"
            
            err, selected_marker_range = AXUIElementCopyAttributeValue(
                focused_element, kAXSelectedTextMarkerRangeAttribute, None
            )
            
            if err == 0 and selected_marker_range is not None:
                logger.info("[光标] 策略2: 检测到 Electron/WebKit 应用")
                err, bounds_value = AXUIElementCopyParameterizedAttributeValue(
                    focused_element,
                    kAXBoundsForTextMarkerRangeParameterizedAttribute,
                    selected_marker_range,
                    None
                )

                if err == 0 and bounds_value is not None:
                    success, rect = AXValueGetValue(bounds_value, kAXValueCGRectType, None)
                    if success:
                        from AppKit import NSScreen
                        primary_screen_height = NSScreen.screens()[0].frame().size.height
                        cocoa_y = primary_screen_height - rect.origin.y - rect.size.height
                        logger.info(f"[光标] 策略2成功: ({rect.origin.x}, {cocoa_y})")
                        return (rect.origin.x, cocoa_y, rect.size.width, rect.size.height)
                    else:
                        logger.info("[光标] 策略2: AXValueGetValue 解析失败")

            # ═══════════════════════════════════════════════════════════════
            # Fallback: 如果获取不到具体光标位置，尝试获取焦点元素（输入框）的位置
            # ═══════════════════════════════════════════════════════════════
            
            # 获取元素位置
            err, pos_value = AXUIElementCopyAttributeValue(focused_element, "AXPosition", None)
            if err != 0 or pos_value is None:
                return None
                
            # 获取元素大小
            err, size_value = AXUIElementCopyAttributeValue(focused_element, "AXSize", None)
            if err != 0 or size_value is None:
                return None

            success_pos, pos = AXValueGetValue(pos_value, 1, None) # kAXValueCGPointType = 1
            success_size, size = AXValueGetValue(size_value, 2, None) # kAXValueCGSizeType = 2
            
            if success_pos and success_size:
                from AppKit import NSScreen
                # 同样使用主屏幕高度进行转换
                primary_screen_height = NSScreen.screens()[0].frame().size.height

                # 转换为 Cocoa 坐标系
                cocoa_y = primary_screen_height - pos.y - size.height
                # 返回元素左下角位置，标记为非精确光标
                logger.info(f"[光标] Fallback: 使用焦点元素位置 ({pos.x}, {cocoa_y})")
                return (pos.x, cocoa_y, size.width, size.height)

            logger.info("[光标] 所有策略都失败")
            return None
        except Exception as e:
            logger.error(f"[光标] 获取文本光标位置异常: {e}")
            import traceback
            traceback.print_exc()
            return None

    def _get_focused_window_input_area(self):
        """获取焦点窗口中可能的输入区域位置"""
        try:
            from AppKit import NSWorkspace, NSScreen
            from ApplicationServices import (
                AXUIElementCreateApplication,
                AXUIElementCopyAttributeValue,
                AXValueGetValue
            )

            # 获取当前活跃应用
            active_app = NSWorkspace.sharedWorkspace().frontmostApplication()
            if not active_app:
                return None

            # 获取应用的 AX 元素
            app_element = AXUIElementCreateApplication(active_app.processIdentifier())

            # 获取焦点窗口
            err, focused_window = AXUIElementCopyAttributeValue(
                app_element, "AXFocusedWindow", None
            )
            if err != 0 or focused_window is None:
                return None

            # 获取窗口位置和大小
            err, pos_value = AXUIElementCopyAttributeValue(focused_window, "AXPosition", None)
            err2, size_value = AXUIElementCopyAttributeValue(focused_window, "AXSize", None)

            if err != 0 or err2 != 0 or pos_value is None or size_value is None:
                return None

            success_pos, pos = AXValueGetValue(pos_value, 1, None)  # kAXValueCGPointType = 1
            success_size, size = AXValueGetValue(size_value, 2, None)  # kAXValueCGSizeType = 2

            if success_pos and success_size:
                # 转换坐标系
                primary_screen_height = NSScreen.screens()[0].frame().size.height
                # 返回窗口中心偏下的位置（通常是输入区域）
                center_x = pos.x + size.width / 2
                # 使用窗口底部 30% 的位置作为估计的输入区域
                center_y = primary_screen_height - pos.y - size.height * 0.3
                logger.info(f"[定位] 焦点窗口输入区域: ({center_x}, {center_y})")
                return (center_x, center_y)
        except Exception as e:
            logger.debug(f"获取焦点窗口输入区域失败: {e}")
        return None

    def _get_focused_screen_center(self):
        """获取焦点窗口所在屏幕的中央位置"""
        try:
            from AppKit import NSWorkspace, NSScreen
            from ApplicationServices import (
                AXUIElementCreateApplication,
                AXUIElementCopyAttributeValue,
                AXValueGetValue
            )

            # 获取当前活跃应用的焦点窗口位置
            active_app = NSWorkspace.sharedWorkspace().frontmostApplication()
            if active_app:
                app_element = AXUIElementCreateApplication(active_app.processIdentifier())
                err, focused_window = AXUIElementCopyAttributeValue(app_element, "AXFocusedWindow", None)

                if err == 0 and focused_window:
                    err, pos_value = AXUIElementCopyAttributeValue(focused_window, "AXPosition", None)
                    if err == 0 and pos_value:
                        success, pos = AXValueGetValue(pos_value, 1, None)
                        if success:
                            # 找到包含这个窗口的屏幕
                            for screen in NSScreen.screens():
                                frame = screen.frame()
                                if (frame.origin.x <= pos.x < frame.origin.x + frame.size.width):
                                    # 返回这个屏幕的中央
                                    center_x = frame.origin.x + frame.size.width / 2
                                    center_y = frame.origin.y + frame.size.height / 2
                                    logger.info(f"[定位] 焦点屏幕中央: ({center_x}, {center_y})")
                                    return (center_x, center_y)

            # 如果获取失败，使用主屏幕中央
            main_screen = NSScreen.mainScreen()
            frame = main_screen.frame()
            center_x = frame.origin.x + frame.size.width / 2
            center_y = frame.origin.y + frame.size.height / 2
            logger.info(f"[定位] 主屏幕中央: ({center_x}, {center_y})")
            return (center_x, center_y)
        except Exception as e:
            logger.debug(f"获取屏幕中央失败: {e}")
            return None

    def _move_to_cursor(self):
        """将窗口移动到文本光标附近 - 智能 fallback 链"""
        try:
            from AppKit import NSScreen

            # 窗口尺寸（包含阴影）
            window_width = self._card_width + self._shadow_padding * 2
            window_height = self._card_height + self._shadow_padding * 2

            # ═══════════════════════════════════════════════════════════════
            # 智能 Fallback 链（不再使用鼠标位置！）
            # 1. 精确光标位置
            # 2. 焦点窗口输入区域
            # 3. 焦点屏幕中央
            # ═══════════════════════════════════════════════════════════════

            caret_pos = self._get_text_caret_position()
            use_caret = False

            if caret_pos:
                # 策略 1: 使用精确光标位置
                caret_x, caret_y, caret_w, caret_h = caret_pos
                x = caret_x
                tooltip_gap = 4
                y = caret_y - window_height - tooltip_gap + self._shadow_padding
                use_caret = True
                logger.info(f"[定位] 使用精确光标位置")
            else:
                # 策略 2: 尝试焦点窗口输入区域
                window_pos = self._get_focused_window_input_area()
                if window_pos:
                    x = window_pos[0] - window_width / 2
                    y = window_pos[1] - window_height / 2
                    logger.info(f"[定位] 使用焦点窗口输入区域")
                else:
                    # 策略 3: 焦点屏幕中央（最终 fallback）
                    screen_center = self._get_focused_screen_center()
                    if screen_center:
                        x = screen_center[0] - window_width / 2
                        y = screen_center[1] - window_height / 2
                        logger.info(f"[定位] 使用焦点屏幕中央")
                    else:
                        # 极端情况：主屏幕中央
                        frame = NSScreen.mainScreen().frame()
                        x = frame.size.width / 2 - window_width / 2
                        y = frame.size.height / 2 - window_height / 2
                        logger.info(f"[定位] 使用主屏幕中央")

            # ═══════════════════════════════════════════════════════════════
            # 边界检查：确保不超出屏幕
            # ═══════════════════════════════════════════════════════════════
            screen = NSScreen.mainScreen()
            screen_frame = screen.frame()

            # 右边界
            if x + window_width > screen_frame.size.width:
                x = screen_frame.size.width - window_width - 8

            # 左边界
            if x < 8:
                x = 8

            # 下边界：如果下方空间不够，改为显示在光标上方
            if y < 8:
                if use_caret and caret_pos:
                    y = caret_pos[1] + caret_pos[3] + 4  # 光标上方
                else:
                    y = 8

            # 上边界
            if y + window_height > screen_frame.size.height:
                y = screen_frame.size.height - window_height - 8

            # 移动窗口
            self.window.setFrameOrigin_((x, y))

        except Exception as e:
            logger.error(f"[定位] 移动窗口失败: {e}")
            import traceback
            traceback.print_exc()

    def _set_recording_state(self, is_recording: bool):
        """更新录音指示器状态 - typeless 风格脉动动画"""
        try:
            from AppKit import NSColor
            from Quartz import (
                CABasicAnimation, CAMediaTimingFunction,
                kCAMediaTimingFunctionEaseInEaseOut,
                CAAnimationGroup
            )

            # 检查圆环层是否存在
            has_rings = (hasattr(self, '_ring_outer_layer') and
                        hasattr(self, '_ring_inner_layer') and
                        hasattr(self, '_mic_core_layer'))

            if not has_rings:
                return

            ring_outer = self._ring_outer_layer
            ring_inner = self._ring_inner_layer
            mic_core = self._mic_core_layer

            if is_recording:
                # ═══════════════════════════════════════════════════════════════
                # 录音中：麦克风脉动 + 圆环扩散动画
                # ═══════════════════════════════════════════════════════════════

                # 外圈脉动 - scale 1.0 -> 1.3，透明度变化
                outer_scale = CABasicAnimation.animationWithKeyPath_("transform.scale")
                outer_scale.setFromValue_(1.0)
                outer_scale.setToValue_(1.3)
                outer_scale.setDuration_(1.5)
                outer_scale.setAutoreverses_(True)
                outer_scale.setRepeatCount_(float('inf'))
                outer_scale.setTimingFunction_(
                    CAMediaTimingFunction.functionWithName_(kCAMediaTimingFunctionEaseInEaseOut)
                )
                ring_outer.addAnimation_forKey_(outer_scale, "pulse_scale")

                outer_opacity = CABasicAnimation.animationWithKeyPath_("opacity")
                outer_opacity.setFromValue_(0.04)
                outer_opacity.setToValue_(0.12)
                outer_opacity.setDuration_(1.5)
                outer_opacity.setAutoreverses_(True)
                outer_opacity.setRepeatCount_(float('inf'))
                outer_opacity.setTimingFunction_(
                    CAMediaTimingFunction.functionWithName_(kCAMediaTimingFunctionEaseInEaseOut)
                )
                ring_outer.addAnimation_forKey_(outer_opacity, "pulse_opacity")

                # 内圈脉动 - 稍小幅度，稍快节奏
                inner_scale = CABasicAnimation.animationWithKeyPath_("transform.scale")
                inner_scale.setFromValue_(1.0)
                inner_scale.setToValue_(1.2)
                inner_scale.setDuration_(1.0)
                inner_scale.setAutoreverses_(True)
                inner_scale.setRepeatCount_(float('inf'))
                inner_scale.setTimingFunction_(
                    CAMediaTimingFunction.functionWithName_(kCAMediaTimingFunctionEaseInEaseOut)
                )
                ring_inner.addAnimation_forKey_(inner_scale, "pulse_scale")

                inner_opacity = CABasicAnimation.animationWithKeyPath_("opacity")
                inner_opacity.setFromValue_(0.05)
                inner_opacity.setToValue_(0.15)
                inner_opacity.setDuration_(1.0)
                inner_opacity.setAutoreverses_(True)
                inner_opacity.setRepeatCount_(float('inf'))
                inner_opacity.setTimingFunction_(
                    CAMediaTimingFunction.functionWithName_(kCAMediaTimingFunctionEaseInEaseOut)
                )
                ring_inner.addAnimation_forKey_(inner_opacity, "pulse_opacity")

                # 麦克风核心 - 轻微脉动
                core_scale = CABasicAnimation.animationWithKeyPath_("transform.scale")
                core_scale.setFromValue_(1.0)
                core_scale.setToValue_(1.1)
                core_scale.setDuration_(0.8)
                core_scale.setAutoreverses_(True)
                core_scale.setRepeatCount_(float('inf'))
                core_scale.setTimingFunction_(
                    CAMediaTimingFunction.functionWithName_(kCAMediaTimingFunctionEaseInEaseOut)
                )
                mic_core.addAnimation_forKey_(core_scale, "pulse_scale")

            else:
                # ═══════════════════════════════════════════════════════════════
                # 非录音：移除所有动画，恢复初始状态
                # ═══════════════════════════════════════════════════════════════
                ring_outer.removeAllAnimations()
                ring_inner.removeAllAnimations()
                mic_core.removeAllAnimations()

                # 重置透明度和缩放
                ring_outer.setOpacity_(1.0)
                ring_inner.setOpacity_(1.0)
                mic_core.setOpacity_(1.0)

        except Exception as e:
            logger.debug(f"更新指示器状态失败: {e}")

    def init_app(self):
        """初始化应用（必须在主线程调用）"""
        logger.info("init_app 开始")
        try:
            from AppKit import NSApplication
            self._app = NSApplication.sharedApplication()
            self._app.setActivationPolicy_(1)  # Accessory
            logger.info("NSApplication 初始化完成，开始创建窗口")
            self._setup_window()
        except Exception as e:
            logger.error(f"init_app 失败: {e}")
            import traceback
            traceback.print_exc()

    def show(self, text: str = "🎤 录音中..."):
        """显示状态条（线程安全）"""
        self._pending_action = ('show', text)
        # 同时尝试使用 AppHelper（菜单栏应用模式）
        try:
            from PyObjCTools import AppHelper
            AppHelper.callAfter(self._do_show, text)
        except:
            pass  # 非菜单栏模式，使用 process_pending

    def _do_show(self, text: str):
        """执行显示"""
        if self.window:
            clean_text = text.replace("🎤 ", "").replace("❌ ", "").replace("✅ ", "")
            self.text_field.setStringValue_(clean_text)
            self._set_recording_state(True)
            self._move_to_cursor()
            self.window.orderFrontRegardless()

    def update(self, text: str):
        """更新文字（线程安全）"""
        self._pending_action = ('update', text)
        try:
            from PyObjCTools import AppHelper
            AppHelper.callAfter(self._do_update, text)
        except:
            pass

    def _do_update(self, text: str):
        """执行更新，自动调整高度"""
        if self.text_field:
            clean_text = text.replace("🎤 ", "").replace("❌ ", "").replace("✅ ", "")
            self.text_field.setStringValue_(clean_text)

            # 动态计算所需高度
            self._adjust_height_for_text(clean_text)

    def _adjust_height_for_text(self, text: str):
        """根据文字内容调整窗口高度"""
        try:
            from AppKit import NSMakeRect, NSFont, NSMakeSize
            from Foundation import NSString, NSUTF8StringEncoding

            if not hasattr(self, '_card_view') or not self._card_view:
                return

            # 文字区域宽度（与 _setup_window 保持一致）
            text_x = 12 + self._mic_area_size + 12  # mic_area_x + size + gap
            text_width = self._card_width - text_x - 16

            # 计算文字所需高度
            font = NSFont.systemFontOfSize_(14)
            ns_string = NSString.stringWithString_(text) if text else NSString.stringWithString_("")

            # 使用 boundingRectWithSize 计算多行文字高度
            from AppKit import NSStringDrawingUsesLineFragmentOrigin
            attrs = {
                'NSFont': font,
            }
            bounding_rect = ns_string.boundingRectWithSize_options_attributes_(
                NSMakeSize(text_width, 10000),  # 最大高度设大
                NSStringDrawingUsesLineFragmentOrigin,
                attrs
            )

            # 计算所需卡片高度（文字高度 + 上下边距）
            text_height = bounding_rect.size.height
            min_card_height = 56  # 最小两行高度
            max_card_height = 200  # 最大高度限制
            padding = 16  # 上下各 8px

            new_card_height = max(min_card_height, min(max_card_height, text_height + padding))

            # 如果高度没变化，不需要调整
            current_height = self._card_view.frame().size.height
            if abs(new_card_height - current_height) < 2:
                return

            # 更新卡片高度
            card_frame = self._card_view.frame()
            height_diff = new_card_height - card_frame.size.height

            # 更新卡片
            new_card_frame = NSMakeRect(
                card_frame.origin.x,
                card_frame.origin.y,
                card_frame.size.width,
                new_card_height
            )
            self._card_view.setFrame_(new_card_frame)

            # 更新文字区域高度
            text_frame = self.text_field.frame()
            new_text_frame = NSMakeRect(
                text_frame.origin.x,
                8,  # 底部边距
                text_frame.size.width,
                new_card_height - 16  # 上下各 8px
            )
            self.text_field.setFrame_(new_text_frame)

            # 更新麦克风区域垂直居中
            mic_area_y = (new_card_height - self._mic_area_size) / 2

            # 更新外圈位置
            ring_outer_x = 12 + (self._mic_area_size - self._ring_outer_size) / 2
            ring_outer_y = mic_area_y + (self._mic_area_size - self._ring_outer_size) / 2
            self._ring_outer.setFrameOrigin_((ring_outer_x, ring_outer_y))

            # 更新内圈位置
            ring_inner_x = 12 + (self._mic_area_size - self._ring_inner_size) / 2
            ring_inner_y = mic_area_y + (self._mic_area_size - self._ring_inner_size) / 2
            self._ring_inner.setFrameOrigin_((ring_inner_x, ring_inner_y))

            # 更新核心位置
            mic_core_size = 20
            mic_core_x = 12 + (self._mic_area_size - mic_core_size) / 2
            mic_core_y = mic_area_y + (self._mic_area_size - mic_core_size) / 2
            self._mic_core.setFrameOrigin_((mic_core_x, mic_core_y))

            # 更新窗口高度
            window_frame = self.window.frame()
            new_window_height = new_card_height + self._shadow_padding * 2

            # 窗口向上扩展（保持底部位置不变）
            new_window_frame = NSMakeRect(
                window_frame.origin.x,
                window_frame.origin.y - height_diff,  # 向下移动以保持视觉位置
                window_frame.size.width,
                new_window_height
            )
            self.window.setFrame_display_(new_window_frame, True)

            # 保存当前卡片高度
            self._card_height = new_card_height

        except Exception as e:
            import logging
            logging.getLogger(__name__).debug(f"调整高度失败: {e}")

    def hide(self):
        """隐藏状态条（线程安全）"""
        self._pending_action = ('hide', None)
        try:
            from PyObjCTools import AppHelper
            AppHelper.callAfter(self._do_hide)
        except:
            pass

    def _do_hide(self):
        """执行隐藏"""
        if self.window:
            self._set_recording_state(False)
            self.window.orderOut_(None)
            # 重置高度到初始状态
            self._reset_height()

    def _reset_height(self):
        """重置窗口高度到初始状态"""
        try:
            from AppKit import NSMakeRect

            if not hasattr(self, '_card_view') or not self._card_view:
                return

            min_card_height = 56

            # 重置卡片高度
            card_frame = self._card_view.frame()
            if abs(card_frame.size.height - min_card_height) < 2:
                return

            new_card_frame = NSMakeRect(
                card_frame.origin.x,
                card_frame.origin.y,
                card_frame.size.width,
                min_card_height
            )
            self._card_view.setFrame_(new_card_frame)

            # 重置文字区域
            text_frame = self.text_field.frame()
            new_text_frame = NSMakeRect(
                text_frame.origin.x,
                8,
                text_frame.size.width,
                min_card_height - 16
            )
            self.text_field.setFrame_(new_text_frame)

            # 重置麦克风区域位置
            mic_area_y = (min_card_height - self._mic_area_size) / 2

            ring_outer_x = 12 + (self._mic_area_size - self._ring_outer_size) / 2
            ring_outer_y = mic_area_y + (self._mic_area_size - self._ring_outer_size) / 2
            self._ring_outer.setFrameOrigin_((ring_outer_x, ring_outer_y))

            ring_inner_x = 12 + (self._mic_area_size - self._ring_inner_size) / 2
            ring_inner_y = mic_area_y + (self._mic_area_size - self._ring_inner_size) / 2
            self._ring_inner.setFrameOrigin_((ring_inner_x, ring_inner_y))

            mic_core_size = 20
            mic_core_x = 12 + (self._mic_area_size - mic_core_size) / 2
            mic_core_y = mic_area_y + (self._mic_area_size - mic_core_size) / 2
            self._mic_core.setFrameOrigin_((mic_core_x, mic_core_y))

            # 重置窗口高度
            window_frame = self.window.frame()
            new_window_height = min_card_height + self._shadow_padding * 2
            new_window_frame = NSMakeRect(
                window_frame.origin.x,
                window_frame.origin.y,
                window_frame.size.width,
                new_window_height
            )
            self.window.setFrame_display_(new_window_frame, False)

            self._card_height = min_card_height

        except Exception as e:
            import logging
            logging.getLogger(__name__).debug(f"重置高度失败: {e}")

    def process_pending(self):
        """处理待执行的操作（兼容旧模式）"""
        if not hasattr(self, '_pending_action') or self._pending_action is None:
            return

        action, text = self._pending_action
        self._pending_action = None

        if action == 'show':
            self._do_show(text)
        elif action == 'update':
            self._do_update(text)
        elif action == 'hide':
            self._do_hide()


class VoiceMemoApp:
    """语音输入应用"""

    def __init__(self):
        self.status_bar: Optional[StatusBar] = None
        self.asr_client: Optional[ASRClient] = None
        self.recorder: Optional[AudioRecorder] = None
        self.key_listener: Optional[keyboard.Listener] = None

        self.is_recording = False
        self.is_option_pressed = False
        self._lock = threading.Lock()
        self.current_text = ""
        self.committed_text = ""  # 已确认的历史文本
        self.saved_clipboard = ""

    def run(self):
        """启动应用"""
        # 验证配置
        valid, error = config.validate_config()
        if not valid:
            print(f"配置错误: {error}")
            print("请复制 .env.example 为 .env 并填入你的 API 密钥")
            return

        # 创建状态条并初始化 NSApplication
        self.status_bar = StatusBar()
        self.status_bar.init_app()

        # 启动键盘监听
        self.key_listener = keyboard.Listener(
            on_press=self._on_key_press,
            on_release=self._on_key_release
        )
        self.key_listener.start()

        print("=" * 50)
        print("语音输入法已启动！")
        print()
        print("使用方法：")
        print("  按住 Option 键开始录音")
        print("  说话时实时显示识别结果")
        print("  松开 Option 键自动输入到光标位置")
        print()
        print("（Ctrl+C 退出）")
        print("=" * 50)

        # 运行主循环，使用 CFRunLoop 处理 Cocoa 事件
        try:
            from Foundation import NSRunLoop, NSDate
            while True:
                # 处理 Cocoa 事件（20ms 间隔，降低 UI 延迟）
                NSRunLoop.currentRunLoop().runMode_beforeDate_(
                    'kCFRunLoopDefaultMode',
                    NSDate.dateWithTimeIntervalSinceNow_(0.02)
                )
                # 处理待执行的 UI 操作
                self.status_bar.process_pending()
        except KeyboardInterrupt:
            pass

    def _on_key_press(self, key):
        """按键按下"""
        # 检测 Option 键
        if key == keyboard.Key.alt or key == keyboard.Key.alt_l or key == keyboard.Key.alt_r:
            logger.info("Option 键按下")
            if not self.is_option_pressed:
                self.is_option_pressed = True
                self._start_recording()

    def _on_key_release(self, key):
        """按键松开"""
        if key == keyboard.Key.alt or key == keyboard.Key.alt_l or key == keyboard.Key.alt_r:
            if self.is_option_pressed:
                self.is_option_pressed = False
                self._stop_recording()

    def _start_recording(self):
        """开始录音"""
        with self._lock:
            if self.is_recording:
                return
            self.is_recording = True

        self.current_text = ""
        self.committed_text = ""

        # 保存当前剪贴板
        self.saved_clipboard = get_clipboard() or ""

        # 显示状态条
        self.status_bar.show("🎤 正在连接...")

        # 后台启动
        threading.Thread(target=self._connect_and_record, daemon=True).start()

    def _connect_and_record(self):
        """连接 ASR 并录音"""
        logger.info("开始连接 ASR...")

        self.asr_client = ASRClient(
            on_result=self._on_asr_result,
            on_error=self._on_asr_error
        )

        success, error = self.asr_client.connect()
        if not success:
            logger.error(f"ASR 连接失败: {error}")
            self.status_bar.update(f"❌ 连接失败")
            time.sleep(1)
            self._reset()
            return

        if not self.is_recording:
            self.asr_client.close()
            return

        # 启动录音
        self.recorder = AudioRecorder(
            on_audio=self._on_audio_data,
            on_error=self._on_recorder_error
        )

        success, error = self.recorder.start()
        if not success:
            logger.error(f"录音失败: {error}")
            self.status_bar.update(f"❌ 录音失败")
            time.sleep(1)
            self._reset()
            return

        logger.info("录音已启动")
        self.status_bar.update("🎤 请说话...")

    def _stop_recording(self):
        """停止录音"""
        with self._lock:
            if not self.is_recording:
                return
            self.is_recording = False

        # 停止录音
        if self.recorder:
            self.recorder.stop()
            self.recorder = None

        # 发送最后一包
        if self.asr_client:
            self.asr_client.send_audio(b'', is_last=True)

        # 等待最后结果
        time.sleep(0.3)

        # 关闭 ASR
        if self.asr_client:
            self.asr_client.close()
            self.asr_client = None

        # 隐藏状态条
        self.status_bar.hide()

        # 输入文本
        full_text = self.committed_text + self.current_text
        if full_text:
            self._do_input(full_text)

    def _do_input(self, text: str):
        """输入文本到当前位置"""
        logger.info(f"输入文本: {text}")

        # 使用剪贴板粘贴
        success, error = type_text(text, restore_clipboard=False)

        if success:
            logger.info("输入成功")
            # 恢复原剪贴板（延迟执行）
            if self.saved_clipboard:
                threading.Timer(0.5, lambda: set_clipboard(self.saved_clipboard)).start()
        else:
            logger.warning(f"输入失败: {error}")
            self.status_bar.show(f"❌ 输入失败，文本已复制")
            time.sleep(2)
            self.status_bar.hide()

    def _reset(self):
        """重置状态"""
        self.is_recording = False
        if self.recorder:
            self.recorder.stop()
            self.recorder = None
        if self.asr_client:
            self.asr_client.close()
            self.asr_client = None
        self.status_bar.hide()

    def _on_audio_data(self, data: bytes):
        """音频数据"""
        if self.asr_client and self.is_recording:
            self.asr_client.send_audio(data)

    def _on_asr_result(self, text: str, is_definite: bool):
        """识别结果"""
        logger.debug(f"识别: '{text}' (definite={is_definite})")
        
        # 豆包流式 ASR 如果开启了分句，text 字段通常是当前句子的内容
        # 当 is_definite=True 时，表示这句话结束，下一帧 text 可能会重置
        # 因此我们需要累积结果
        
        if is_definite:
            # 句子结束，追加到历史记录
            self.committed_text += text
            self.current_text = ""  # 清空当前正在变的文本
            display_text = self.committed_text
        else:
            # 句子未结束，更新当前文本
            self.current_text = text
            display_text = self.committed_text + self.current_text
            
        # 实时更新显示
        if display_text:
            display = f"🎤 {display_text}"
            self.status_bar.update(display)

    def _on_asr_error(self, error: str):
        """ASR 错误"""
        logger.error(f"ASR 错误: {error}")

    def _on_recorder_error(self, error: str):
        """录音错误"""
        logger.error(f"录音错误: {error}")
        self._reset()


def main():
    """入口"""
    app = VoiceMemoApp()
    app.run()


if __name__ == "__main__":
    main()
