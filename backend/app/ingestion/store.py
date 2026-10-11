from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingestion.normalize import NormalizedJob
from app.models import Job, utcnow


def upsert_job(db: Session, incoming: NormalizedJob) -> Job:
    incoming.finalize()
    values = incoming.storage_values()
    job = db.scalar(select(Job).where(Job.source == incoming.source, Job.external_id == incoming.external_id))
    now = utcnow()
    if job is None:
        job = Job(**values, first_seen_at=now, last_seen_at=now, updated_at=now)
        db.add(job)
    else:
        if job.content_hash != values["content_hash"]:
            for key, value in values.items():
                setattr(job, key, value)
            job.updated_at = now
        job.last_seen_at = now
    db.commit()
    return job
