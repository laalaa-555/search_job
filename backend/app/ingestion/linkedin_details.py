"""Bounded second phase, after every LinkedIn list keyword has completed."""
from datetime import timedelta

from bs4 import BeautifulSoup
from sqlalchemy import select

from app.ingestion.http import SourceError
from app.ingestion.normalize import NormalizedJob, parse_salary
from app.models import Job, utcnow


def detail_candidates(db, seen, settings):
    if not seen or not settings.linkedin_detail_limit:
        return []
    cutoff = utcnow() - timedelta(days=settings.linkedin_detail_refresh_days)
    statement = select(Job).where(Job.source == "linkedin", Job.external_id.in_(seen), (Job.detail_collected_at.is_(None)) | (Job.detail_collected_at < cutoff)).order_by(Job.detail_collected_at.is_not(None), Job.detail_collected_at, Job.id).limit(settings.linkedin_detail_limit)
    return [NormalizedJob(**{name: getattr(job, name) for name in NormalizedJob.model_fields if hasattr(job, name)}, update_mode="details") for job in db.scalars(statement)]


async def fetch_detail(client, incoming):
    response = await client.get(incoming.url)
    if not str(response.url).split("?")[0].startswith("https://www.linkedin.com/jobs/view/"):
        raise SourceError("LinkedIn 詳情轉向登入或驗證頁", "verification_required", client.settings.blocked_cooldown_seconds)
    soup = BeautifulSoup(response.text, "html.parser")
    description = soup.select_one(".show-more-less-html__markup")
    if not description or not description.get_text(strip=True):
        raise SourceError("LinkedIn 詳情頁缺少可辨識描述", "format_changed")
    incoming.description = description.get_text(" ", strip=True)
    salary = soup.select_one(".compensation__salary")
    if salary:
        raw = salary.get_text(" ", strip=True)
        # Reuse the pinned library's currency/period parser, while preserving raw text.
        from jobspy.linkedin import LinkedIn
        compensation = LinkedIn._parse_salary(raw)
        values = parse_salary(raw)
        if compensation:
            values.update(salary_currency=compensation.currency, salary_period=compensation.interval.value, salary_min=compensation.min_amount, salary_max=compensation.max_amount)
            values["salary_period"] = {"yearly": "year", "monthly": "month", "hourly": "hour", "daily": "day", "weekly": "week"}.get(values["salary_period"])
        for name, value in values.items():
            setattr(incoming, name, value)
    incoming.detail_status = "complete"
    incoming.detail_collected_at = utcnow()
    return incoming.finalize()
