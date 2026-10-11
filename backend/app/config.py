from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

SOURCES = {"104": "104", "1111": "1111", "cake": "Cake", "linkedin": "LinkedIn", "indeed": "Indeed"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=Path(__file__).resolve().parents[2] / ".env", extra="ignore")
    database_url: str = "sqlite:///./data/jobs.db"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    enabled_sources: str = "104,1111,cake,linkedin,indeed"
    collect_keywords: str = "Data Scientist,資料科學,Data Analyst,Data Engineer,Machine Learning,AI 工程師,前端工程師,後端工程師,全端工程師,軟體工程師,DevOps,SRE"
    collect_location: str = "Taiwan"
    collect_on_startup: bool = True
    schedule_timezone: str = "Asia/Taipei"
    schedule_hour: int = Field(default=6, ge=0, le=23)
    schedule_minute: int = Field(default=0, ge=0, le=59)
    max_results_per_keyword: int = Field(default=100, ge=1, le=1000)
    max_pages_per_keyword: int = Field(default=10, ge=1, le=100)
    request_delay_seconds: float = Field(default=2, ge=0)
    request_timeout_seconds: float = Field(default=30, gt=0)
    request_retries: int = Field(default=2, ge=0, le=5)
    source_timeout_seconds: float = Field(default=1800, gt=0)
    worker_lock_seconds: int = Field(default=120, ge=30)
    status_stale_days: int = Field(default=7, ge=1)

    @field_validator("enabled_sources")
    @classmethod
    def known_sources(cls, value: str) -> str:
        if set(split_csv(value)) - SOURCES.keys():
            raise ValueError("ENABLED_SOURCES contains an unknown source")
        return value

    @field_validator("schedule_timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        ZoneInfo(value)
        return value

    @property
    def sources(self) -> list[str]:
        return split_csv(self.enabled_sources)

    @property
    def keywords(self) -> list[str]:
        return split_csv(self.collect_keywords)


def split_csv(value: str) -> list[str]:
    return list(dict.fromkeys(part.strip() for part in value.split(",") if part.strip()))


@lru_cache
def get_settings() -> Settings:
    return Settings()
