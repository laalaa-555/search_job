import asyncio
import io
import json

import httpx
import pytest
from sqlalchemy import func, select

from app import main, worker
from app.config import Settings
from app.ingestion import runner
from app.ingestion.connectors import Cake, JobSpyConnector, SearchPage, Source104
from app.ingestion.http import SourceError
from app.ingestion.normalize import NormalizedJob
from app.ingestion.progress import checkpoint, next_keyword, prepare_cycle, progress_rows
from app.ingestion.browser import diagnostic_url
from app.models import Bookmark, CollectionCycle, Job, SourceState
from app.models import utcnow
from datetime import timedelta


def settings(**values):
    defaults = dict(collect_keywords="one,two,three", source_request_delays={"linkedin":0,"cake":0}, request_delay_seconds=0)
    return Settings(_env_file=None, **(defaults | values))


def begin(sessions, config, source="linkedin"):
    with sessions() as db:
        state = db.get(SourceState, source)
        if state is None:
            state = SourceState(source=source)
            db.add(state)
        cycle = prepare_cycle(db, state, config)
        db.commit()
        return cycle.id


def page_event(sessions, cycle_id, *, ids=None, has_more=True, source="linkedin"):
    with sessions() as db:
        cycle = db.get(CollectionCycle, cycle_id)
        row = next_keyword(cycle, progress_rows(db, cycle_id))
        ids = ids if ids is not None else [f"{row.keyword}-{row.next_page}"]
        return dict(type="page", cycle_id=cycle_id, keyword_index=row.keyword_index, page=row.next_page,
                    jobs=[NormalizedJob(source=source, external_id=value, title="工程師", company="測試公司",
                           url=f"https://www.linkedin.com/jobs/view/{value}" if source == "linkedin" else f"https://www.cake.me/companies/test/jobs/{value}",
                           detail_status="pending", update_mode="listing").model_dump(mode="json") for value in ids],
                    raw_ids=ids, has_more=has_more, next_offset=row.next_offset+10)


def save(sessions, event, source="linkedin"):
    with sessions() as db:
        checkpoint(db, db.get(SourceState, source), event)
        db.commit()


def test_round_robin_and_restart_resume_each_keywords_page(sessions):
    config = settings()
    cycle_id = begin(sessions, config)
    positions = []
    for _ in range(6):
        event = page_event(sessions, cycle_id)
        positions.append((event["keyword_index"], event["page"]))
        save(sessions, event)
    assert positions == [(0,1),(1,1),(2,1),(0,2),(1,2),(2,2)]
    assert begin(sessions, config) == cycle_id
    with sessions() as db:
        rows = progress_rows(db, cycle_id)
        assert [row.next_page for row in rows] == [3,3,3]
        assert [row.next_offset for row in rows] == [20,20,20]
        assert db.scalar(select(func.count(Job.id))) == 6


def test_page_transaction_rollback_keeps_jobs_and_progress_unchanged(sessions):
    cycle_id = begin(sessions, settings())
    event = page_event(sessions, cycle_id)
    with sessions() as db:
        checkpoint(db, db.get(SourceState, "linkedin"), event)
        db.rollback()  # Crash before commit; the same page is safe to retry.
    with sessions() as db:
        assert db.scalar(select(func.count(Job.id))) == 0
        assert progress_rows(db, cycle_id)[0].next_page == 1
    save(sessions, event)
    with sessions() as db:
        assert db.scalar(select(func.count(Job.id))) == 1


def test_failed_page_rotates_keyword_but_does_not_advance_page(sessions):
    cycle_id = begin(sessions, settings())
    event = page_event(sessions, cycle_id)
    event["type"] = "page_failed"
    save(sessions, event)
    with sessions() as db:
        rows = progress_rows(db, cycle_id)
        assert rows[0].next_page == 1 and rows[0].seen_ids == []
        assert next_keyword(db.get(CollectionCycle, cycle_id), rows).keyword == "two"


