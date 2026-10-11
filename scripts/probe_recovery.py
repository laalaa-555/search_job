"""Small real connector probe; never inserts diagnostics into the job database.

Windows: .venv/Scripts/python.exe scripts/probe_recovery.py --source 104
Container: docker compose run --rm --no-deps worker python -m app.worker --once --source 104
For real persistence/worker acceptance use app.worker, not this diagnostic.
"""
import argparse
import asyncio
import json
from pathlib import Path
import sys
from datetime import datetime, timezone
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import get_settings
from app.ingestion.connectors import make_connector
from app.ingestion.http import SourceError


async def probe(source, keyword, status_url):
    # Fail closed: this diagnostic must not bypass the production source cooldown.
    status = json.load(urllib.request.urlopen(status_url, timeout=10))
    state = next(item for item in status["items"] if item["source"] == source)
    next_retry = state.get("next_retry_at")
    if next_retry and datetime.fromisoformat(next_retry.replace("Z", "+00:00")).replace(tzinfo=timezone.utc) > datetime.now(timezone.utc):
        print(json.dumps({"source":source,"status":"skipped","reason":"cooldown","next_retry_at":next_retry},ensure_ascii=False))
        return
    settings = get_settings().for_source(source).model_copy(update={"max_results_per_keyword":5, "max_pages_per_keyword":2})
    connector = make_connector(source, settings)
    result = {"source":source,"keyword":keyword,"jobs":[],"runtime":"Windows Chromium / Python"}
    try:
        async for job in connector.collect(keyword):
            result["jobs"].append(job.finalize().model_dump(mode="json"))
        result["status"] = "success"
    except Exception as exc:
        result.update(status="partial" if result["jobs"] else "error", error=str(exc) if isinstance(exc, SourceError) else type(exc).__name__, reason_code=getattr(exc,"code","source_error"))
        browser = getattr(connector,"browser_search",None)
        if browser and browser.page:
            result["diagnostics"] = browser.diagnostics
    finally:
        await connector.close()
    directory = ROOT / "data" / "diagnostics"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"recovery-{source}-windows.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(result,ensure_ascii=False))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, choices=["104","1111","linkedin"])
    parser.add_argument("--keyword", default="Data Scientist")
    parser.add_argument("--status-url", default="http://127.0.0.1:8000/api/v1/sources")
    args = parser.parse_args()
    asyncio.run(probe(args.source,args.keyword,args.status_url))
