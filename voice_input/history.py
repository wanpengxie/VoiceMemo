"""
输入历史记录管理
- 全量历史保存到 history_archive.jsonl（追加写入，无限保存）
- 最近 100 条缓存到 history.json（快速读取）
- 持久化存储到 ~/Library/Application Support/VoiceFlow/
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
HISTORY_FILE = APP_SUPPORT_DIR / "history.json"           # 最近 100 条，快速读取
ARCHIVE_FILE = APP_SUPPORT_DIR / "history_archive.jsonl"  # 全量归档，追加写入

# 历史记录限制
MAX_HISTORY_COUNT = 100  # 内存中保留的最大数量
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
        self._migrate_to_archive()  # 首次运行时迁移旧数据到归档
        self._load()

    def _ensure_dir(self):
        """确保目录存在"""
        APP_SUPPORT_DIR.mkdir(parents=True, exist_ok=True)

    def _migrate_to_archive(self):
        """将旧的 history.json 数据迁移到归档文件（仅首次运行）"""
        # 如果归档文件已存在，说明已经迁移过
        if ARCHIVE_FILE.exists():
            return

        # 如果 history.json 不存在，无需迁移
        if not HISTORY_FILE.exists():
            return

        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)

            if not data:
                return

            # 按时间顺序（从旧到新）写入归档文件
            # history.json 是最新在前，所以需要反转
            with open(ARCHIVE_FILE, "w", encoding="utf-8") as f:
                for item in reversed(data):
                    f.write(json.dumps(item, ensure_ascii=False) + "\n")

            logger.info(f"已迁移 {len(data)} 条历史记录到归档文件")
        except Exception as e:
            logger.error(f"迁移历史记录失败: {e}")

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

        # 追加到归档文件（无限保存）
        self._append_to_archive(item)

        # 插入到内存开头
        self._history.insert(0, item)

        # 限制内存中的数量
        if len(self._history) > MAX_HISTORY_COUNT:
            self._history = self._history[:MAX_HISTORY_COUNT]

        # 保存最近 100 条到快速读取文件
        self._save()
        logger.info(f"保存历史记录: {item.get_display_text()}")

    def _append_to_archive(self, item: HistoryItem) -> None:
        """追加到归档文件（JSONL 格式，每行一条记录）"""
        try:
            with open(ARCHIVE_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(item), ensure_ascii=False) + "\n")
        except Exception as e:
            logger.error(f"追加归档失败: {e}")

    def get_recent(self, count: int = RECENT_COUNT) -> List[HistoryItem]:
        """获取最近的记录"""
        return self._history[:count]

    def get_all(self) -> List[HistoryItem]:
        """获取内存中的所有记录（最近 100 条）"""
        return self._history.copy()

    def get_all_from_archive(self) -> List[HistoryItem]:
        """从归档文件读取全部历史记录（按时间倒序，最新在前）"""
        if not ARCHIVE_FILE.exists():
            return self._history.copy()

        try:
            items = []
            with open(ARCHIVE_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        data = json.loads(line)
                        items.append(HistoryItem(
                            text=data["text"],
                            timestamp=data["timestamp"]
                        ))
            # 归档文件是按时间顺序（旧到新），返回时反转为最新在前
            items.reverse()
            return items
        except Exception as e:
            logger.error(f"读取归档文件失败: {e}")
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
