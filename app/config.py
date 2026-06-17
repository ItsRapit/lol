from functools import lru_cache
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    bot_token: str
    bot_username: str = ""
    owner_admin_ids: str = ""
    admin_review_channel_id: int | None = None
    reports_channel_id: int | None = None
    database_path: str = "./data/quiz_duel.sqlite3"
    log_level: str = "INFO"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def owner_ids(self) -> set[int]:
        ids: set[int] = set()
        for part in self.owner_admin_ids.split(","):
            part = part.strip()
            if part:
                ids.add(int(part))
        return ids

    def ensure_data_dir(self) -> None:
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
