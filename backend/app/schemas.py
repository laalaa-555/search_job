from datetime import datetime

from pydantic import BaseModel, ConfigDict


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    source: str
    title: str
    company: str
    location: str
    location_city: str
    url: str
    skills: list[str]
    salary_text: str
    salary_currency: str | None
    salary_period: str | None
    salary_min: float | None
    salary_max: float | None
    annual_salary_min: int | None
    annual_salary_max: int | None
    salary_estimated: bool
    is_active: bool
    last_seen_at: datetime
    updated_at: datetime
    bookmarked: bool = False
    stale: bool = False
    detail_status: str = "complete"
    detail_collected_at: datetime | None = None


class JobDetail(JobOut):
    description: str
    first_seen_at: datetime
    source_updated_at: datetime | None


class JobPage(BaseModel):
    items: list[JobOut]
    total: int
    page: int
    page_size: int


class SourceOut(BaseModel):
    source: str
    label: str
    enabled: bool
    status: str
    last_started_at: datetime | None = None
    last_success_at: datetime | None = None
    last_finished_at: datetime | None = None
    collected_count: int = 0
    stored_count: int = 0
    keywords_completed: int = 0
    keywords_total: int = 0
    error: str | None = None
    truncated: bool = False
    reason_code: str | None = None
    next_retry_at: datetime | None = None
    last_data_at: datetime | None = None


class SourcesOut(BaseModel):
    items: list[SourceOut]
    keywords: list[str]
    location: str
    max_results_per_keyword: int
    schedule_timezone: str
    schedule_hour: int
    schedule_minute: int
    worker_running: bool
