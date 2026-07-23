from functools import lru_cache
from typing import Literal, Self

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

INSECURE_DEFAULT_SECRET = "local-development-only-change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: Literal["local", "test", "production"] = "local"
    app_debug: bool = False
    secret_key: str = INSECURE_DEFAULT_SECRET
    database_url: str = "postgresql+asyncpg://secure_lab:secure_lab_dev@127.0.0.1:5432/secure_lab"

    @model_validator(mode="after")
    def validate_production_secrets(self) -> Self:
        if self.app_env == "production" and (
            self.secret_key == INSECURE_DEFAULT_SECRET or len(self.secret_key) < 32
        ):
            raise ValueError("production SECRET_KEY must contain at least 32 characters")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
