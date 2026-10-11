import asyncio

import httpx

from app.config import Settings


class SourceError(RuntimeError):
    pass


class PublicClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.AsyncClient(timeout=settings.request_timeout_seconds, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8"})

    async def get(self, url, **kwargs):
        for attempt in range(self.settings.request_retries + 1):
            try:
                response = await self.client.get(url, **kwargs)
                if response.status_code in (403, 401, 452):
                    raise SourceError(f"公開資料存取被拒絕（HTTP {response.status_code}）")
                response.raise_for_status()
                return response
            except (httpx.HTTPError, SourceError) as exc:
                if isinstance(exc, SourceError):
                    raise
                if attempt == self.settings.request_retries:
                    status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
                    raise SourceError(f"HTTP {status}" if status else "來源連線逾時或網路錯誤") from exc
                await asyncio.sleep(max(self.settings.request_delay_seconds, 2 ** attempt))

    async def close(self):
        await self.client.aclose()
