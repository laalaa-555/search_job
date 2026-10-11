"""Worker-only checkpoints. Caller commits jobs and progress in one transaction."""
import hashlib
import json

from sqlalchemy import select

from app.ingestion.http import SourceError
from app.ingestion.normalize import NormalizedJob
from app.ingestion.store import upsert_job
from app.models import CollectionCycle, KeywordProgress, utcnow

BATCH_SOURCES = {"linkedin", "cake"}


def prepare_cycle(db, state, settings):
    cycle = db.get(CollectionCycle, state.current_cycle_id) if state.current_cycle_id else None
    if cycle is None or cycle.scope_hash != settings.scope_hash(state.source) or cycle.completed_at:
        cycle = CollectionCycle(source=state.source, scope_hash=settings.scope_hash(state.source), scope={
            "keywords": settings.keywords, "location": settings.collect_location,
            "max_results": settings.max_results_per_keyword, "max_pages": settings.max_pages_per_keyword})
        db.add(cycle)
        db.flush()
        for index, keyword in enumerate(settings.keywords):
            db.add(KeywordProgress(cycle_id=cycle.id, keyword_index=index, keyword=keyword))
        state.current_cycle_id = cycle.id
        db.flush()
        if not settings.keywords:
            cycle.completed_at = utcnow()
    return cycle


def progress_rows(db, cycle_id):
    return list(db.scalars(select(KeywordProgress).where(KeywordProgress.cycle_id == cycle_id).order_by(KeywordProgress.keyword_index)))


def next_keyword(cycle, rows):
    return next((rows[(cycle.next_keyword_index + n) % len(rows)] for n in range(len(rows))
                 if rows[(cycle.next_keyword_index + n) % len(rows)].completed_at is None), None)


def checkpoint(db, state, event):
    cycle = db.get(CollectionCycle, state.current_cycle_id)
    if cycle is None or cycle.id != event["cycle_id"] or cycle.source != state.source or cycle.completed_at:
        raise ValueError("Invalid collection cycle")
    rows = progress_rows(db, cycle.id)
    row = next_keyword(cycle, rows)
    if row is None or row.keyword_index != event["keyword_index"] or row.next_page != event["page"]:
        raise ValueError("Out-of-order page checkpoint")
    if event["type"] == "page_failed":
        # Rotate the keyword without advancing its failed page.
        cycle.next_keyword_index = (row.keyword_index + 1) % len(rows)
        return []
    raw_ids = event["raw_ids"]
    signature = hashlib.sha256(json.dumps(sorted(set(raw_ids))).encode()).hexdigest()
    if raw_ids and signature in row.page_signatures:
        raise SourceError("來源分頁重複，保留原分頁進度", "pagination_repeated")
    jobs = [NormalizedJob.model_validate(value).finalize() for value in event["jobs"]]
    if any(job.source != state.source for job in jobs):
        raise ValueError("Page contains a different source")
    seen = set(row.seen_ids)
    saved = []
    for job in jobs:
        if job.external_id not in seen and len(seen) < cycle.scope["max_results"]:
            upsert_job(db, job, commit=False)
            seen.add(job.external_id)
            saved.append(job.external_id)
    row.seen_ids = sorted(seen)
    row.page_signatures = [*row.page_signatures, signature] if raw_ids else row.page_signatures
    row.next_page += 1
    row.next_offset = event["next_offset"]
    row.truncated = len(seen) >= cycle.scope["max_results"] or row.next_page > cycle.scope["max_pages"]
    if row.truncated or not event["has_more"]:
        row.completed_at = utcnow()
    cycle.next_keyword_index = (row.keyword_index + 1) % len(rows)
    cycle.truncated = any(item.truncated for item in rows)
    if all(item.completed_at for item in rows):
        cycle.completed_at = utcnow()
        state.last_cycle_completed_at = cycle.completed_at
        state.last_completed_scope_hash = cycle.scope_hash
    state.keywords_completed = sum(item.completed_at is not None for item in rows)
    state.truncated = cycle.truncated
    return saved
