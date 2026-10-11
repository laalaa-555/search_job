import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import func, select

from app.config import Settings
from app.ingestion.connectors import Cake, extract_cake, parse_104
from app.ingestion.http import PublicClient, SourceError
from app.ingestion.normalize import NormalizedJob, city_for, parse_salary
from app.ingestion.store import upsert_job
from app.models import CollectionRun, Job, SourceState, WorkerLock, utcnow
from app.worker import Lease, schedule_trigger


def job(**overrides):
    return NormalizedJob(**dict(source="104", external_id="one", title="Data Scientist", company="測試公司", location="臺北市", url="https://www.104.com.tw/job/test", description="Python SQL", salary_currency="TWD", salary_period="month", salary_min=100000, salary_max=120000) | overrides).finalize()


def seed(sessions):
    with sessions() as db:
        upsert_job(db, job())
        upsert_job(db, job(external_id="two", title="資料工程師", location="台中市", salary_min=80000, salary_max=None))
        upsert_job(db, job(source="cake", external_id="three", title="Data Scientist", url="https://www.cake.me/companies/test/jobs/three", salary_min=None, salary_max=None, salary_text="薪資面議"))
        upsert_job(db, job(external_id="foreign", salary_currency="USD", salary_min=120000, salary_period="year"))


def test_salary_normalization():
    assert job().annual_salary_min == 1200000
    assert job().salary_estimated is True
    assert job(salary_period="year").annual_salary_min == 100000
    assert job(salary_period="hour").annual_salary_min is None
    assert job(salary_currency="USD").annual_salary_min is None
    assert job(salary_min=float("nan")).annual_salary_min is None
    assert parse_salary("月薪 4.5萬～6萬")['salary_min'] == 45000
    assert parse_salary("薪資面議（經常性薪資達4萬元以上）")['salary_min'] is None
    assert city_for("New Taipei City, Taiwan") == "新北市"
    assert city_for("臺中市") == "台中市"


def test_search_without_outbound_network(client, sessions, monkeypatch):
    seed(sessions)
    def forbidden(*args, **kwargs):
        raise AssertionError("Search must never request external sources")
    monkeypatch.setattr(httpx.AsyncClient, "get", forbidden)
    monkeypatch.setattr(PublicClient, "get", forbidden)
    response = client.get("/api/v1/jobs", params={"q":"data scientist", "min_annual_salary":1200000})
    assert response.status_code == 200
    assert response.json()['total'] == 3  # includes unknown and USD
    assert client.get("/api/v1/jobs?min_annual_salary=1200000&include_unknown_salary=false").json()['total'] == 1
    assert client.get("/api/v1/jobs?city=台中市").json()['total'] == 1
    assert client.get("/api/v1/jobs?sources=cake").json()['total'] == 1
    assert client.get("/api/v1/jobs?sources=invalid").status_code == 422
    assert client.get("/api/v1/jobs?page=0").status_code == 422
    assert client.get("/api/v1/jobs?page_size=1&page=2").json()['total'] == 4
    assert client.get("/api/v1/jobs?q=%25").json()['total'] == 0


def test_bookmark_idempotency_and_persistence(client, sessions):
    seed(sessions)
    identifier = client.get("/api/v1/jobs").json()['items'][0]['id']
    assert client.put(f"/api/v1/bookmarks/{identifier}").status_code == 204
    assert client.put(f"/api/v1/bookmarks/{identifier}").status_code == 204
    assert client.get("/api/v1/bookmarks").json()['total'] == 1
    # The fixture opens a new DB session on every request.
    assert client.get(f"/api/v1/jobs/{identifier}").json()['bookmarked'] is True
    assert client.delete(f"/api/v1/bookmarks/{identifier}").status_code == 204
    assert client.get("/api/v1/bookmarks").json()['total'] == 0
    assert client.put("/api/v1/bookmarks/999999").status_code == 404


def test_city_filter_matches_multiple_locations_without_taipei_collision(client, sessions):
    with sessions() as db:
        upsert_job(db, job(location="New Taipei City, Taiwan"))
        upsert_job(db, job(external_id="multiple", location="台北市, 台灣 / Taichung City, Taiwan"))
    assert client.get("/api/v1/jobs?city=台北市").json()['total'] == 1
    assert client.get("/api/v1/jobs?city=台中市").json()['total'] == 1
    assert client.get("/api/v1/jobs?city=新北市").json()['total'] == 1


def test_upsert_preserves_identity_and_revises_changed_content(sessions):
    with sessions() as db:
        first = upsert_job(db, job())
        identifier, updated = first.id, first.updated_at
        again = upsert_job(db, job())
        assert again.id == identifier
        assert again.updated_at == updated
        changed = upsert_job(db, job(salary_min=110000))
        assert changed.id == identifier
        assert changed.annual_salary_min == 1320000
        assert db.scalar(select(func.count()).select_from(Job)) == 1


def test_leases_acquire_renew_expire_and_owner_release(sessions):
    first, second = Lease(sessions, 60), Lease(sessions, 60)
    assert first.acquire()
    assert not second.acquire()
    assert first.renew()
    with sessions() as db:
        db.get(WorkerLock, "collector").expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    assert second.acquire()
    assert not first.renew()
    first.release()
    assert not first.acquire()
    second.release()
    assert first.acquire()
    first.release()


