import asyncio
from datetime import timedelta, timezone
from email.utils import format_datetime
import json

import httpx
import pytest
from sqlalchemy import select

from app import worker
from app.config import Settings
from app.ingestion.browser import EMPTY, page_error, parse_cards
from app.ingestion.connectors import Source104, Source1111, JobSpyConnector
from app.ingestion.http import PublicClient, SourceError, retry_after_seconds
from app.ingestion.linkedin_details import detail_candidates, fetch_detail
from app.ingestion.normalize import NormalizedJob
from app.ingestion.store import upsert_job
from app.models import CollectionRun, Job, SourceState, utcnow


def linkedin(identifier="li-1", **kwargs):
    values = dict(source="linkedin", external_id=identifier, title="Data Scientist", company="測試公司", url=f"https://www.linkedin.com/jobs/view/{identifier.removeprefix('li-')}")
    return NormalizedJob(**(values | kwargs))


def test_listing_preserves_description_skills_salary_and_refresh_timestamp(sessions):
    now = utcnow()
    with sessions() as db:
        original = upsert_job(db, linkedin(description="Python SQL 原描述", salary_text="月薪 60,000", salary_currency="TWD", salary_period="month", salary_min=60000, detail_collected_at=now))
        identifier = original.id
        again = upsert_job(db, linkedin(title="Data Engineer", update_mode="listing", detail_status="pending"))
        assert again.id == identifier and again.description == "Python SQL 原描述"
        assert again.annual_salary_min == 720000 and "SQL" in again.skills
        assert again.detail_status == "complete" and again.detail_collected_at.replace(tzinfo=timezone.utc) == now
        assert "原描述" in again.search_text


def test_detail_refresh_does_not_change_list_last_seen_and_refreshes_identical_content(sessions):
    with sessions() as db:
        first = upsert_job(db, linkedin(description="Python", detail_collected_at=utcnow() - timedelta(days=8)))
        seen = first.last_seen_at
        fresh = utcnow()
        again = upsert_job(db, linkedin(description="Python", detail_collected_at=fresh, update_mode="details"))
        assert again.last_seen_at == seen
        assert again.detail_collected_at == fresh


def test_detail_candidates_prioritize_missing_and_skip_recent(sessions):
    settings = Settings(_env_file=None, linkedin_detail_limit=2)
    with sessions() as db:
        upsert_job(db, linkedin("li-1", detail_collected_at=utcnow() - timedelta(days=8)))
        upsert_job(db, linkedin("li-2", detail_status="pending"))
        upsert_job(db, linkedin("li-3", detail_collected_at=utcnow()))
        upsert_job(db, linkedin("li-4", detail_status="pending"))
        candidates = detail_candidates(db, {"li-1", "li-2", "li-3"}, settings)
        assert [job.external_id for job in candidates] == ["li-2", "li-1"]
        assert not detail_candidates(db, {"li-3"}, settings)


@pytest.mark.parametrize("status,code", [(403,"access_denied"),(452,"access_denied"),(429,"rate_limited")])
def test_blocked_http_is_single_request_with_cooldown(status, code):
    async def check():
        client = PublicClient(Settings(_env_file=None, request_retries=5, request_delay_seconds=0))
        await client.client.aclose()
        calls = []
        def respond(request):
            calls.append(request)
            return httpx.Response(status, headers={"Retry-After":"7200"}, request=request)
        client.client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        try:
            with pytest.raises(SourceError) as error:
                await client.get("https://www.linkedin.com/jobs/view/1")
            assert len(calls) == 1 and error.value.code == code
            assert error.value.cooldown_seconds == (7200 if status == 429 else 86400)
        finally:
            await client.close()
    asyncio.run(check())


def test_retry_after_date_and_invalid_values():
    assert retry_after_seconds("invalid") == 0
    assert retry_after_seconds("-1") == 0
    assert 3590 <= retry_after_seconds(format_datetime(utcnow() + timedelta(hours=1), usegmt=True)) <= 3600


