"""Polite HTTP downloads with retries and an on-disk cache of raw responses."""

from __future__ import annotations

import logging
import time
from pathlib import Path

import requests

log = logging.getLogger(__name__)

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0 Safari/537.36"
)


class NotFound(Exception):
    """The source answered 404 (e.g. no NSE bhavcopy on a holiday)."""


def session(user_agent: str = BROWSER_UA) -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"})
    return s


def fetch(
    sess: requests.Session,
    url: str,
    cache_path: Path | None = None,
    *,
    refresh: bool = False,
    retries: int = 4,
    min_interval: float = 0.15,
    expect: bytes | None = None,
    timeout: float = 60,
) -> bytes:
    """GET ``url`` (or return the cached copy). ``expect`` is a required magic prefix."""
    if cache_path is not None and cache_path.exists() and not refresh:
        return cache_path.read_bytes()
    last_error: Exception | None = None
    for attempt in range(retries):
        time.sleep(min_interval * (2**attempt) if attempt else min_interval)
        try:
            resp = sess.get(url, timeout=timeout)
        except requests.RequestException as exc:
            last_error = exc
            log.warning("GET %s failed (%s), attempt %d", url, exc, attempt + 1)
            continue
        if resp.status_code == 404:
            raise NotFound(url)
        if resp.status_code in (403, 429) or resp.status_code >= 500:
            last_error = RuntimeError(f"HTTP {resp.status_code} for {url}")
            log.warning("GET %s -> %s, attempt %d", url, resp.status_code, attempt + 1)
            continue
        resp.raise_for_status()
        body = resp.content
        if expect is not None and not body.startswith(expect):
            last_error = RuntimeError(f"unexpected payload from {url}: {body[:40]!r}")
            log.warning("%s", last_error)
            continue
        if cache_path is not None:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = cache_path.with_suffix(cache_path.suffix + ".part")
            tmp.write_bytes(body)
            tmp.replace(cache_path)
        return body
    raise RuntimeError(f"giving up on {url}: {last_error}")
