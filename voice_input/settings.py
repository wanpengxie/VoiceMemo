"""
配置持久化存储
使用 macOS UserDefaults (NSUserDefaults) 存储 API 密钥
"""

from Foundation import NSUserDefaults

# Bundle ID
BUNDLE_ID = "com.doubao.voiceinput"

# 配置键名
KEY_APP_KEY = "app_key"
KEY_ACCESS_KEY = "access_key"
KEY_RESOURCE_ID = "resource_id"

# 默认值
DEFAULT_RESOURCE_ID = "volc.bigasr.sauc.duration"


class Settings:
    """配置管理器"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._defaults = NSUserDefaults.standardUserDefaults()
        return cls._instance

    @property
    def app_key(self) -> str:
        """获取 App Key"""
        return self._defaults.stringForKey_(KEY_APP_KEY) or ""

    @app_key.setter
    def app_key(self, value: str):
        """设置 App Key"""
        self._defaults.setObject_forKey_(value, KEY_APP_KEY)
        self._defaults.synchronize()

    @property
    def access_key(self) -> str:
        """获取 Access Key"""
        return self._defaults.stringForKey_(KEY_ACCESS_KEY) or ""

    @access_key.setter
    def access_key(self, value: str):
        """设置 Access Key"""
        self._defaults.setObject_forKey_(value, KEY_ACCESS_KEY)
        self._defaults.synchronize()

    @property
    def resource_id(self) -> str:
        """获取 Resource ID"""
        return self._defaults.stringForKey_(KEY_RESOURCE_ID) or DEFAULT_RESOURCE_ID

    @resource_id.setter
    def resource_id(self, value: str):
        """设置 Resource ID"""
        self._defaults.setObject_forKey_(value, KEY_RESOURCE_ID)
        self._defaults.synchronize()

    def is_configured(self) -> bool:
        """检查是否已配置必要的密钥"""
        return bool(self.app_key and self.access_key)

    def validate(self) -> tuple[bool, str]:
        """
        验证配置是否完整
        Returns:
            (是否有效, 错误信息)
        """
        if not self.app_key:
            return False, "缺少 App Key"
        if not self.access_key:
            return False, "缺少 Access Key"
        return True, ""


# 全局单例
settings = Settings()
