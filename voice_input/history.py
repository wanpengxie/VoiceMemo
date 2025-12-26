"""
输入历史记录管理
- 保存最近 100 条识别结果
- 持久化存储到 ~/Library/Application Support/VoiceFlow/history.json
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional
from dataclasses import dataclass, asdict

logger = logging.getLogger(__name__)

# 应用数据目录
APP_SUPPORT_DIR = Path.home() / "Library" / "Application Support" / "VoiceFlow"
HISTORY_FILE = APP_SUPPORT_DIR / "history.json"

# 历史记录限制
MAX_HISTORY_COUNT = 100
RECENT_COUNT = 10


@dataclass
class HistoryItem:
    """历史记录项"""
    text: str
    timestamp: str  # ISO 格式时间戳

    @classmethod
    def create(cls, text: str) -> "HistoryItem":
        """创建新的历史记录"""
        return cls(
            text=text,
            timestamp=datetime.now().isoformat()
        )

    def get_display_text(self, max_length: int = 30) -> str:
        """获取显示用的短文本"""
        text = self.text.replace("\n", " ").strip()
        if len(text) > max_length:
            return text[:max_length] + "..."
        return text

    def get_time_display(self) -> str:
        """获取时间显示"""
        try:
            dt = datetime.fromisoformat(self.timestamp)
            now = datetime.now()

            # 今天的显示时间
            if dt.date() == now.date():
                return dt.strftime("%H:%M")
            # 昨天
            elif (now.date() - dt.date()).days == 1:
                return "昨天 " + dt.strftime("%H:%M")
            # 今年内
            elif dt.year == now.year:
                return dt.strftime("%m-%d %H:%M")
            else:
                return dt.strftime("%Y-%m-%d %H:%M")
        except:
            return ""


class HistoryManager:
    """历史记录管理器"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._history: List[HistoryItem] = []
        self._ensure_dir()
        self._load()

    def _ensure_dir(self):
        """确保目录存在"""
        APP_SUPPORT_DIR.mkdir(parents=True, exist_ok=True)

    def _load(self):
        """从文件加载历史"""
        if not HISTORY_FILE.exists():
            self._history = []
            return

        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)

            self._history = [
                HistoryItem(text=item["text"], timestamp=item["timestamp"])
                for item in data
            ]
            logger.info(f"加载了 {len(self._history)} 条历史记录")
        except Exception as e:
            logger.error(f"加载历史记录失败: {e}")
            self._history = []

    def _save(self):
        """保存到文件"""
        try:
            data = [asdict(item) for item in self._history]
            with open(HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存历史记录失败: {e}")

    def add(self, text: str) -> None:
        """添加新的历史记录"""
        if not text or not text.strip():
            return

        # 创建新记录
        item = HistoryItem.create(text.strip())

        # 插入到开头
        self._history.insert(0, item)

        # 限制数量
        if len(self._history) > MAX_HISTORY_COUNT:
            self._history = self._history[:MAX_HISTORY_COUNT]

        # 保存
        self._save()
        logger.info(f"保存历史记录: {item.get_display_text()}")

    def get_recent(self, count: int = RECENT_COUNT) -> List[HistoryItem]:
        """获取最近的记录"""
        return self._history[:count]

    def get_all(self) -> List[HistoryItem]:
        """获取所有记录"""
        return self._history.copy()

    def get_by_index(self, index: int) -> Optional[HistoryItem]:
        """根据索引获取记录"""
        if 0 <= index < len(self._history):
            return self._history[index]
        return None

    def clear(self) -> None:
        """清空历史"""
        self._history = []
        self._save()
        logger.info("历史记录已清空")

    def count(self) -> int:
        """历史记录数量"""
        return len(self._history)


# 全局单例
history_manager = HistoryManager()