def test_cooldown_skips_without_replacing_or_advancing_cycle(sessions, monkeypatch):
    config = settings()
    cycle_id = begin(sessions, config)
    save(sessions, page_event(sessions,cycle_id))
    with sessions() as db:
        state = db.get(SourceState,"linkedin")
        state.next_retry_at = utcnow()+timedelta(hours=1)
        db.commit()
    monkeypatch.setattr(worker,"SessionLocal",sessions)
    async def forbidden(*args,**kwargs):
        raise AssertionError("Cooling source must not spawn a collector")
    monkeypatch.setattr(asyncio,"create_subprocess_exec",forbidden)
    asyncio.run(worker.run_source("linkedin","startup",config,asyncio.Semaphore(1)))
    with sessions() as db:
        state = db.get(SourceState,"linkedin")
        assert state.current_cycle_id == cycle_id and state.next_retry_at
        assert progress_rows(db,cycle_id)[0].next_page == 2


def test_repeated_page_is_error_and_does_not_advance(sessions):
    cycle_id = begin(sessions, settings(collect_keywords="one"))
    save(sessions, page_event(sessions, cycle_id, ids=["same"]))
    with pytest.raises(SourceError) as error:
        save(sessions, page_event(sessions, cycle_id, ids=["same"]))
    assert error.value.code == "pagination_repeated"
    with sessions() as db:
        assert progress_rows(db, cycle_id)[0].next_page == 2
        assert db.scalar(select(func.count(Job.id))) == 1


@pytest.mark.parametrize("limit,pages", [(2,10),(100,1)])
def test_caps_complete_but_mark_truncated(sessions, limit, pages):
    config = Settings(_env_file=None, collect_keywords="one", max_results_per_keyword=limit, max_pages_per_keyword=pages)
    cycle_id = begin(sessions, config)
    save(sessions, page_event(sessions, cycle_id, ids=["a","b","c"]))
    with sessions() as db:
        cycle = db.get(CollectionCycle, cycle_id)
        row = progress_rows(db, cycle_id)[0]
        assert row.completed_at and row.truncated and cycle.completed_at and cycle.truncated
        assert len(row.seen_ids) == min(limit,3)


def test_complete_cycle_starts_new_cycle_only_on_next_run_and_keeps_bookmark(sessions):
    config = Settings(_env_file=None, collect_keywords="one")
    first = begin(sessions, config)
    save(sessions, page_event(sessions, first, has_more=False))
    with sessions() as db:
        db.add(Bookmark(job_id=db.scalar(select(Job.id))))
        db.commit()
    second = begin(sessions, config)
    assert second != first
    with sessions() as db:
        assert db.get(CollectionCycle, first).completed_at
        assert db.scalar(select(func.count(Bookmark.job_id))) == 1
        assert db.get(SourceState, "linkedin").last_completed_scope_hash == config.scope_hash("linkedin")


@pytest.mark.parametrize("change", [{"collect_keywords":"two,one"},{"collect_location":"台北市"},{"max_results_per_keyword":20},{"max_pages_per_keyword":3}])
def test_scope_changes_start_new_cycle_preserving_existing_data(sessions, change):
    config = settings()
    first = begin(sessions, config)
    save(sessions, page_event(sessions, first))
    second = begin(sessions, config.model_copy(update=change))
    assert second != first
    with sessions() as db:
        assert db.scalar(select(func.count(Job.id))) == 1
        assert progress_rows(db, second)[0].seen_ids == []


