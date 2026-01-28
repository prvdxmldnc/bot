from pydantic import BaseModel
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    bot_token: str = ""
    database_url: str = "postgresql+asyncpg://bot:bot@db:5432/bot"
    redis_url: str = "redis://redis:6379/0"
    admin_phone: str = "+89047678710"
    manager_phone: str = "+7999999999"
    secret_key: str = "change-me"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    gigachat_api_key: str = ""
    gigachat_model: str = "GigaChat"
    gigachat_base_url: str = "https://gigachat.devices.sberbank.ru/api/v1"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
