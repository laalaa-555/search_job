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
    source_request_delays: dict[str, float] = Field(default_factory=lambda: {"linkedin": 10})
    source_request_timeouts: dict[str, float] = Field(default_factory=dict)
    source_timeouts: dict[str, float] = Field(default_factory=lambda: {"linkedin": 3600})
    linkedin_detail_limit: int = Field(default=20, ge=0, le=100)
    linkedin_detail_refresh_days: int = Field(default=7, ge=1)
    rate_limit_cooldown_seconds: int = Field(default=3600, ge=3600)
    blocked_cooldown_seconds: int = Field(default=86400, ge=1)

    @field_validator("source_request_delays", "source_request_timeouts", "source_timeouts")
    @classmethod
    def valid_overrides(cls, value, info):
        import math
        if set(value) - SOURCES.keys() or any(not math.isfinite(n) or n < 0 or (n == 0 and info.field_name != "source_request_delays") for n in value.values()):
            raise ValueError("Source overrides must use known sources and valid durations")
        return value

    def for_source(self, source: str):
        return self.model_copy(update={
            "request_delay_seconds": self.source_request_delays.get(source, self.request_delay_seconds),
            "request_timeout_seconds": self.source_request_timeouts.get(source, self.request_timeout_seconds),
            "source_timeout_seconds": self.source_timeouts.get(source, self.source_timeout_seconds),
        })

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