def test_api_exposes_cycle_and_hides_legacy_or_other_scope_complete_time(sessions, client, monkeypatch):
    config = settings()
    monkeypatch.setattr(main, "settings", config)
    cycle_id = begin(sessions, config)
    save(sessions, page_event(sessions, cycle_id, has_more=False))
    row = next(item for item in client.get("/api/v1/sources").json()["items"] if item["source"] == "linkedin")
    assert row["cycle_keywords_completed"] == 1 and row["pending_keywords"] == 2
    assert row["cycle_pages_collected"] == 1 and not row["cycle_completed"]
    assert row["last_cycle_completed_at"] is None and row["keyword_progress"][0]["completed"]
    monkeypatch.setattr(main,"settings",config.model_copy(update={"collect_keywords":"changed"}))
    row = next(item for item in client.get("/api/v1/sources").json()["items"] if item["source"] == "linkedin")
    assert row["cycle_id"] is None and row["pending_keywords"] == 1


def test_runner_obeys_batch_budget_and_defers_details_across_runs(sessions, monkeypatch):
    config = settings(batch_pages_per_run=2)
    begin(sessions, config)
    monkeypatch.setattr(runner,"SessionLocal",sessions)
    monkeypatch.setattr(runner,"get_settings",lambda:config)
    events, calls = [], []
    class Connector:
        truncated = False
        async def collect_page(self, keyword, page, offset):
            calls.append((keyword,page,offset))
            return SearchPage([],[],False)
        async def close(self):
            pass
    monkeypatch.setattr(runner,"make_connector",lambda *args:Connector())
    def emit(event):
        events.append(event)
        if event["type"] == "page":
            save(sessions,event)
    monkeypatch.setattr(runner,"emit",emit)
    monkeypatch.setattr(runner.sys,"stdin",io.StringIO("page_saved\npage_saved\n"))
    asyncio.run(runner.collect("linkedin"))
    assert calls == [("one",1,0),("two",1,0)]
    assert events[-1]["status"] == "batch_complete" and events[-1]["completed"] == 2
    monkeypatch.setattr(runner.sys,"stdin",io.StringIO("page_saved\n"))
    asyncio.run(runner.collect("linkedin"))
    assert calls[-1] == ("three",1,0) and events[-1]["status"] == "success"


def test_runner_429_keeps_previous_pages_and_failed_position(sessions, monkeypatch):
    config = settings()
    cycle_id = begin(sessions, config)
    monkeypatch.setattr(runner,"SessionLocal",sessions)
    monkeypatch.setattr(runner,"get_settings",lambda:config)
    events = []
    class Connector:
        truncated = False
        async def collect_page(self, keyword, page, offset):
            if keyword == "two":
                raise SourceError("HTTP 429","rate_limited",7200)
            incoming = NormalizedJob(source="linkedin",external_id="one",title="工程師",company="公司",url="https://www.linkedin.com/jobs/view/1")
            return SearchPage([incoming],["one"],True,10)
        async def close(self):
            pass
    monkeypatch.setattr(runner,"make_connector",lambda *args:Connector())
    def emit(event):
        events.append(event)
        if event["type"] in ("page","page_failed"):
            save(sessions,event)
    monkeypatch.setattr(runner,"emit",emit)
    monkeypatch.setattr(runner.sys,"stdin",io.StringIO("page_saved\npage_saved\n"))
    asyncio.run(runner.collect("linkedin"))
    assert events[-1]["status"] == "partial" and events[-1]["cooldown_seconds"] == 7200
    with sessions() as db:
        rows = progress_rows(db,cycle_id)
        assert [row.next_page for row in rows] == [2,1,1]
        assert db.get(CollectionCycle,cycle_id).next_keyword_index == 2


