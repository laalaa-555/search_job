import asyncio
from dataclasses import dataclass
from datetime import datetime
import json
import logging
import re
import time
from urllib.parse import quote, urlencode, urljoin

from bs4 import BeautifulSoup

from app.config import Settings
from app.ingestion.http import PublicClient, SourceError, response_error
from app.ingestion.browser import BrowserSearch, EMPTY, parse_cards, page_error
from app.ingestion.normalize import NormalizedJob, number, parse_salary, plain


class Connector:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.http = PublicClient(settings)
        self.truncated = False

    async def close(self):
        await self.http.close()


@dataclass
class SearchPage:
    jobs: list[NormalizedJob]
    raw_ids: list[str]
    has_more: bool
    next_offset: int = 0


def extract_104(payload):
    if not isinstance(payload, dict):
        raise SourceError("104 回應格式已變更，JSON 不是物件")
    data = payload.get("data")
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("list", "results"):
            if isinstance(data.get(key), list):
                return data[key]
    raise SourceError("104 回應格式已變更，沒有可辨識的職缺列表")


def parse_104(item):
    link = item.get("link", {})
    url = link.get("job", "") if isinstance(link, dict) else ""
    if not url or not item.get("jobNo"):
        raise ValueError("104 職缺缺少來源 ID 或真實網址")
    text = item.get("salaryDesc") or item.get("salary") or "薪資未提供"
    salary = parse_salary(text) if isinstance(text, str) else parse_salary("薪資未提供")
    low = number(item.get("salaryLow"))
    high = number(item.get("salaryHigh"))
    low = low if low and low < 9999999 else None
    high = high if high and high < 9999999 else None
    if not re.search(r"面議", salary["salary_text"]):
        salary["salary_min"] = low or salary["salary_min"]
        salary["salary_max"] = high or salary["salary_max"]
    return NormalizedJob(source="104", external_id=str(item["jobNo"]), title=item.get("jobName", ""), company=item.get("custName", ""), location=item.get("jobAddrNoDesc", ""), url=urljoin("https://www.104.com.tw", url), description=item.get("description", ""), skills=[skill["description"] for skill in item.get("pcSkills", []) if isinstance(skill, dict) and skill.get("description")], **salary)


class Source104(Connector):
    def __init__(self, settings):
        super().__init__(settings)
        self.browser_search = BrowserSearch("104", settings)
        self.browser_mode = False
        self.session_loaded = False

    async def collect(self, keyword):
        seen = set()
        for page in range(1, self.settings.max_pages_per_keyword + 1):
            params = dict(keyword=keyword, page=page, order=15, asc=0, mode="s", jobsource="2018indexpoc")
            search_url = "https://www.104.com.tw/jobs/search/?" + urlencode(params)
            rows = None
            if not self.browser_mode:
                try:
                    if not self.session_loaded:
                        await self.http.get(search_url)
                        self.session_loaded = True
                    response = await self.http.get("https://www.104.com.tw/jobs/search/api/jobs", params=params, headers={"Referer": search_url, "Accept": "application/json, text/plain, */*"})
                    rows = [parse_104(item) for item in extract_104(response.json())]
                except SourceError as exc:
                    if exc.code == "rate_limited":
                        raise
                    self.browser_mode = True
                except ValueError:
                    # One ordinary browser context per round, reused only if it works.
                    self.browser_mode = True
            if self.browser_mode:
                body, payload = await self.browser_search.load(search_url)
                rows = [parse_104(item) for item in payload] if payload is not None else parse_cards(body, "104")
                if not rows and payload is None and not EMPTY.search(plain(body)):
                    raise page_error(body, self.settings)
            if not rows:
                return
            new = 0
            for job in rows:
                if job.external_id not in seen:
                    seen.add(job.external_id)
                    new += 1
                    yield job
                if len(seen) >= self.settings.max_results_per_keyword:
                    self.truncated = True
                    return
            if not new:
                raise SourceError("104 分頁重複，已停止收集", "pagination_repeated")
        self.truncated = True

    async def close(self):
        await self.browser_search.close()
        await super().close()