def test_cooldown_survives_new_worker_session_and_api_exposes_it(sessions, client, monkeypatch):
    future = utcnow() + timedelta(hours=2)
    with sessions() as db:
        db.add(SourceState(source="104", status="error", reason_code="access_denied", next_retry_at=future, error="HTTP 403"))
        db.commit()
    monkeypatch.setattr(worker, "SessionLocal", sessions)
    async def forbidden(*args, **kwargs):
        raise AssertionError("Cooldown must not launch source requests")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden)
    for trigger in ("startup", "scheduled"):
        asyncio.run(worker.run_source("104", trigger, Settings(_env_file=None), asyncio.Semaphore(1)))
    with sessions() as db:
        assert list(db.scalars(select(CollectionRun))) == []
    state = next(s for s in client.get("/api/v1/sources").json()["items"] if s["source"] == "104")
    assert state["next_retry_at"] and state["reason_code"] == "access_denied"


def test_source_specific_config_and_validation():
    settings = Settings(_env_file=None)
    assert settings.for_source("linkedin").request_delay_seconds == 10
    assert settings.for_source("linkedin").source_timeout_seconds == 3600
    assert settings.for_source("104").source_timeout_seconds == 1800
    with pytest.raises(ValueError):
        Settings(_env_file=None, source_request_delays={"unknown":2})


def test_browser_distinguishes_empty_verification_loading_and_format():
    settings = Settings(_env_file=None)
    assert EMPTY.search("沒有符合條件的職缺")
    assert not EMPTY.search("會員點數 0 筆")
    assert page_error("<title>Just a moment...</title>", settings).code == "verification_required"
    assert page_error("<title>Loading...</title>", settings).code == "loading_timeout"
    assert page_error("<h1>搜尋職缺</h1><p>新版未知結構</p>", settings).code == "format_changed"
    assert page_error('<title>Loading...</title><div hidden>安全驗證失敗</div>', settings, 'Loading...').code == "loading_timeout"


def test_1111_structured_jobs_and_cards_preserve_salary():
    value = {"@type":"JobPosting", "title":"工程師", "hiringOrganization":{"name":"公司"}, "url":"https://www.1111.com.tw/job/123/", "jobLocation":{"address":{"addressLocality":"台北市"}}, "baseSalary":{"currency":"TWD", "value":{"unitText":"MONTH","minValue":50000,"maxValue":70000}}}
    body = '<script type="application/ld+json">' + json.dumps(value) + '</script>'
    result = parse_cards(body, "1111")[0].finalize()
    assert result.external_id == "123" and result.annual_salary_min == 600000
    cards = '<div class="item__job"><a class="card-title" href="/job/124">工程師</a><a class="job_item_company">公司</a><div class="job_item_detail_salary">月薪 60,000</div></div>'
    assert parse_cards(cards, "1111")[0].finalize().annual_salary_min == 720000


def test_104_http_falls_back_once_and_reuses_browser_for_second_page(monkeypatch):
    async def check():
        connector = Source104(Settings(_env_file=None, request_delay_seconds=0, max_pages_per_keyword=3))
        requests, browser_pages = [], []
        async def refused(url, **kwargs):
            requests.append(url)
            raise SourceError("HTTP 403", "access_denied", 86400)
        async def browser(url):
            browser_pages.append(url)
            if len(browser_pages) == 2:
                return "沒有符合條件的職缺", None
            return "", [{"jobNo":"1","jobName":"工程師","custName":"公司","link":{"job":"https://www.104.com.tw/job/one"}}]
        monkeypatch.setattr(connector.http, "get", refused)
        monkeypatch.setattr(connector.browser_search, "load", browser)
        try:
            results = [job async for job in connector.collect("工程師")]
            assert len(results) == 1 and len(requests) == 1 and len(browser_pages) == 2
        finally:
            await connector.close()
    asyncio.run(check())


