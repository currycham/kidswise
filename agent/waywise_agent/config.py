from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    agent_api_key: str = "change-me"
    agent_model_name: str = "mumi-teacher"

    llm_provider: str = "mock"  # "mock" | "openai"
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_timeout_s: float = 8.0
    # 回复长度的硬上限：prompt 管不住时兜底，防止一轮说十几秒
    llm_max_tokens: int = 150

    llm_fallback_base_url: str = ""
    llm_fallback_api_key: str = ""
    llm_fallback_model: str = ""

    default_child_name: str = "小朋友"
    default_child_age: int = 5
    default_theme_id: str = "park"

    # 存储（SQLite 文件，容器里挂卷）；region 写进每一行，按区域可分离
    db_path: str = "state/waywise.db"
    region: str = "cn"
    # 同一设备空闲多久算新的一场；一场安静多久后抽取跨会话记忆
    session_idle_s: float = 600
    memory_idle_s: float = 20

    # 家长端小程序
    wx_appid: str = ""
    wx_appsecret: str = ""
    auth_secret: str = ""  # 留空则由 AGENT_API_KEY 派生
    dev_login: bool = False  # 联调用免微信登录，演示环境必须关
    timezone: str = "Asia/Shanghai"


@lru_cache
def get_settings() -> Settings:
    return Settings()
