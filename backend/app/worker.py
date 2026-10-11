import argparse
import asyncio
from contextlib import suppress
from datetime import timedelta
import json
import logging
import signal
import sys
import uuid

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.db import SessionLocal
from app.ingestion.normalize import NormalizedJob
from app.ingestion.store import upsert_job
from app.models import CollectionRun, SourceState, WorkerLock, utcnow

log = logging.getLogger("jobfinder.worker")


class Lease:
    def __init__(self, sessions=None, seconds=120):
        self.sessions = sessions if sessions is not None else SessionLocal
        self.seconds = seconds
        self.owner = str(uuid.uuid4())

    def acquire(self):
        with self.sessions() as db:
            now = utcnow()
            result = db.execute(update(WorkerLock).where(WorkerLock.name == "collector", WorkerLock.expires_at <= now).values(owner=self.owner, expires_at=now + timedelta(seconds=self.seconds)))
            if result.rowcount:
                db.commit()
                return True
            if db.get(WorkerLock, "collector"):
                db.rollback()
                return False
            db.add(WorkerLock(name="collector", owner=self.owner, expires_at=now + timedelta(seconds=self.seconds)))
            try:
                db.commit()
                return True
            except IntegrityError:
                db.rollback()
                return False

    def renew(self):
        with self.sessions() as db:
            result = db.execute(update(WorkerLock).where(WorkerLock.name == "collector", WorkerLock.owner == self.owner).values(expires_at=utcnow() + timedelta(seconds=self.seconds)))
            db.commit()
            return bool(result.rowcount)

    def release(self):
        with self.sessions() as db:
            db.execute(delete(WorkerLock).where(WorkerLock.name == "collector", WorkerLock.owner == self.owner))
            db.commit()


async def run_source(source, trigger, settings, semaphore):
    async with semaphore:
        with SessionLocal() as db:
            state = db.get(SourceState, source)
            if state is None:
                state = SourceState(source=source)
                db.add(state)
            if state.next_retry_at and state.next_retry_at.replace(tzinfo=utcnow().tzinfo) > utcnow():
                log.info("%s cooling down until %s; skipped", source, state.next_retry_at)
                return
            state.status, state.error, state.truncated = "running", None, False
            state.reason_code, state.next_retry_at = None, None
            state.last_started_at = utcnow()
            state.collected_count, state.keywords_completed, state.keywords_total = 0, 0, len(settings.keywords)
            run = CollectionRun(source=source, trigger=trigger)
            db.add(run)
            db.commit()
            run_id = run.id
        log.info("Starting %s (%s)", source, trigger)
        process = None
        finished = None
        seen = set()
        try:
            process = await asyncio.create_subprocess_exec(sys.executable, "-m", "app.ingestion.runner", "--source", source, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, limit=1024 * 1024)

            async def stderr_log():
                # Drain to avoid deadlocks; only log bounded diagnostic lines.
                while line := await process.stderr.readline():
                    log.warning("%s: %s", source, line.decode("utf-8", "replace").strip()[:500])

            drain = asyncio.create_task(stderr_log())
            try:
                async with asyncio.timeout(settings.for_source(source).source_timeout_seconds):
                    while line := await process.stdout.readline():
                        event = json.loads(line.decode("utf-8"))
                        with SessionLocal() as db:
                            state = db.get(SourceState, source)
                            if event["type"] in ("job", "detail"):
                                job = NormalizedJob.model_validate(event["job"])
                                if job.source != source:
                                    raise ValueError("Source process emitted a different source")
                                upsert_job(db, job)
                                if event["type"] == "job":
                                    seen.add(job.external_id)
                                    state.collected_count = len(seen)
                                state.last_data_at = utcnow()
                            elif event["type"] == "lists_finished":
                                process.stdin.write(b"lists_saved\n")
                                await process.stdin.drain()
                            elif event["type"] == "progress":
                                state.keywords_completed = event["completed"]
                            elif event["type"] == "finished":
                                finished = event
                            db.commit()
                    code = await process.wait()
                    if code or not finished:
                        raise RuntimeError("來源程序未正常完成")
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.wait()
                await drain
        except asyncio.CancelledError:
            finished = {"status": "interrupted", "error": "收集已中斷，已寫入資料保留"}
            raise
        except Exception as exc:
            finished = {"status": "partial" if seen else "error", "error": "來源執行逾時" if isinstance(exc, TimeoutError) else str(exc)[:250]}
            log.warning("%s failed: %s", source, finished["error"])
        finally:
            if process and process.returncode is None:
                process.kill()
                await process.wait()
            with SessionLocal() as db:
                state = db.get(SourceState, source)
                run = db.get(CollectionRun, run_id)
                state.status = run.status = (finished or {}).get("status", "interrupted")
                state.error = run.error = (finished or {}).get("error")
                state.reason_code = (finished or {}).get("reason_code")
                cooldown = (finished or {}).get("cooldown_seconds", 0)
                if cooldown:
                    state.next_retry_at = utcnow() + timedelta(seconds=cooldown)
                state.last_finished_at = run.finished_at = utcnow()
                state.collected_count = run.collected_count = len(seen)
                state.truncated = (finished or {}).get("truncated", False)
                state.keywords_completed = (finished or {}).get("completed", state.keywords_completed)
                if state.status == "success":
                    state.last_success_at = utcnow()
                db.commit()
        log.info("Finished %s: %s, %d jobs", source, state.status, len(seen))


async def collect_all(trigger="manual"):
    settings = get_settings()
    lease = Lease(seconds=settings.worker_lock_seconds)
    if not lease.acquire():
        log.info("Another worker owns the lease; collection skipped")
        return False

    async def heartbeat():
        while True:
            await asyncio.sleep(settings.worker_lock_seconds / 3)
            if not lease.renew():
                raise RuntimeError("Worker lease lost")

    beat = asyncio.create_task(heartbeat())
    semaphore = asyncio.Semaphore(2)
    collection = asyncio.ensure_future(asyncio.gather(*(run_source(source, trigger, settings, semaphore) for source in settings.sources)))
    try:
        done, _ = await asyncio.wait([beat, collection], return_when=asyncio.FIRST_COMPLETED)
        if beat in done:
            collection.cancel()
            await beat
        await collection
        return True
    finally:
        collection.cancel()
        beat.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await collection
        with suppress(asyncio.CancelledError, Exception):
            await beat
        lease.release()


def schedule_trigger(settings):
    return CronTrigger(hour=settings.schedule_hour, minute=settings.schedule_minute, timezone=settings.schedule_timezone)


async def serve():
    settings = get_settings()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)
    scheduler = AsyncIOScheduler(timezone=settings.schedule_timezone)
    scheduler.add_job(collect_all, trigger=schedule_trigger(settings), args=["scheduled"], max_instances=1, coalesce=True, misfire_grace_time=60)
    scheduler.start()
    log.info("Worker ready; daily schedule %02d:%02d %s", settings.schedule_hour, settings.schedule_minute, settings.schedule_timezone)
    startup = asyncio.create_task(collect_all("startup")) if settings.collect_on_startup else None
    try:
        await stop.wait()
    finally:
        scheduler.shutdown(wait=False)
        if startup:
            startup.cancel()
            with suppress(asyncio.CancelledError):
                await startup


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--source", help="Override enabled sources for a one-off collection")
    args = parser.parse_args()
    if args.source:
        get_settings().enabled_sources = args.source
        # Child processes also need the override.
        import os
        os.environ["ENABLED_SOURCES"] = args.source
    asyncio.run(collect_all() if args.once else serve())