def test_detail_html_parser_keeps_raw_salary_and_sets_freshness():
    async def check():
        client = PublicClient(Settings(_env_file=None, request_delay_seconds=0))
        await client.client.aclose()
        client.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, text='<div class="show-more-less-html__markup">Python SQL</div><div class="compensation__salary">$117,000.00/yr - $234,000.00/yr</div>', request=request)))
        try:
            job = await fetch_detail(client, linkedin(update_mode="details"))
            assert job.detail_status == "complete" and job.detail_collected_at
            assert job.salary_currency == "USD" and job.salary_period == "year"
            assert job.annual_salary_min is None and "$117,000.00" in job.salary_text
        finally:
            await client.close()
    asyncio.run(check())


def test_104_json_and_dom_identity_match_without_collapsing_distinct_ids(sessions):
    values = dict(source="104", title="工程師", company="公司", url="https://www.104.com.tw/job/abc")
    with sessions() as db:
        first = upsert_job(db, NormalizedJob(external_id="12345", **values))
        dom = upsert_job(db, NormalizedJob(external_id="abc", **values))
        assert first.id == dom.id and dom.external_id == "12345"
        other = upsert_job(db, NormalizedJob(external_id="23456", **(values | {"url":"https://www.104.com.tw/job/def"})))
        assert other.id != first.id


def test_worker_acknowledges_list_commit_and_persists_detail_failure(sessions, monkeypatch):
    monkeypatch.setattr(worker, "SessionLocal", sessions)
    class Sink:
        def write(self, data):
            assert data == b"lists_saved\n"
            with sessions() as db:
                assert db.scalar(select(Job)).detail_status == "pending"
        async def drain(self):
            pass
    class Process:
        returncode = None
        stdin = Sink()
        def __init__(self):
            self.stdout, self.stderr = asyncio.StreamReader(), asyncio.StreamReader()
            events = [
                {"type":"job", "job":linkedin(update_mode="listing", detail_status="pending").model_dump(mode="json")},
                {"type":"lists_finished"},
                {"type":"finished", "status":"partial", "completed":1, "error":"詳情 HTTP 429", "reason_code":"rate_limited", "cooldown_seconds":7200},
            ]
            self.stdout.feed_data("".join(json.dumps(event) + "\n" for event in events).encode())
            self.stdout.feed_eof()
            self.stderr.feed_eof()
        async def wait(self):
            self.returncode = 0
            return 0
        def kill(self):
            self.returncode = -9
    calls = []
    async def spawn(*args, **kwargs):
        calls.append(args)
        return Process()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    settings = Settings(_env_file=None, collect_keywords="Data Scientist")
    asyncio.run(worker.run_source("linkedin", "startup", settings, asyncio.Semaphore(1)))
    asyncio.run(worker.run_source("linkedin", "startup", settings, asyncio.Semaphore(1)))
    assert len(calls) == 1
    with sessions() as db:
        state = db.get(SourceState, "linkedin")
        assert state.status == "partial" and state.reason_code == "rate_limited"
        assert state.next_retry_at and state.last_data_at and state.keywords_completed == 1
        assert db.scalar(select(Job)).detail_status == "pending"


def test_runner_finishes_all_lists_before_details_and_keeps_list_on_detail_error(sessions, monkeypatch):
    from app.ingestion import runner
    settings = Settings(_env_file=None, collect_keywords="one,two", source_request_delays={"linkedin":0}, request_delay_seconds=0)
    monkeypatch.setattr(runner, "get_settings", lambda:settings)
    monkeypatch.setattr(runner, "SessionLocal", sessions)
    order, events = [], []
    class Connector:
        truncated = False
        http = object()
        async def collect(self, keyword):
            order.append(keyword)
            yield linkedin("li-1" if keyword == "one" else "li-2", update_mode="listing", detail_status="pending")
        async def close(self):
            pass
    monkeypatch.setattr(runner, "make_connector", lambda *args:Connector())
    def emit(event):
        events.append(event)
        if event["type"] == "job":
            with sessions() as db:
                upsert_job(db, NormalizedJob.model_validate(event["job"]))
    monkeypatch.setattr(runner, "emit", emit)
    import io
    monkeypatch.setattr(runner.sys, "stdin", io.StringIO("lists_saved\n"))
    async def detail(*args):
        order.append("detail")
        raise SourceError("HTTP 429", "rate_limited", 3600)
    monkeypatch.setattr(runner, "fetch_detail", detail)
    asyncio.run(runner.collect("linkedin"))
    assert order == ["one","two","detail"]
    assert events[-1]["status"] == "partial" and events[-1]["completed"] == 2
    assert events[-1]["cooldown_seconds"] == 3600


