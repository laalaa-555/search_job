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


def emit(event):
    print(json.dumps(event, ensure_ascii=False), flush=True)


async def collect(source: str):
    settings = get_settings().for_source(source)
    connector = make_connector(source, settings)
    seen = set()
    errors = []
    completed = 0
    reason_code = None
    cooldown_seconds = 0
    try:
        for keyword in settings.keywords:
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
        if source == "linkedin" and not errors and settings.linkedin_detail_limit and seen:
            # Parent acknowledges only after all preceding list writes are committed.
            emit({"type": "lists_finished"})
            acknowledgement = await asyncio.to_thread(sys.stdin.readline)
            if acknowledgement.strip() != "lists_saved":
                raise SourceError("Worker 未確認列表寫入，停止詳情收集", "protocol_error")
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
    status = "success" if not errors else ("partial" if seen else "error")
    emit({"type": "finished", "status": status, "count": len(seen), "completed": completed, "truncated": connector.truncated, "error": "；".join(errors)[:2000] or None, "reason_code": reason_code, "cooldown_seconds": cooldown_seconds})


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    args = parser.parse_args()
    asyncio.run(collect(args.source))