@pytest.mark.parametrize("status", [403, 429, 500])
def test_http_error_is_not_empty_success(status):
    async def run():
        settings = Settings(_env_file=None, request_retries=0)
        client = PublicClient(settings)
        await client.client.aclose()
        client.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(status, request=request)))
        with pytest.raises(SourceError):
            await client.get("https://example.com/jobs")
        await client.close()
    asyncio.run(run())


def test_schedule_is_taipei_and_daily():
    settings = Settings(_env_file=None)
    trigger = schedule_trigger(settings)
    now = datetime(2026, 10, 11, 0, 0, tzinfo=timezone.utc)
    next_run = trigger.get_next_fire_time(None, now)
    assert next_run.hour == 6
    assert next_run.day == 12
    assert str(next_run.tzinfo) == "Asia/Taipei"


def test_source_status_reports_interruption_and_preserves_jobs(client, sessions):
    seed(sessions)
    with sessions() as db:
        db.add(SourceState(source="104", status="running", collected_count=1))
        db.add(SourceState(source="cake", status="error", error="HTTP 403", collected_count=0))
        db.commit()
    states = {item['source']:item for item in client.get("/api/v1/sources").json()['items']}
    assert states['104']['status'] == 'interrupted'
    assert states['cake']['stored_count'] == 1
    assert client.get("/api/v1/jobs").json()['total'] == 4


def test_cake_parses_requested_page_not_advertisements():
    import json
    item = {"jobSource":"cake", "title":"正確職缺"}
    state = {"jobSearch":{"entityByPathId":{"wanted":item,"ad":{"jobSource":"cake","title":"廣告"}},"activeFilterKey":"query","viewsByFilterKey":{"query":{"pageMap":{"2":["wanted"]},"pagination":{"total_pages":2}}}}}
    html = '<script id="__NEXT_DATA__" type="application/json">' + json.dumps({"props":{"pageProps":{"initialState":state}}}) + '</script>'
    assert extract_cake(html, 2) == [item]
    with pytest.raises(SourceError):
        extract_cake(html, 1)


def test_cake_continues_past_pages_of_other_sources_and_stops_at_last_page(monkeypatch):
    import json
    async def check():
        settings = Settings(_env_file=None, request_delay_seconds=0)
        connector = Cake(settings)
        pages = []
        async def get(url, **kwargs):
            page = 2 if "page=2" in url else 1
            pages.append(page)
            item = {"jobSource":"company-site"} if page == 1 else {"jobSource":"cake","path":"real-job","title":"工程師","page":{"path":"company","name":"公司"},"locations":["台北市, 台灣"]}
            state = {"jobSearch":{"entityByPathId":{"one":item},"activeFilterKey":"query","viewsByFilterKey":{"query":{"pageMap":{str(page):["one"]},"pagination":{"total_pages":2}}}}}
            html = '<script id="__NEXT_DATA__">' + json.dumps({"props":{"pageProps":{"initialState":state}}}) + '</script>'
            return httpx.Response(200, text=html)
        monkeypatch.setattr(connector.http, "get", get)
        try:
            results = [job async for job in connector.collect("工程師")]
            assert len(results) == 1 and results[0].title == "工程師"
            assert pages == [1,2] and not connector.truncated
        finally:
            await connector.close()
    asyncio.run(check())


def test_104_sentinel_and_missing_salary_period_are_not_estimated():
    result = parse_104({"jobNo":"123", "jobName":"工程師", "custName":"公司", "link":{"job":"//www.104.com.tw/job/abc"}, "salaryLow":9999999, "salaryHigh":9999999, "salaryDesc":"待遇面議"}).finalize()
    assert result.salary_min is None
    result = parse_104({"jobNo":"123", "jobName":"工程師", "custName":"公司", "link":{"job":"//www.104.com.tw/job/abc"}, "salaryLow":50000}).finalize()
    assert result.annual_salary_min is None


def test_job_urls_reject_untrusted_hosts():
    with pytest.raises(ValueError):
        job(url="javascript:alert(1)")
    with pytest.raises(ValueError):
        job(url="https://www.104.com.tw.example.com/job/1")


def test_jobspy_rate_limit_warning_stops_further_requests(monkeypatch):
    import logging
    import jobspy
    from jobspy.util import RequestsRotating
    from app.ingestion.connectors import JobSpyConnector

    calls = []
    def request(session, method, url, **kwargs):
        calls.append(url)
        logging.getLogger("JobSpy:LinkedIn").warning("LinkedIn response status code 429 for job 1")
        return httpx.Response(429)

    class EmptyFrame:
        def to_json(self, **kwargs):
            return "[]"

    def scrape(**kwargs):
        session = RequestsRotating()
        session.request("GET", "https://www.linkedin.com/jobs/1")
        with pytest.raises(SourceError):
            session.request("GET", "https://www.linkedin.com/jobs/2")
        return EmptyFrame()

    monkeypatch.setattr(RequestsRotating, "request", request)
    monkeypatch.setattr(jobspy, "scrape_jobs", scrape)
    connector = JobSpyConnector("linkedin", Settings(_env_file=None, request_delay_seconds=0))
    try:
        records, errors = connector.scrape("Data Scientist")
        assert records == [] and any("429" in message for message in errors)
        assert len(calls) == 1
        assert RequestsRotating.request is request
    finally:
        asyncio.run(connector.close())
