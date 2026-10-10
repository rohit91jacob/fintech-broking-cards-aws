"""Downloads identify themselves honestly, and a source that refuses such a client is skipped
(never retried in disguise) while the rest of the ingest carries on."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from datetime import date

import pytest
import requests

from fbc import http
from fbc.config import DEFAULT_USER_AGENT, PROJECT_URL, Settings
from fbc.ingest import fetch_all, load_all, read_manifest
from fbc.sources import nse, rbi
from tests.conftest import FIXTURES

CHALLENGE = (
    b'<!DOCTYPE html><html><head><script src="/TSPD/08c70e?type=5"></script></head><body>'
    b"This question is for testing whether you are a human visitor</body></html>"
)
LISTING = (
    b"<html><a href='https://rbidocs.rbi.org.in/rdocs/PSI/DOCs/PSI08202612AB.XLSX'>x</a>"
    b"<a href='https://rbidocs.rbi.org.in/rdocs/ATM/DOCs/ATMAUGUST202634CD.XLSX'>y</a></html>"
)


class Resp:
    def __init__(self, status: int = 200, body: bytes = b"", ctype: str = "text/html") -> None:
        self.status_code = status
        self.content = body
        self.headers = {"Content-Type": ctype}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


class FakeSession(requests.Session):
    """Answers every GET from ``route(url)``; records the User-Agent it was created with."""

    def __init__(self, user_agent: str, route: Callable[[str], Resp | Exception]) -> None:
        super().__init__()
        self.headers.update({"User-Agent": user_agent})
        self.route = route
        self.calls: list[str] = []

    def get(self, url, **kwargs):  # type: ignore[override]
        self.calls.append(url)
        answer = self.route(url)
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(http.time, "sleep", lambda _s: None)


def _install(monkeypatch, route) -> list[FakeSession]:
    made: list[FakeSession] = []

    def factory(user_agent: str) -> FakeSession:
        made.append(FakeSession(user_agent, route))
        return made[-1]

    monkeypatch.setattr(http, "session", factory)
    return made


def test_default_user_agent_is_descriptive_and_not_a_browser():
    assert DEFAULT_USER_AGENT.startswith("fintech-broking-cards-aws/")
    assert PROJECT_URL in DEFAULT_USER_AGENT
    assert PROJECT_URL == "https://github.com/rohit91jacob/fintech-broking-cards-aws"
    for browser_token in ("Mozilla", "Chrome", "Safari", "Gecko", "Windows NT"):
        assert browser_token not in DEFAULT_USER_AGENT
    assert http.session(DEFAULT_USER_AGENT).headers["User-Agent"] == DEFAULT_USER_AGENT


def test_challenge_page_instead_of_the_file_is_a_refusal():
    sess = FakeSession("ua", lambda _u: Resp(200, CHALLENGE))
    with pytest.raises(http.Refused, match="bot-challenge"):
        http.fetch(sess, "https://example.org/a.XLSX", expect=b"PK", retries=2)
    assert len(sess.calls) == 2


def test_silence_and_403_are_refusals_but_5xx_is_not():
    silent = FakeSession("ua", lambda _u: requests.ReadTimeout("read timed out"))
    with pytest.raises(http.Refused, match="no response"):
        http.fetch(silent, "https://example.org/a.zip", expect=b"PK", retries=2)
    forbidden = FakeSession("ua", lambda _u: Resp(403))
    with pytest.raises(http.Refused, match="403"):
        http.fetch(forbidden, "https://example.org/a.zip", retries=2)
    broken = FakeSession("ua", lambda _u: Resp(503))
    with pytest.raises(RuntimeError, match="giving up") as exc:
        http.fetch(broken, "https://example.org/a.zip", retries=2)
    assert not isinstance(exc.value, http.Refused)
    with pytest.raises(http.NotFound):
        http.fetch(FakeSession("ua", lambda _u: Resp(404)), "https://example.org/a.zip")


def test_nse_fetch_stops_at_the_first_refusal(tmp_path, monkeypatch):
    made = _install(monkeypatch, lambda _u: requests.ReadTimeout("read timed out"))
    with pytest.raises(http.Refused, match="does not answer non-browser clients"):
        nse.fetch(tmp_path, 10, user_agent="test-agent", end=date(2026, 10, 9))
    # One weekday, two attempts: not a walk back through 40 days of timeouts.
    assert [len(s.calls) for s in made] == [2]
    assert made[0].headers["User-Agent"] == "test-agent"


def test_nse_download_is_off_by_default(settings, monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("NSE must not be contacted unless FBC_NSE_DOWNLOAD=1")

    monkeypatch.setattr(nse, "fetch", boom)
    status = fetch_all(settings, only=("nse",))
    assert status["nse"]["ok"] is False
    assert "Terms of Use" in status["nse"]["skipped"]


def test_opted_in_nse_refusal_is_reported_not_raised(monkeypatch, tmp_path):
    monkeypatch.setenv("FBC_NSE_DOWNLOAD", "1")
    monkeypatch.setenv("FBC_USER_AGENT", "my-fork/1.0 (+https://example.org/my-fork)")
    s = Settings.from_env(data_dir=tmp_path / "data", trading_days=4)
    made = _install(monkeypatch, lambda _u: requests.ReadTimeout("read timed out"))
    status = fetch_all(s, only=("nse",))
    assert status["nse"]["ok"] is False and "refused" in status["nse"]
    assert made[0].headers["User-Agent"] == "my-fork/1.0 (+https://example.org/my-fork)"


def test_rbi_refusal_falls_back_to_fixtures_and_says_so(settings, monkeypatch):
    def route(url: str) -> Resp:
        return Resp(200, CHALLENGE) if "rbidocs" in url else Resp(200, LISTING)

    made = _install(monkeypatch, route)
    status = fetch_all(settings, only=("rbi",))
    assert status["rbi"]["ok"] is False and "bot-challenge" in status["rbi"]["refused"]
    assert made[0].headers["User-Agent"] == settings.user_agent == DEFAULT_USER_AGENT
    # The listing page was fetched; the challenged workbook was not saved.
    assert not list((settings.raw_dir / "rbi").glob("*/*.XLSX"))

    shutil.copytree(FIXTURES / "sec", settings.raw_dir / "sec")
    rows = load_all(settings)
    sources = read_manifest(settings)["sources"]
    assert rows["raw_rbi_psi"] > 0 and rows["raw_nse_bhavcopy"] > 0
    assert sources["rbi"] == {
        "dir": str(settings.fixture_dir / "rbi"),
        "fallback": True,
        "synthetic": True,
    }
    assert sources["nse"]["fallback"] and sources["nse"]["synthetic"]
    assert sources["sec"]["synthetic"] is False


def test_rbi_challenge_on_the_listing_page_is_a_refusal(tmp_path, monkeypatch):
    _install(monkeypatch, lambda _u: Resp(200, CHALLENGE))
    with pytest.raises(http.Refused, match="no XLSX links"):
        rbi.fetch(tmp_path, 12, user_agent="test-agent")
