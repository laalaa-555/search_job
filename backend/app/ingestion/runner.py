"""Isolated source process. stdout is a validated JSON-lines protocol."""
import argparse
import asyncio
import json
import logging
import sys

from app.config import get_settings
from app.ingestion.connectors import make_connector
from app.ingestion.http import SourceError
from app.ingestion.linkedin_details import detail_candidates, fetch_detail
from app.db import SessionLocal
from app.ingestion.progress import BATCH_SOURCES, next_keyword, progress_rows
from app.models import CollectionCycle, SourceState


def emit(event):
    print(json.dumps(event, ensure_ascii=False), flush=True)


async def acknowledge(expected):
    acknowledgement = await asyncio.to_thread(sys.stdin.readline)
    if acknowledgement.strip() != expected:
        raise SourceError("Worker 未確認資料寫入，停止後續收集", "protocol_error")


def cycle_snapshot(source):
    with SessionLocal() as db:
        state = db.get(SourceState, source)
        if state is None or state.current_cycle_id is None:
            raise SourceError("Worker 尚未建立收集週期", "protocol_error")
        cycle = db.get(CollectionCycle, state.current_cycle_id)
        rows = progress_rows(db, cycle.id)
        # Sessions use expire_on_commit=False; attributes are loaded before detaching.
        return cycle, rows


async def collect(source: str):
    settings = get_settings().for_source(source)
    connector = make_connector(source, settings)
    seen = set()
    errors = []
    completed = 0
    reason_code = None
    cooldown_seconds = 0
    cycle_complete = True
    batched = source in BATCH_SOURCES
    try:
        if batched:
            for _ in range(settings.batch_pages_per_run):
                cycle, rows = cycle_snapshot(source)
                row = next_keyword(cycle, rows)
                if row is None:
                    break
                try:
                    page = await connector.collect_page(row.keyword, row.next_page, row.next_offset)
                    emit({"type": "page", "cycle_id": cycle.id, "keyword_index": row.keyword_index,
                          "page": row.next_page, "jobs": [job.finalize().model_dump(mode="json") for job in page.jobs],
                          "raw_ids": page.raw_ids, "has_more": page.has_more, "next_offset": page.next_offset})
                    await acknowledge("page_saved")
                    await asyncio.sleep(settings.request_delay_seconds)
                except Exception as exc:
                    emit({"type": "page_failed", "cycle_id": cycle.id, "keyword_index": row.keyword_index, "page": row.next_page})
                    await acknowledge("page_saved")
                    errors.append(f"{row.keyword}：" + (str(exc)[:180] if isinstance(exc, (ValueError, RuntimeError)) else type(exc).__name__))
                    reason_code = exc.code if isinstance(exc, SourceError) else "format_changed" if isinstance(exc, (ValueError, KeyError, TypeError)) else "source_error"
                    cooldown_seconds = getattr(exc, "cooldown_seconds", 0)
                    break
            cycle, rows = cycle_snapshot(source)
            completed = sum(row.completed_at is not None for row in rows)
            cycle_complete = cycle.completed_at is not None
            connector.truncated = cycle.truncated
            seen = {identity for row in rows for identity in row.seen_ids}
        for keyword in ([] if batched else settings.keywords):
            try:
                async for job in connector.collect(keyword):
                    job.finalize()
                    if job.external_id not in seen:
                        seen.add(job.external_id)
                        emit({"type": "job", "job": job.model_dump(mode="json")})
                completed += 1
            except Exception as exc:
                logging.exception("Source keyword failed: %s", source)
                # Do not echo response bodies, cookies or proxy credentials.
                error = str(exc)[:180] if isinstance(exc, (ValueError, RuntimeError)) else type(exc).__name__
                errors.append(f"{keyword}：{error}")
                reason_code = exc.code if isinstance(exc, SourceError) else "format_changed" if isinstance(exc, ValueError) else "source_error"
                cooldown_seconds = max(cooldown_seconds, getattr(exc, "cooldown_seconds", 0))
                if reason_code in ("access_denied", "rate_limited", "verification_required", "loading_timeout", "format_changed", "pagination_repeated"):
                    break
            emit({"type": "progress", "completed": completed})
            await asyncio.sleep(settings.request_delay_seconds)
        if source == "linkedin" and cycle_complete and not errors and settings.linkedin_detail_limit and seen:
            # Parent acknowledges only after all preceding list writes are committed.
            emit({"type": "lists_finished"})
            await acknowledge("lists_saved")
            with SessionLocal() as db:
                candidates = detail_candidates(db, seen, settings)
            for incoming in candidates:
                try:
                    result = await fetch_detail(connector.http, incoming)
                    emit({"type": "detail", "job": result.model_dump(mode="json")})
                except Exception as exc:
                    errors.append("詳情：" + (str(exc)[:180] if isinstance(exc, SourceError) else type(exc).__name__))
                    reason_code = exc.code if isinstance(exc, SourceError) else "source_error"
                    cooldown_seconds = max(cooldown_seconds, getattr(exc, "cooldown_seconds", 0))
                    # Failed detail parsing/access stops this phase; lists stay saved.
                    break
    finally:
        await connector.close()
    status = ("success" if cycle_complete else "batch_complete") if not errors else ("partial" if seen else "error")
    emit({"type": "finished", "status": status, "count": len(seen), "completed": completed, "truncated": connector.truncated, "error": "；".join(errors)[:2000] or None, "reason_code": reason_code, "cooldown_seconds": cooldown_seconds})


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    args = parser.parse_args()
    asyncio.run(collect(args.source))
