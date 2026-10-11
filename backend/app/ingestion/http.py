import asyncio
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import time

import httpx

from app.config import Settings


class SourceError(RuntimeError):
    def __init__(self, message, code=None, cooldown_seconds=0):
        super().__init__(message)
        self.code = code or ("format_changed" if any(word in message for word in ("格式", "欄位", "缺少結構化")) else "source_error")
        self.cooldown_seconds = cooldown_seconds


def retry_after_seconds(value):
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        try:
            stamp = parsedate_to_datetime(value)
            return max(0, int((stamp - datetime.now(timezone.utc)).total_seconds()))
        except (TypeError, ValueError, OverflowError):
            return 0


def response_error(status, settings, retry_after=None):
    if status == 429:
        return SourceError("來源限流（HTTP 429）", "rate_limited", max(settings.rate_limit_cooldown_seconds, retry_after_seconds(retry_after)))
    return SourceError(f"公開資料存取被拒絕（HTTP {status}）", "access_denied", settings.blocked_cooldown_seconds)


class PublicClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.next_request = 0.0
        self.client = httpx.AsyncClient(timeout=settings.request_timeout_seconds, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8"})

    async def get(self, url, **kwargs):
        for attempt in range(self.settings.request_retries + 1):
            try:
                await asyncio.sleep(max(0, self.next_request - time.monotonic()))
                self.next_request = time.monotonic() + self.settings.request_delay_seconds
                response = await self.client.get(url, **kwargs)
                if response.status_code in (403, 401, 429, 452):
                    raise response_error(response.status_code, self.settings, response.headers.get("Retry-After"))
                response.raise_for_status()
                return response
            except (httpx.HTTPError, SourceError) as exc:
                if isinstance(exc, SourceError):
                    raise
                if attempt == self.settings.request_retries:
                    status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
                    raise SourceError(f"HTTP {status}" if status else "來源連線逾時或網路錯誤", "http_error" if status else "network_error") from exc
                await asyncio.sleep(max(self.settings.request_delay_seconds, 2 ** attempt))

    async def close(self):
        await self.client.aclose()
