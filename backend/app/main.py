from datetime import timedelta
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, or_, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import SOURCES, get_settings, split_csv
from app.db import get_db
from app.locations import CITY_ALIASES
from app.models import Bookmark, Job, SourceState, WorkerLock, utcnow
from app.schemas import JobDetail, JobOut, JobPage, SourceOut, SourcesOut

settings = get_settings()
app = FastAPI(title="My Job Finder", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=split_csv(settings.cors_origins), allow_methods=["GET", "PUT", "DELETE"], allow_headers=["Content-Type"])
Db = Annotated[Session, Depends(get_db)]


def serialize(job: Job, bookmarked: bool = False, detail: bool = False):
    result = (JobDetail if detail else JobOut).model_validate(job)
    result.bookmarked = bookmarked
    result.stale = job.last_seen_at.replace(tzinfo=utcnow().tzinfo) < utcnow() - timedelta(days=settings.status_stale_days)
    return result


def filtered_jobs(q: str, city: str, sources: list[str] | None, min_annual_salary: int | None, include_unknown_salary: bool):
    statement = select(Job).where(Job.is_active.is_(True))
    if q.strip():
        statement = statement.where(Job.search_text.contains(q.strip().lower().replace("臺", "台"), autoescape=True))
    if city:
        city = city.replace("臺", "台")
        location = func.lower(func.replace(Job.location, "臺", "台"))
        # Prevent New Taipei from matching Taipei, while accepting multiple locations.
        location = func.replace(location, "new taipei", "新北市")
        aliases = CITY_ALIASES.get(city, [])
        statement = statement.where(or_(Job.location_city == city, *(location.contains(alias.lower().replace("臺", "台"), autoescape=True) for alias in aliases)))
    if sources is not None:
        unknown = set(sources) - SOURCES.keys()
        if unknown:
            raise HTTPException(422, "未知資料來源")
        statement = statement.where(Job.source.in_(sources))
    if min_annual_salary is not None:
        condition = Job.annual_salary_min >= min_annual_salary
        if include_unknown_salary:
            condition = or_(condition, Job.annual_salary_min.is_(None))
        statement = statement.where(condition)
    elif not include_unknown_salary:
        statement = statement.where(Job.annual_salary_min.is_not(None))
    return statement


def paginate(statement, db: Session, page: int, page_size: int) -> JobPage:
    total = db.scalar(select(func.count()).select_from(statement.subquery())) or 0
    jobs = db.scalars(statement.order_by(Job.updated_at.desc(), Job.id.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    saved = set(db.scalars(select(Bookmark.job_id).where(Bookmark.job_id.in_([job.id for job in jobs]))))
    return JobPage(items=[serialize(job, job.id in saved) for job in jobs], total=total, page=page, page_size=page_size)


@app.get("/api/v1/jobs", response_model=JobPage)
def jobs(db: Db, q: str = Query("", max_length=200), city: str = Query("", max_length=100), sources: Annotated[list[str] | None, Query()] = None, min_annual_salary: int | None = Query(None, ge=0), include_unknown_salary: bool = True, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    return paginate(filtered_jobs(q, city, sources, min_annual_salary, include_unknown_salary), db, page, page_size)


@app.get("/api/v1/jobs/{job_id}", response_model=JobDetail)
def job_detail(job_id: int, db: Db):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "職缺不存在")
    return serialize(job, db.get(Bookmark, job_id) is not None, detail=True)


@app.get("/api/v1/bookmarks", response_model=JobPage)
def bookmarks(db: Db, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    return paginate(select(Job).join(Bookmark), db, page, page_size)


@app.put("/api/v1/bookmarks/{job_id}", status_code=204)
def save_bookmark(job_id: int, db: Db):
    if not db.get(Job, job_id):
        raise HTTPException(404, "職缺不存在")
    if not db.get(Bookmark, job_id):
        db.add(Bookmark(job_id=job_id))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
    return Response(status_code=204)


@app.delete("/api/v1/bookmarks/{job_id}", status_code=204)
def remove_bookmark(job_id: int, db: Db):
    bookmark = db.get(Bookmark, job_id)
    if bookmark:
        db.delete(bookmark)
        db.commit()
    return Response(status_code=204)


@app.get("/api/v1/sources", response_model=SourcesOut)
def sources_status(db: Db):
    states = {state.source: state for state in db.scalars(select(SourceState))}
    counts = dict(db.execute(select(Job.source, func.count(Job.id)).group_by(Job.source)).all())
    running = bool(db.scalar(select(WorkerLock.name).where(WorkerLock.expires_at > utcnow())))
    items = []
    for source, label in SOURCES.items():
        state = states.get(source)
        values = {key: getattr(state, key) for key in SourceOut.model_fields if state and hasattr(state, key)}
        enabled = source in settings.sources
        status = values.pop("status", "idle")
        if not enabled:
            status = "disabled"
        if status == "running" and not running:
            status = "interrupted"
        values.pop("source", None)
        items.append(SourceOut(source=source, label=label, enabled=enabled, status=status, stored_count=counts.get(source, 0), **values))
    return SourcesOut(items=items, keywords=settings.keywords, location=settings.collect_location, max_results_per_keyword=settings.max_results_per_keyword, schedule_timezone=settings.schedule_timezone, schedule_hour=settings.schedule_hour, schedule_minute=settings.schedule_minute, worker_running=running)


@app.get("/api/v1/health")
def health(db: Db):
    try:
        db.execute(text("SELECT 1"))
        db.execute(select(SourceState.source).limit(1))
    except SQLAlchemyError:
        raise HTTPException(503, "資料庫尚未就緒") from None
    return {"status": "ok", "database": "ok"}