def test_parent_acknowledges_page_only_after_transaction_commit(sessions, monkeypatch):
    config = settings()
    monkeypatch.setattr(worker,"SessionLocal",sessions)
    class Process:
        returncode = None
        def __init__(self):
            with sessions() as db:
                cycle_id = db.get(SourceState,"linkedin").current_cycle_id
            event = page_event(sessions,cycle_id)
            self.stdout,self.stderr = asyncio.StreamReader(),asyncio.StreamReader()
            self.stdout.feed_data((json.dumps(event)+"\n"+json.dumps({"type":"finished","status":"batch_complete","completed":0})+"\n").encode())
            self.stdout.feed_eof()
            self.stderr.feed_eof()
            self.stdin = self
        def write(self, data):
            assert data == b"page_saved\n"
            with sessions() as db:
                assert db.scalar(select(func.count(Job.id))) == 1
                cycle_id = db.get(SourceState,"linkedin").current_cycle_id
                assert progress_rows(db,cycle_id)[0].next_page == 2
        async def drain(self):
            pass
        async def wait(self):
            self.returncode = 0
            return 0
        def kill(self):
            self.returncode = -9
    async def spawn(*args,**kwargs):
        return Process()
    monkeypatch.setattr(asyncio,"create_subprocess_exec",spawn)
    asyncio.run(worker.run_source("linkedin","startup",config,asyncio.Semaphore(1)))
    with sessions() as db:
        assert db.get(SourceState,"linkedin").last_success_at is None


def test_cake_excluded_first_page_is_not_zero_results(monkeypatch):
    state = {"jobSearch":{"activeFilterKey":"x","viewsByFilterKey":{"x":{"pagination":{"total_pages":2},"pageMap":{"1":["transferred"]}}},"entityByPathId":{"transferred":{"jobSource":"104"}}}}
    async def check():
        connector = Cake(settings())
        async def get(*args,**kwargs):
            return httpx.Response(200,text='<script id="__NEXT_DATA__">'+json.dumps({"props":{"pageProps":{"initialState":state}}})+'</script>')
        monkeypatch.setattr(connector.http,"get",get)
        try:
            page = await connector.collect_page("one",1)
            assert page.jobs == [] and page.raw_ids == ["transferred"] and page.has_more
        finally:
            await connector.close()
    asyncio.run(check())


def test_linkedin_one_page_uses_offset_and_enforces_one_http_request(monkeypatch):
    import jobspy
    from jobspy.util import RequestsRotating
    calls = []
    def request(*args,**kwargs):
        calls.append(kwargs)
        return httpx.Response(200,text='<div class="base-search-card" data-entity-urn="urn:li:jobPosting:123"></div>')
    monkeypatch.setattr(RequestsRotating,"request",request)
    class Frame:
        def to_json(self,**kwargs):
            return "[]"
    def scrape(**kwargs):
        assert kwargs["offset"] == 20 and kwargs["results_wanted"] == 10 and not kwargs["fetch_description"]
        session = RequestsRotating()
        try:
            session.get("https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search")
            with pytest.raises(SourceError):
                session.get("https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search")
        finally:
            session.close()
        return Frame()
    monkeypatch.setattr(jobspy,"scrape_jobs",scrape)
    connector = JobSpyConnector("linkedin",settings())
    try:
        connector.scrape("one",20,True)
        assert len(calls) == 1 and connector.raw_ids == ["123"]
    finally:
        asyncio.run(connector.close())


def test_104_429_does_not_trigger_additional_browser_requests(monkeypatch):
    async def check():
        connector = Source104(settings())
        async def limited(*args,**kwargs):
            raise SourceError("HTTP 429","rate_limited",3600)
        async def forbidden(*args,**kwargs):
            raise AssertionError("429 must stop the source before a browser fallback")
        monkeypatch.setattr(connector.http,"get",limited)
        monkeypatch.setattr(connector.browser_search,"load",forbidden)
        try:
            with pytest.raises(SourceError) as error:
                _ = [job async for job in connector.collect("one")]
            assert error.value.code == "rate_limited"
        finally:
            await connector.close()
    asyncio.run(check())


def test_diagnostics_exclude_credentials_queries_fragments_and_inline_content():
    assert diagnostic_url("https://user:secret@www.1111.com.tw/search/job?token=secret#private") == "https://www.1111.com.tw/search/job"
    assert diagnostic_url("https://www.104.com.tw/user/private@example.com") == "https://www.104.com.tw/user/[redacted]"
    assert diagnostic_url("data:text/javascript,secret") == "data:[inline]"
