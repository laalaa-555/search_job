import asyncio
from datetime import datetime
import json
import logging
import re
import time
from urllib.parse import quote, urlencode, urljoin

from bs4 import BeautifulSoup

from app.config import Settings
from app.ingestion.http import PublicClient, SourceError
from app.ingestion.normalize import NormalizedJob, number, parse_salary, plain


class Connector:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.http = PublicClient(settings)
        self.truncated = False

    async def close(self):
        await self.http.close()


def extract_104(payload):
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
    async def collect(self, keyword):
        seen = set()
        for page in range(1, self.settings.max_pages_per_keyword + 1):
            params = dict(keyword=keyword, page=page, order=15, asc=0, mode="s", jobsource="2018indexpoc")
            response = await self.http.get("https://www.104.com.tw/jobs/search/api/jobs", params=params, headers={"Referer": "https://www.104.com.tw/jobs/search/", "Accept": "application/json, text/plain, */*"})
            try:
                rows = extract_104(response.json())
            except json.JSONDecodeError as exc:
                raise SourceError("104 沒有回傳職缺 JSON，可能需要驗證") from exc
            if not rows:
                return
            new = 0
            for item in rows:
                job = parse_104(item)
                if job.external_id not in seen:
                    seen.add(job.external_id)
                    new += 1
                    yield job
                if len(seen) >= self.settings.max_results_per_keyword:
                    self.truncated = True
                    return
            if not new:
                self.truncated = True
                raise SourceError("104 分頁重複，已停止以避免無限收集")
            await asyncio.sleep(self.settings.request_delay_seconds)
        self.truncated = True


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
    soup = BeautifulSoup(body, "html.parser")
    cards = soup.select(".item__job")
    jobs = []
    for card in cards:
        link = card.select_one('.job_item_info a[href*="/job/"]')
        title = card.select_one(".card-title")
        company = card.select_one(".job_item_company")
        location = card.select_one(".job_item_detail a")
        salary = card.select_one(".job_item_detail_salary")
        if not all((link, title, company)):
            raise SourceError("1111 職缺卡片欄位已變更")
        url = urljoin("https://www.1111.com.tw", link["href"])
        match = re.search(r"/job/(\d+)", url)
        if not match:
            continue
        jobs.append(NormalizedJob(source="1111", external_id=match.group(1), title=title.get_text(" ", strip=True), company=company.get_text(" ", strip=True).split("｜")[0].split("|")[0].strip(), location=location.get_text(" ", strip=True) if location else "", url=url, description=plain(card), **parse_salary(salary.get_text(" ", strip=True) if salary else "薪資未提供")))
    return jobs


class Source1111(Connector):
    def __init__(self, settings):
        super().__init__(settings)
        self.playwright = self.browser = self.page = None

    async def browser_html(self, url):
        from playwright.async_api import async_playwright, TimeoutError as BrowserTimeout
        if not self.browser:
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.launch(headless=True)
            self.page = await self.browser.new_page(locale="zh-TW")
        response = await self.page.goto(url, wait_until="domcontentloaded", timeout=int(self.settings.request_timeout_seconds * 1000))
        if response and response.status in (403, 401, 452):
            raise SourceError(f"公開資料存取被拒絕（HTTP {response.status}）")
        try:
            await self.page.wait_for_selector(".item__job", timeout=int(self.settings.request_timeout_seconds * 1000))
        except BrowserTimeout:
            text = await self.page.locator("body").inner_text()
            if not re.search(r"沒有符合|查無|0\s*筆", text):
                raise SourceError("1111 一般瀏覽器仍停留在載入／驗證頁") from None
        return await self.page.content()

    async def collect(self, keyword):
        seen = set()
        for page in range(1, self.settings.max_pages_per_keyword + 1):
            url = "https://www.1111.com.tw/search/job?" + urlencode({"ks": keyword, "page": page, "col": "da"})
            try:
                body = (await self.http.get(url)).text
            except SourceError:
                body = ""
            rows = parse_1111(body)
            if not rows:
                body = await self.browser_html(url)
                rows = parse_1111(body)
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
                raise SourceError("1111 分頁重複，已停止收集")
            await asyncio.sleep(self.settings.request_delay_seconds)
        self.truncated = True

    async def close(self):
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
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

    def scrape(self, keyword):
        from jobspy import scrape_jobs
        from jobspy.util import RequestsRotating
        capture = ErrorCapture()
        logger = logging.getLogger(f"JobSpy:{'LinkedIn' if self.source == 'linkedin' else 'Indeed'}")
        logger.addHandler(capture)
        original = RequestsRotating.request
        next_request = 0.0

        def paced(session, method, url, **kwargs):
            nonlocal next_request
            if any(re.search(r"status code (?:403|429)\b", message) for message in capture.messages):
                raise SourceError("本次來源已拒絕請求，停止後續網路存取")
            time.sleep(max(0, next_request - time.monotonic()))
            kwargs.setdefault("timeout", self.settings.request_timeout_seconds)
            try:
                return original(session, method, url, **kwargs)
            finally:
                next_request = time.monotonic() + self.settings.request_delay_seconds

        RequestsRotating.request = paced
        try:
            frame = scrape_jobs(site_name=[self.source], search_term=keyword, location=self.settings.collect_location, country_indeed="Taiwan", results_wanted=self.settings.max_results_per_keyword, enforce_annual_salary=False, description_format="plain", fetch_description=self.source == "linkedin", verbose=1)
            # Replace Pandas NaN/NaT without leaking non-finite values into JSON.
            records = json.loads(frame.to_json(orient="records", date_format="iso"))
        finally:
            RequestsRotating.request = original
            logger.removeHandler(capture)
        return records, capture.messages

    async def collect(self, keyword):
        records, errors = await asyncio.to_thread(self.scrape, keyword)
        if len(records) >= self.settings.max_results_per_keyword:
            self.truncated = True
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
            yield NormalizedJob(source=self.source, external_id=str(row.get("id") or ""), title=row.get("title") or "", company=row.get("company") or "", location=row.get("location") or "", url=row.get("job_url") or "", description=row.get("description") or "", salary_text=text, salary_currency=row.get("currency"), salary_period=period, salary_min=low, salary_max=high, source_updated_at=datetime.fromisoformat(posted.replace("Z", "+00:00")) if posted else None)
        if errors:
            statuses = [match.group(1) for message in errors if (match := re.search(r"status code (\d+)", message))]
            raise SourceError("JobSpy 來源請求失敗" + (f"（HTTP {','.join(dict.fromkeys(statuses))}）" if statuses else "，未將空結果視為成功"))


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