def extract_cake(body, page, include_pagination=False):
    soup = BeautifulSoup(body, "html.parser")
    script = soup.find("script", id="__NEXT_DATA__")
    if not script:
        raise SourceError("Cake 缺少結構化搜尋資料")
    state = json.loads(script.string or script.get_text())["props"]["pageProps"]["initialState"]
    if isinstance(state, str):
        state = json.loads(state)
    search = state["jobSearch"]
    entities = search["entityByPathId"]
    view = search["viewsByFilterKey"][search["activeFilterKey"]]
    total_pages = view.get("pagination", {}).get("total_pages")
    if not isinstance(total_pages, int) or total_pages < 0:
        raise SourceError("Cake 分頁回應格式已變更")
    page_map = view.get("pageMap", {})
    ids = page_map.get(str(page), page_map.get(page))
    if ids is None and total_pages == 0 and page == 1:
        ids = []
    if ids is None:
        raise SourceError("Cake 分頁回應與要求頁碼不一致")
    rows = [entities[key] for key in ids if key in entities and entities[key].get("jobSource") == "cake"]
    return (rows, total_pages) if include_pagination else rows


def parse_cake(item):
    company = item.get("page") or {}
    salary = item.get("salary") or {}
    period = {"per_month": "month", "per_year": "year", "per_hour": "hour", "per_day": "day"}.get(salary.get("type"))
    low, high = number(salary.get("min")), number(salary.get("max"))
    text = "薪資面議"
    if low or high:
        amounts = f"{low:,.0f}" if low else f"最高 {high:,.0f}"
        if low and high and low != high:
            amounts += f"–{high:,.0f}"
        text = f"{salary.get('currency', '')} {amounts} / {dict(month='月', year='年', hour='時', day='日').get(period, salary.get('type', ''))}"
    updated = item.get("contentUpdatedAt")
    return NormalizedJob(source="cake", external_id=f"{company.get('path')}/{item['path']}", title=item.get("title", ""), company=company.get("name", ""), location=" / ".join(item.get("locations", [])), url=f"https://www.cake.me/companies/{company['path']}/jobs/{item['path']}", description=item.get("description", ""), skills=item.get("tags", []), salary_text=text, salary_currency=salary.get("currency"), salary_period=period, salary_min=low, salary_max=high, source_updated_at=datetime.fromisoformat(updated.replace("Z", "+00:00")) if updated else None)


class Cake(Connector):
    async def collect_page(self, keyword, page, offset=0):
        params = {"locale": "zh-TW", "page": page}
        url = f"https://www.cake.me/jobs/{quote(keyword, safe='')}?{urlencode(params)}"
        response = await self.http.get(url, headers={"Referer": url, "Accept": "application/json,text/html"})
        rows, total_pages = extract_cake(response.text, page, include_pagination=True)
        # Include transferred/overseas IDs in the signature: an excluded page is not zero results.
        state = json.loads(BeautifulSoup(response.text, "html.parser").find("script", id="__NEXT_DATA__").get_text())["props"]["pageProps"]["initialState"]
        state = json.loads(state) if isinstance(state, str) else state
        search = state["jobSearch"]
        view = search["viewsByFilterKey"][search["activeFilterKey"]]
        raw_ids = view.get("pageMap", {}).get(str(page), view.get("pageMap", {}).get(page, []))
        jobs = [parse_cake(item) for item in rows if self.settings.collect_location.lower() not in ("taiwan", "台灣", "全台")
                or re.search(r"台灣|臺灣|Taiwan", " ".join(item.get("locations", [])), re.I)]
        return SearchPage(jobs, [str(value) for value in raw_ids], page < total_pages)

    async def collect(self, keyword):
        seen = set()
        for page in range(1, self.settings.max_pages_per_keyword + 1):
            params = {"locale": "zh-TW"}
            if page > 1:
                params["page"] = page
            url = f"https://www.cake.me/jobs/{quote(keyword, safe='')}?{urlencode(params)}"
            response = await self.http.get(url, headers={"Referer": url, "Accept": "application/json,text/html"})
            rows, total_pages = extract_cake(response.text, page, include_pagination=True)
            new = 0
            for item in rows:
                # Cake search is global. Keep only Taiwan job locations for this scope.
                locations = " ".join(item.get("locations", []))
                if self.settings.collect_location.lower() in ("taiwan", "台灣", "全台") and not re.search(r"台灣|臺灣|Taiwan", locations, re.I):
                    continue
                job = parse_cake(item)
                if job.external_id not in seen:
                    seen.add(job.external_id)
                    new += 1
                    yield job
                if len(seen) >= self.settings.max_results_per_keyword:
                    self.truncated = True
                    return
            # A page containing only overseas jobs may still have Taiwan jobs later.
            if rows and page > 1 and not new and all(f"{(row.get('page') or {}).get('path')}/{row.get('path')}" in seen for row in rows):
                raise SourceError("Cake 分頁重複，已停止收集")
            if page >= total_pages:
                return
            await asyncio.sleep(self.settings.request_delay_seconds)
        self.truncated = True


