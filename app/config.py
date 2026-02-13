from pydantic import BaseModel
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    bot_token: str = ""
    database_url: str = "postgresql+asyncpg://bot:bot@db:5432/bot"
    redis_url: str = "redis://redis:6379/0"
    admin_phone: str = "+89047678710"
    admin_tg_id: int = 0
    admin_tg_username: str = ""
    manager_phone: str = "+7999999999"
    secret_key: str = "change-me"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    llm_provider: str = "ollama"
    llm_enabled: bool = False
    ollama_base_url: str = "http://ollama:11434"
    ollama_model: str = "qwen2.5:1.5b-instruct"
    llm_timeout_seconds: int = 30
    ollama_num_predict: int = 96
    ollama_num_ctx: int = 1024
    ollama_keep_alive: str = "10m"
    gigachat_oauth_url: str = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
    gigachat_basic_auth_key: str = ""
    gigachat_scope: str = "GIGACHAT_API_PERS"
    gigachat_api_base_url: str = "https://gigachat.devices.sberbank.ru/api/v1"
    gigachat_model: str = "GigaChat"
    gigachat_timeout_seconds: int = 20
    gigachat_token_cache_prefix: str = "gigachat:token"
    gigachat_ca_bundle: str = ""
    one_c_enabled: bool = False
    one_c_base_url: str = ""
    one_c_username: str = ""
    one_c_password: str = ""
    one_c_sync_interval_minutes: int = 10
    one_c_webhook_token: str = ""

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
