"""Ordinary, unauthenticated browser load; saves ignored diagnostic HTML."""
import asyncio
import json
from pathlib import Path
import sys
from playwright.async_api import async_playwright


async def main():
    directory = Path("data/diagnostics")
    directory.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        for name, url in {
            "104": "https://www.104.com.tw/jobs/search/?keyword=Data%20Scientist",
            "1111": "https://www.1111.com.tw/search/job?ks=Data%20Scientist",
        }.items():
            page = await browser.new_page(locale="zh-TW")
            captured = []
            async def on_response(response):
                if "104.com.tw/jobs/search" in response.url and "api" in response.url:
                    try:
                        data = await response.json()
                        path = directory / "104-search.json"
                        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                        captured.append({"url": response.url, "status": response.status})
                    except Exception:
                        pass
            page.on("response", on_response)
            try:
                response = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                try:
                    await page.wait_for_selector('a[href*="/job/"]', timeout=12000)
                except Exception:
                    pass
                body = await page.content()
                (directory / f"{name}.html").write_text(body, encoding="utf-8")
                print(json.dumps({"source":name,"status":response.status if response else None,"title":await page.title(),"jobLinks":await page.locator('a[href*="/job/"]').count(),"body":(await page.locator("body").inner_text())[:900],"captured":captured},ensure_ascii=False),flush=True)
            except Exception as exc:
                print(json.dumps({"source":name,"error":str(exc)[:300]},ensure_ascii=False),flush=True)
            await page.close()
        await browser.close()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