def parse_1111(body):
    return parse_cards(body, "1111")


class Source1111(Connector):
    def __init__(self, settings):
        super().__init__(settings)
        self.browser_search = BrowserSearch("1111", settings)

    async def collect(self, keyword):
        seen = set()
        for page in range(1, self.settings.max_pages_per_keyword + 1):
            url = "https://www.1111.com.tw/search/job?" + urlencode({"ks": keyword, "page": page, "col": "da"})
            body, _ = await self.browser_search.load(url)
            rows = parse_1111(body)
            if not rows:
                if EMPTY.search(plain(body)):
                    return
                raise page_error(body, self.settings)
            new = 0
            for job in rows:
                if job.external_id not in seen:
                    seen.add(job.external_id)
                    new += 1
                    yield job
                if len(seen) >= self.settings.max_results_per_keyword:
                    self.truncated = True
                    return
            if not new:
                raise SourceError("1111 分頁重複，已停止收集", "pagination_repeated")
        self.truncated = True

    async def close(self):
        await self.browser_search.close()
        await super().close()


class ErrorCapture(logging.Handler):
    def __init__(self):
        super().__init__(logging.WARNING)
        self.messages = []

    def emit(self, record):
        message = record.getMessage()
        if record.levelno >= logging.ERROR or "response status code" in message:
            self.messages.append(message)


