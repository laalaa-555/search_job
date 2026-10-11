"""Verify genuine jobs and bookmarks survive local container restarts.

Stop the Worker first so the job count remains stable. No fixture jobs are inserted.
"""
import json
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
PORT = int(dotenv_values(ROOT / ".env").get("API_PORT", "8000"))
BASE = f"http://127.0.0.1:{PORT}/api/v1"


def request(path, method="GET"):
    with urllib.request.urlopen(urllib.request.Request(BASE + path, method=method), timeout=5) as response:
        body = response.read()
        return json.loads(body) if body else None


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    subprocess.run(["docker", "compose", "stop", "worker"], cwd=ROOT, check=True)
    before = request("/jobs")
    if not before["items"]:
        raise SystemExit("先收集至少一筆真實職缺再執行")
    job = before["items"][0]
    saved_before = job["bookmarked"]
    request(f"/bookmarks/{job['id']}", "PUT")
    try:
        subprocess.run(["docker", "compose", "restart", "db", "api", "frontend"], cwd=ROOT, check=True)
        deadline = time.monotonic() + 60
        while True:
            try:
                request("/health")
                break
            except (OSError, ValueError):
                if time.monotonic() >= deadline:
                    raise
                time.sleep(1)
        after = request("/jobs")
        assert before["total"] == after["total"], "職缺筆數未保留"
        assert request(f"/jobs/{job['id']}")["bookmarked"], "收藏未保留"
        report = {"job_count": after["total"], "bookmarked_job_id": job["id"], "restart_persistence": "passed"}
        (ROOT / "data").mkdir(exist_ok=True)
        (ROOT / "data" / "verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False))
    finally:
        if not saved_before:
            request(f"/bookmarks/{job['id']}", "DELETE")
