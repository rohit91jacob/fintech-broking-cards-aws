"""Polite HTTP downloads with retries and an on-disk cache of raw responses.

Every request carries an honest, descriptive User-Agent (``FBC_USER_AGENT``, by default
``fintech-broking-cards-aws/<version> (+<repository URL>)``). The pipeline never poses as a
browser: when a server will not serve a client that says what it is, :class:`Refused` is
raised and the caller skips that source.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import requests

log = logging.getLogger(__name__)

# Markers of bot-challenge / access-denied pages seen in place of the requested file.
CHALLENGE_MARKERS = (
    b"/TSPD/",  # F5 BIG-IP ASM JavaScript challenge (rbidocs.rbi.org.in)
    b"human visitor",
    b"captcha",
    b"Unauthorised Access",
    b"Access Denied",
)


class NotFound(Exception):
    """The source answered 404 (e.g. no NSE bhavcopy on a holiday)."""


class Refused(RuntimeError):
    """The server did not serve the file to this client: a bot-challenge or access-denied page,
    HTTP 403, or no answer at all within the timeout (some edges silently drop requests from
    non-browser clients)."""


def is_challenge_page(body: bytes) -> bool:
    head = body[:20000]
    return any(marker.lower() in head.lower() for marker in CHALLENGE_MARKERS)


def session(user_agent: str) -> requests.Session:
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
    timeout: float | tuple[float, float] = 60,
) -> bytes:
    """GET ``url`` (or return the cached copy). ``expect`` is a required magic prefix.

    Raises :class:`NotFound` on 404 and :class:`Refused` when every attempt was refused
    (challenge page, 403 or timeout); other persistent failures raise ``RuntimeError``.
    """
    if cache_path is not None and cache_path.exists() and not refresh:
        return cache_path.read_bytes()
    last_error: Exception | None = None
    refused: str | None = None
    for attempt in range(retries):
        time.sleep(min_interval * (2**attempt) if attempt else min_interval)
        try:
            resp = sess.get(url, timeout=timeout)
        except requests.Timeout as exc:
            last_error, refused = exc, f"no response within {timeout} s"
            log.warning("GET %s timed out, attempt %d", url, attempt + 1)
            continue
        except requests.RequestException as exc:
            last_error, refused = exc, None
            log.warning("GET %s failed (%s), attempt %d", url, exc, attempt + 1)
            continue
        if resp.status_code == 404:
            raise NotFound(url)
        if resp.status_code in (403, 429) or resp.status_code >= 500:
            last_error = RuntimeError(f"HTTP {resp.status_code} for {url}")
            refused = "HTTP 403" if resp.status_code == 403 else None
            log.warning("GET %s -> %s, attempt %d", url, resp.status_code, attempt + 1)
            continue
        resp.raise_for_status()
        body = resp.content
        if expect is not None and not body.startswith(expect):
            last_error = RuntimeError(f"unexpected payload from {url}: {body[:40]!r}")
            html = "html" in resp.headers.get("Content-Type", "") or body.lstrip()[:1] == b"<"
            refused = (
                "a bot-challenge / access-denied page instead of the file"
                if html and is_challenge_page(body)
                else None
            )
            log.warning("%s", last_error)
            continue
        if cache_path is not None:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = cache_path.with_suffix(cache_path.suffix + ".part")
            tmp.write_bytes(body)
            tmp.replace(cache_path)
        return body
    if refused is not None:
        raise Refused(f"{url}: {refused} (User-Agent {sess.headers.get('User-Agent')!r})")
    raise RuntimeError(f"giving up on {url}: {last_error}")