class JobSpyConnector(Connector):
    def __init__(self, source, settings):
        super().__init__(settings)
        self.source = source
        self.last_error = None
        self.next_request = 0.0
        self.raw_ids = []

    def scrape(self, keyword, offset=0, single_page=False):
        from jobspy import scrape_jobs
        from jobspy.util import RequestsRotating, TLSRotating
        capture = ErrorCapture()
        logger = logging.getLogger(f"JobSpy:{'LinkedIn' if self.source == 'linkedin' else 'Indeed'}")
        logger.addHandler(capture)
        originals = {RequestsRotating: RequestsRotating.request, TLSRotating: TLSRotating.request}
        requests = 0
        self.raw_ids = []
        self.last_error = None

        def paced(session, method, url, **kwargs):
            nonlocal requests
            if self.last_error or any(re.search(r"status code (?:403|429)\b", message) for message in capture.messages):
                raise SourceError("本次來源已拒絕請求，停止後續網路存取")
            if single_page and requests:
                raise SourceError("JobSpy 嘗試超出單頁請求額度", "format_changed")
            requests += 1
            time.sleep(max(0, self.next_request - time.monotonic()))
            kwargs.setdefault("timeout", self.settings.request_timeout_seconds)
            try:
                response = originals[type(session)](session, method, url, **kwargs)
                if response.status_code in (401, 403, 429, 452):
                    self.last_error = response_error(response.status_code, self.settings, response.headers.get("Retry-After"))
                elif single_page and response.status_code == 200:
                    cards = BeautifulSoup(response.text, "html.parser").select(".base-search-card")
                    self.raw_ids = [card.get("data-entity-urn", "").split(":")[-1] for card in cards]
                return response
            finally:
                self.next_request = time.monotonic() + self.settings.request_delay_seconds

        RequestsRotating.request = paced
        TLSRotating.request = paced
        try:
            from jobspy.linkedin.constant import jobs_per_page
            frame = scrape_jobs(site_name=[self.source], search_term=keyword, location=self.settings.collect_location, country_indeed="Taiwan", results_wanted=jobs_per_page if single_page else self.settings.max_results_per_keyword, offset=offset, enforce_annual_salary=False, description_format="plain", fetch_description=False, verbose=1)
            # Replace Pandas NaN/NaT without leaking non-finite values into JSON.
            records = json.loads(frame.to_json(orient="records", date_format="iso"))
        finally:
            for kind, original in originals.items():
                kind.request = original
            logger.removeHandler(capture)
        return records, capture.messages

    def check_errors(self, errors):
        if self.last_error:
            raise self.last_error
        if errors:
            statuses = [match.group(1) for message in errors if (match := re.search(r"status code (\d+)", message))]
            if any(status in ("403", "429", "452", "401") for status in statuses):
                raise response_error(int(next(status for status in statuses if status in ("403", "429", "452", "401"))), self.settings)
            raise SourceError("JobSpy 來源請求失敗" + (f"（HTTP {','.join(dict.fromkeys(statuses))}）" if statuses else "，未將空結果視為成功"))

    async def collect_page(self, keyword, page, offset=0):
        from jobspy.linkedin.constant import jobs_per_page
        records, errors = await asyncio.to_thread(self.scrape, keyword, offset, True)
        # A failed page is never committed as a successful checkpoint.
        self.check_errors(errors)
        if self.raw_ids and (any(not value for value in self.raw_ids) or len(records) != min(len(set(self.raw_ids)), jobs_per_page)):
            raise SourceError("LinkedIn 卡片未完整解析，保留原分頁進度", "format_changed")
        return SearchPage(list(self.normalize_records(records)), self.raw_ids, len(self.raw_ids) >= jobs_per_page, offset + jobs_per_page)

    def normalize_records(self, records):
        for row in records:
            low, high = number(row.get("min_amount")), number(row.get("max_amount"))
            period = {"monthly": "month", "yearly": "year", "hourly": "hour", "daily": "day", "weekly": "week"}.get(row.get("interval"))
            text = "薪資未提供"
            if low or high:
                amount = f"{low:,.0f}" if low else f"最高 {high:,.0f}"
                if low and high and low != high:
                    amount += f"–{high:,.0f}"
                text = f"{row.get('currency') or ''} {amount} / {row.get('interval') or '週期未提供'}"
            posted = row.get("date_posted")
            yield NormalizedJob(source=self.source, external_id=str(row.get("id") or ""), title=row.get("title") or "", company=row.get("company") or "", location=row.get("location") or "", url=row.get("job_url") or "", description=row.get("description") or "", salary_text=text, salary_currency=row.get("currency"), salary_period=period, salary_min=low, salary_max=high, source_updated_at=datetime.fromisoformat(posted.replace("Z", "+00:00")) if posted else None, update_mode="listing" if self.source == "linkedin" else "full", detail_status="pending" if self.source == "linkedin" else "complete")

    async def collect(self, keyword):
        records, errors = await asyncio.to_thread(self.scrape, keyword)
        if len(records) >= self.settings.max_results_per_keyword:
            self.truncated = True
        for job in self.normalize_records(records):
            yield job
        self.check_errors(errors)


def make_connector(source, settings):
    if source == "104":
        return Source104(settings)
    if source == "1111":
        return Source1111(settings)
    if source == "cake":
        return Cake(settings)
    if source in ("linkedin", "indeed"):
        return JobSpyConnector(source, settings)
    raise ValueError("Unknown source")