def test_1111_partial_page_failure_keeps_first_page_and_repeated_page_is_error(monkeypatch):
    async def check():
        connector = Source1111(Settings(_env_file=None, request_delay_seconds=0))
        count = 0
        body = '<div class="item__job"><a class="card-title" href="/job/123">工程師</a><a class="job_item_company">公司</a></div>'
        async def load(url):
            nonlocal count
            count += 1
            if count == 2:
                raise SourceError("HTTP 429", "rate_limited",3600)
            return body,None
        monkeypatch.setattr(connector.browser_search,"load",load)
        jobs = []
        try:
            with pytest.raises(SourceError) as error:
                async for job in connector.collect("工程師"):
                    jobs.append(job)
            assert len(jobs) == 1 and error.value.code == "rate_limited"
            async def repeat(url):
                return body,None
            monkeypatch.setattr(connector.browser_search,"load",repeat)
            with pytest.raises(SourceError) as error:
                _ = [job async for job in connector.collect("工程師")]
            assert error.value.code == "pagination_repeated"
        finally:
            await connector.close()
    asyncio.run(check())


def test_jobspy_preserves_retry_after_for_requests_and_tls(monkeypatch):
    import jobspy
    from jobspy.util import RequestsRotating,TLSRotating
    for source,kind in (("linkedin",RequestsRotating),("indeed",TLSRotating)):
        def request(session,method,url,**kwargs):
            return httpx.Response(429,headers={"Retry-After":"9000"})
        monkeypatch.setattr(kind,"request",request)
        class Frame:
            def to_json(self,**kwargs):
                return "[]"
        def scrape(**kwargs):
            session = kind()
            try:
                session.get("https://www.linkedin.com/jobs/test")
            finally:
                session.close()
            return Frame()
        monkeypatch.setattr(jobspy,"scrape_jobs",scrape)
        connector = JobSpyConnector(source,Settings(_env_file=None,request_delay_seconds=0))
        try:
            assert connector.scrape("工程師")[0] == []
            assert connector.last_error.cooldown_seconds == 9000
            assert kind.request is request
        finally:
            asyncio.run(connector.close())


def test_expired_cooldown_resumes_and_clears_failure(sessions, monkeypatch):
    with sessions() as db:
        db.add(SourceState(source="104",status="error",reason_code="access_denied",next_retry_at=utcnow()-timedelta(seconds=1),error="HTTP 403"))
        db.commit()
    monkeypatch.setattr(worker,"SessionLocal",sessions)
    class Process:
        returncode = None
        def __init__(self):
            self.stdout,self.stderr=asyncio.StreamReader(),asyncio.StreamReader()
            self.stdout.feed_data((json.dumps({"type":"finished","status":"success","completed":1,"error":None})+"\n").encode())
            self.stdout.feed_eof()
            self.stderr.feed_eof()
        async def wait(self):
            self.returncode=0
            return 0
        def kill(self):
            self.returncode=-9
    calls=[]
    async def spawn(*args,**kwargs):
        calls.append(args)
        return Process()
    monkeypatch.setattr(asyncio,"create_subprocess_exec",spawn)
    asyncio.run(worker.run_source("104","scheduled",Settings(_env_file=None,collect_keywords="one"),asyncio.Semaphore(1)))
    assert len(calls)==1
    with sessions() as db:
        state=db.get(SourceState,"104")
        assert state.status=="success" and state.last_success_at
        assert state.next_retry_at is None and state.reason_code is None and state.error is None
