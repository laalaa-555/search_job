"""Ordinary unauthenticated browser search; no saved profile or challenge bypass."""
import asyncio
import json
import re
import time
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from app.ingestion.http import SourceError, response_error
from app.ingestion.normalize import NormalizedJob, parse_salary

EMPTY = re.compile(r"沒有符合.{0,30}(?:職缺|工作)|查無.{0,20}(?:職缺|工作)|找不到.{0,20}(?:職缺|工作)|共\s*0\s*(?:筆|個).{0,15}(?:職缺|工作)")
CHALLENGE = re.compile(r"verify you are human|checking your browser|just a moment|captcha|安全驗證|人機驗證|確認您是真人", re.I)


def page_error(body, settings, visible_text=None):
    soup = BeautifulSoup(body, "html.parser")
    text = soup.get_text(" ", strip=True) if visible_text is None else visible_text
    if CHALLENGE.search(text):
        return SourceError("來源要求人工驗證，已停止本輪收集", "verification_required", settings.blocked_cooldown_seconds)
    if not text or text.lower() in ("loading", "loading..."):
        return SourceError("來源網頁仍停留在載入頁，未取得職缺", "loading_timeout", settings.blocked_cooldown_seconds)
    return SourceError("來源搜尋頁結構已變更，未找到可辨識職缺或零結果提示", "format_changed")


def structured_jobs(body, source):
    soup = BeautifulSoup(body, "html.parser")
    results = []
    def walk(value):
        if isinstance(value, list):
            for entry in value:
                walk(entry)
        elif isinstance(value, dict):
            kind = value.get("@type")
            if kind == "JobPosting" or isinstance(kind, list) and "JobPosting" in kind:
                org = value.get("hiringOrganization") or {}
                url = value.get("url")
                if not url and isinstance(value.get("mainEntityOfPage"), dict):
                    url = value["mainEntityOfPage"].get("@id")
                if not url:
                    return
                match = re.search(r"/job/([^/?]+)", url)
                if not match:
                    return
                locations = value.get("jobLocation") or []
                if isinstance(locations, dict):
                    locations = [locations]
                city = " / ".join(str((loc.get("address") or {}).get("addressLocality") or "") for loc in locations if isinstance(loc, dict))
                salary = value.get("baseSalary") or {}
                amount = salary.get("value") or {}
                period = {"MONTH": "month", "YEAR": "year", "HOUR": "hour", "DAY": "day"}.get(str(amount.get("unitText", "")).upper()) if isinstance(amount, dict) else None
                low = amount.get("minValue", amount.get("value")) if isinstance(amount, dict) else amount
                high = amount.get("maxValue") if isinstance(amount, dict) else None
                text = f"{salary.get('currency', '')} {low or ''}{'–' + str(high) if high else ''} / {period or '週期未提供'}" if low or high else "薪資未提供"
                results.append(NormalizedJob(source=source, external_id=match.group(1), title=value.get("title") or "", company=org.get("name") or "", url=urljoin(f"https://www.{source}.com.tw", url), location=city, description=value.get("description") or "", salary_text=text, salary_currency=salary.get("currency"), salary_period=period, salary_min=low, salary_max=high))
            else:
                for child in value.values():
                    if isinstance(child, (dict, list)):
                        walk(child)
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            walk(json.loads(script.get_text()))
        except (ValueError, TypeError):
            continue
    return results


def parse_cards(body, source):
    jobs = structured_jobs(body, source)
    if jobs:
        return jobs
    soup = BeautifulSoup(body, "html.parser")
    selectors = '.item__job, [data-job-id], .job-list-item' if source == "1111" else '.job-list-item, article[data-job-no], [data-job-no], .b-block__left'
    for card in soup.select(selectors):
        link = card.select_one('a[href*="/job/"]')
        company = card.select_one('.job_item_company, .company-name, a[href*="/corp/"], a[href*="/company/"]')
        title = card.select_one('.card-title, .job-name, .job-title, h2, h3') or link
        location = card.select_one('.job_item_detail a, .job-location, .location, .job-list-tag.b-content')
        salary = card.select_one('.job_item_detail_salary, .job-salary, .salary')
        if not link or not title or not company:
            continue
        url = urljoin(f"https://www.{source}.com.tw", link.get("href", ""))
        match = re.search(r"/job/([^/?]+)", url)
        if match:
            jobs.append(NormalizedJob(source=source, external_id=match.group(1), title=title.get_text(" ", strip=True), company=company.get_text(" ", strip=True).split("｜")[0].split("|")[0], location=location.get_text(" ", strip=True) if location else "", url=url, **parse_salary(salary.get_text(" ", strip=True) if salary else "薪資未提供")))
    return jobs


class BrowserSearch:
    def __init__(self, source, settings):
        self.source, self.settings = source, settings
        self.playwright = self.browser = self.page = None
        self.next_request = 0.0

    async def load(self, url):
        from playwright.async_api import async_playwright, TimeoutError as BrowserTimeout
        if self.browser is None:
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.launch(headless=True)
            self.page = await self.browser.new_page(locale="zh-TW")
        await asyncio.sleep(max(0, self.next_request - time.monotonic()))
        captured, tasks = [], set()
        async def capture(response):
            host = urlsplit(response.url).hostname or ""
            if self.source == "104" and (host == "104.com.tw" or host.endswith(".104.com.tw")) and "search" in response.url and "json" in response.headers.get("content-type", ""):
                try:
                    from app.ingestion.connectors import extract_104
                    rows = extract_104(await response.json())
                    captured.append(rows)
                except (ValueError, SourceError):
                    pass
        def on_response(response):
            task = asyncio.create_task(capture(response))
            tasks.add(task)
            task.add_done_callback(tasks.discard)
        self.page.on("response", on_response)
        try:
            response = await self.page.goto(url, wait_until="domcontentloaded", timeout=int(self.settings.request_timeout_seconds * 1000))
            if response and response.status in (401, 403, 429, 452):
                body = await self.page.content()
                visible = await self.page.title() + " " + await self.page.locator("body").inner_text()
                if response.status != 429 and CHALLENGE.search(visible):
                    raise page_error(body, self.settings, visible)
                raise response_error(response.status, self.settings, response.headers.get("retry-after"))
            deadline = time.monotonic() + self.settings.request_timeout_seconds
            while True:
                body = await self.page.content()
                if captured:
                    return body, captured[-1]
                text = (await self.page.title() + " " + await self.page.locator("body").inner_text()).strip()
                if CHALLENGE.search(text):
                    raise page_error(body, self.settings, text)
                if parse_cards(body, self.source) or EMPTY.search(text):
                    return body, None
                if time.monotonic() >= deadline:
                    raise page_error(body, self.settings, text)
                await asyncio.sleep(.5)
        except BrowserTimeout:
            raise SourceError("來源瀏覽器載入逾時", "loading_timeout", self.settings.blocked_cooldown_seconds) from None
        finally:
            self.page.remove_listener("response", on_response)
            for task in list(tasks):
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            self.next_request = time.monotonic() + self.settings.request_delay_seconds

    async def close(self):
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
