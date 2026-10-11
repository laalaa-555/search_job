import asyncio
import json

from app import worker
from app.config import Settings
from app.models import Job, SourceState
from sqlalchemy import select


def test_partial_source_failure_does_not_stop_other_sources(sessions, monkeypatch):
    settings = Settings(_env_file=None, enabled_sources="104,cake", collect_keywords="Data Scientist", source_timeout_seconds=3)
    monkeypatch.setattr(worker, "SessionLocal", sessions)
    monkeypatch.setattr(worker, "get_settings", lambda: settings)

    class FakeProcess:
        def __init__(self, source):
            self.returncode = None
            self.stdout = asyncio.StreamReader()
            self.stderr = asyncio.StreamReader()
            job = dict(source=source, external_id="real-fixture", title="Data Scientist", company="測試公司", url=f"https://www.{'104.com.tw/job/test' if source == '104' else 'cake.me/companies/test/jobs/one'}")
            events = [{"type":"job", "job":job}, {"type":"finished", "status":"partial" if source == "104" else "success", "error":"第二頁 HTTP 429" if source == "104" else None, "completed":0 if source == "104" else 1}]
            self.stdout.feed_data("".join(json.dumps(event) + "\n" for event in events).encode())
            self.stdout.feed_eof()
            self.stderr.feed_eof()
        async def wait(self):
            self.returncode = 0
            return 0
        def kill(self):
            self.returncode = -9

    async def spawn(*args, **kwargs):
        return FakeProcess(args[-1])
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    assert asyncio.run(worker.collect_all("startup"))
    with sessions() as db:
        assert db.get(SourceState, "104").status == "partial"
        assert db.get(SourceState, "cake").status == "success"
        assert len(db.scalars(select(Job)).all()) == 2
    # Re-running on restart updates existing jobs instead of duplicating them.
    assert asyncio.run(worker.collect_all("startup"))
    with sessions() as db:
        assert len(db.scalars(select(Job)).all()) == 2


def test_source_process_timeout_marks_error_and_releases_lease(sessions, monkeypatch):
    settings = Settings(_env_file=None, enabled_sources="104", collect_keywords="one", source_timeout_seconds=.02)
    monkeypatch.setattr(worker, "SessionLocal", sessions)
    monkeypatch.setattr(worker, "get_settings", lambda: settings)
    class HangingProcess:
        returncode = None
        def __init__(self):
            self.stdout, self.stderr = asyncio.StreamReader(), asyncio.StreamReader()
            self.stderr.feed_eof()
        def kill(self):
            self.returncode = -9
            self.stdout.feed_eof()
        async def wait(self):
            return self.returncode
    async def spawn(*args, **kwargs):
        return HangingProcess()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    assert asyncio.run(worker.collect_all())
    with sessions() as db:
        assert db.get(SourceState, "104").status == "error"
        assert "逾時" in db.get(SourceState, "104").error
    lease = worker.Lease(sessions)
    assert lease.acquire()
    lease.release()


def test_worker_process_start_triggers_immediate_collection(monkeypatch):
    settings = Settings(_env_file=None, collect_on_startup=True)
    monkeypatch.setattr(worker, "get_settings", lambda: settings)
    triggers = []
    async def check():
        started = asyncio.Event()
        async def collect(trigger):
            triggers.append(trigger)
            started.set()
        monkeypatch.setattr(worker, "collect_all", collect)
        task = asyncio.create_task(worker.serve())
        try:
            await asyncio.wait_for(started.wait(), timeout=1)
            assert triggers == ["startup"]
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    asyncio.run(check())
