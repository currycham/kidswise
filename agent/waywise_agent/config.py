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


@lru_cache
def get_settings() -> Settings:
    return Settings()
