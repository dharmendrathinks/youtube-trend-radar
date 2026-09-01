from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
import hashlib
import json
import time

import httpx

from youtube_trend_radar.config import HttpConfig
from youtube_trend_radar.db import CacheRecord, Database


SENSITIVE_PARAMS = {"key", "token", "access_token", "api_key"}


@dataclass(slots=True)
class HttpPayload:
    body: bytes
    status_code: int
    headers: dict[str, str]
    fetched_at: datetime
    cache_state: str = "live"

    def json(self) -> Any:
        return json.loads(self.body)


class HttpRequestError(RuntimeError):
    pass


class CachedHttpClient:
    def __init__(
        self,
        database: Database,
        config: HttpConfig,
        *,
        default_headers: dict[str, str] | None = None,
        secrets: list[str] | None = None,
    ):
        self.database = database
        self.config = config
        self.default_headers = {"User-Agent": config.user_agent, **(default_headers or {})}
        self.secrets = [secret for secret in (secrets or []) if secret]
        self.request_count = 0

    def _safe_params(self, params: dict[str, Any] | None) -> dict[str, Any]:
        return {
            key: "<redacted>" if key.lower() in SENSITIVE_PARAMS else value
            for key, value in sorted((params or {}).items())
        }

    def _cache_key(self, url: str, params: dict[str, Any] | None) -> tuple[str, str]:
        safe_params = self._safe_params(params)
        encoded = json.dumps([url, safe_params], sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode()).hexdigest(), f"{url}?{httpx.QueryParams(safe_params)}"

    def _sanitize(self, message: str) -> str:
        sanitized = message
        for secret in self.secrets:
            sanitized = sanitized.replace(secret, "<redacted>")
        return sanitized[:500]

    def get(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        ttl: timedelta | None = None,
    ) -> HttpPayload:
        now = datetime.now(UTC)
        ttl = ttl or timedelta(minutes=self.config.cache_ttl_minutes)
        cache_key, safe_url = self._cache_key(url, params)
        cached = self.database.get_cache(cache_key)
        if cached and cached.expires_at > now:
            return HttpPayload(cached.body, cached.status_code, cached.headers, cached.fetched_at, "cached")

        request_headers = {**self.default_headers, **(headers or {})}
        if cached and cached.etag:
            request_headers["If-None-Match"] = cached.etag
        if cached and cached.last_modified:
            request_headers["If-Modified-Since"] = cached.last_modified

        last_error: Exception | None = None
        for attempt in range(self.config.max_retries + 1):
            try:
                self.request_count += 1
                response = httpx.get(
                    url,
                    params=params,
                    headers=request_headers,
                    timeout=self.config.timeout_seconds,
                    follow_redirects=True,
                )
                if response.status_code == 304 and cached:
                    refreshed = CacheRecord(
                        cache_key=cache_key,
                        url=safe_url,
                        body=cached.body,
                        status_code=cached.status_code,
                        headers=cached.headers,
                        fetched_at=now,
                        expires_at=now + ttl,
                        etag=cached.etag,
                        last_modified=cached.last_modified,
                    )
                    self.database.put_cache(refreshed)
                    return HttpPayload(cached.body, cached.status_code, cached.headers, now, "validated-cache")
                if response.status_code in {429, 500, 502, 503, 504}:
                    if attempt < self.config.max_retries:
                        retry_after = response.headers.get("Retry-After")
                        delay = min(10.0, float(retry_after)) if retry_after and retry_after.isdigit() else self.config.retry_backoff_seconds * (2**attempt)
                        time.sleep(delay)
                        continue
                if response.status_code >= 400:
                    raise HttpRequestError(f"HTTP {response.status_code} from {safe_url}")

                selected_headers = {
                    key.lower(): value
                    for key, value in response.headers.items()
                    if key.lower() in {"content-type", "etag", "last-modified", "x-ratelimit-remaining", "x-ratelimit-reset"}
                }
                record = CacheRecord(
                    cache_key=cache_key,
                    url=safe_url,
                    body=response.content,
                    status_code=response.status_code,
                    headers=selected_headers,
                    fetched_at=now,
                    expires_at=now + ttl,
                    etag=response.headers.get("ETag"),
                    last_modified=response.headers.get("Last-Modified"),
                )
                self.database.put_cache(record)
                return HttpPayload(response.content, response.status_code, selected_headers, now)
            except (httpx.HTTPError, HttpRequestError, ValueError) as exc:
                last_error = exc
                if attempt < self.config.max_retries and not isinstance(exc, HttpRequestError):
                    time.sleep(self.config.retry_backoff_seconds * (2**attempt))
                    continue
                break

        if cached and now - cached.fetched_at <= timedelta(hours=self.config.stale_if_error_hours):
            return HttpPayload(cached.body, cached.status_code, cached.headers, cached.fetched_at, "stale")
        raise HttpRequestError(self._sanitize(str(last_error or "request failed")))

