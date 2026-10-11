"""Isolated source process. stdout is a validated JSON-lines protocol."""
import argparse
import asyncio
import json
import logging
import sys

from app.config import get_settings
from app.ingestion.connectors import make_connector


def emit(event):
    print(json.dumps(event, ensure_ascii=False), flush=True)


async def collect(source: str):
    settings = get_settings()
    connector = make_connector(source, settings)
    seen = set()
    errors = []
    completed = 0
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
                if any(marker in error for marker in ("被拒絕", "HTTP 403", "HTTP 429", "驗證頁", "格式已變更", "缺少結構化")):
                    break
            emit({"type": "progress", "completed": completed})
            await asyncio.sleep(settings.request_delay_seconds)
    finally:
        await connector.close()
    status = "success" if not errors else ("partial" if seen else "error")
    emit({"type": "finished", "status": status, "count": len(seen), "completed": completed, "truncated": connector.truncated, "error": "；".join(errors)[:2000] or None})


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    args = parser.parse_args()
    asyncio.run(collect(args.source))
