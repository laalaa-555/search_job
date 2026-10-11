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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import get_settings
from app.ingestion.connectors import make_connector
from app.ingestion.http import SourceError


async def probe(source, keyword):
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
            result["page_title"] = await browser.page.title()
            result["page_text"] = (await browser.page.locator("body").inner_text())[:500]
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
    args = parser.parse_args()
    asyncio.run(probe(args.source,args.keyword))
