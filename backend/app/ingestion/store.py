from sqlalchemy import select, or_
from urllib.parse import urlsplit
from sqlalchemy.orm import Session

from app.ingestion.normalize import NormalizedJob
from app.models import Job, utcnow


def upsert_job(db: Session, incoming: NormalizedJob, *, commit: bool = True) -> Job:
    job = db.scalar(select(Job).where(Job.source == incoming.source, Job.external_id == incoming.external_id))
    if job is None and incoming.source == "104":
        # DOM URLs use a short slug while JSON can carry a numeric jobNo.
        slug = urlsplit(incoming.url).path.rstrip("/").split("/")[-1]
        job = db.scalar(select(Job).where(Job.source == "104", Job.url == incoming.url, or_(Job.external_id == slug, incoming.external_id == slug)))
        if job:
            incoming.external_id = job.external_id
    if job and incoming.update_mode in ("listing", "details"):
        # Missing list/detail fields are not instructions to erase earlier data.
        if not incoming.description:
            incoming.description = job.description
        incoming.skills = list(dict.fromkeys(job.skills + incoming.skills))
        if incoming.salary_text == "薪資未提供":
            for key in ("salary_text", "salary_currency", "salary_period", "salary_min", "salary_max"):
                setattr(incoming, key, getattr(job, key))
        if incoming.update_mode == "listing":
            incoming.detail_status = job.detail_status
            incoming.detail_collected_at = job.detail_collected_at
    incoming.finalize()
    values = incoming.storage_values()
    now = utcnow()
    if job is None:
        job = Job(**values, first_seen_at=now, last_seen_at=now, updated_at=now)
        db.add(job)
    else:
        if job.content_hash != values["content_hash"]:
            for key, value in values.items():
                setattr(job, key, value)
            job.updated_at = now
        # A detail refresh timestamp may change without changing the content hash.
        job.detail_collected_at = values["detail_collected_at"]
        if incoming.update_mode != "details":
            job.last_seen_at = now
    if commit:
        db.commit()
    else:
        db.flush()
    return job
