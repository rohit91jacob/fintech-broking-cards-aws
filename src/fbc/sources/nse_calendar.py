"""NSE capital-market trading calendar: weekdays minus NSE's published trading holidays.

The holidays are NSE's own list (``www.nseindia.com/api/holiday-master?type=trading``,
segment ``CM``), copied as published, weekend entries included. nseindia.com does not answer
non-browser clients, so the lists were read from the Internet Archive's copies of that endpoint:

* 2024: https://web.archive.org/web/20241111100733/https://www.nseindia.com/api/holiday-master?type=trading
* 2025: https://web.archive.org/web/20250821072057/https://www.nseindia.com/api/holiday-master?type=trading
* 2026: https://web.archive.org/web/20260916045525/https://www.nseindia.com/api/holiday-master?type=trading

Outside ``COVERED_YEARS`` every weekday is assumed to be a session, and the raw data-quality
gate warns about it: add the next year's list when NSE publishes it (usually in December), and
any holiday NSE declares at short notice. Muhurat trading (a short Diwali session, sometimes on
a listed holiday or a weekend) is not predicted: a day that has a bhavcopy counts as a session
(the ``observed`` argument).
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, timedelta

HOLIDAYS: dict[date, str] = {
    # 2024
    date(2024, 1, 22): "Special Holiday",
    date(2024, 1, 26): "Republic Day",
    date(2024, 3, 8): "Mahashivratri",
    date(2024, 3, 25): "Holi",
    date(2024, 3, 29): "Good Friday",
    date(2024, 4, 11): "Id-Ul-Fitr (Ramadan Eid)",
    date(2024, 4, 14): "Dr. Baba Saheb Ambedkar Jayanti",
    date(2024, 4, 17): "Shri Ram Navmi",
    date(2024, 4, 21): "Shri Mahavir Jayanti",
    date(2024, 5, 1): "Maharashtra Day",
    date(2024, 5, 20): "General Parliamentary Elections",
    date(2024, 6, 17): "Bakri Id",
    date(2024, 7, 17): "Moharram",
    date(2024, 8, 15): "Independence Day",
    date(2024, 9, 7): "Ganesh Chaturthi",
    date(2024, 10, 2): "Mahatma Gandhi Jayanti",
    date(2024, 10, 12): "Dussehra",
    date(2024, 11, 2): "Balipratipada",
    date(2024, 11, 15): "Prakash Gurpurb Sri Guru Nanak Dev",
    date(2024, 11, 20): "Assembly Elections in Maharashtra",
    date(2024, 12, 25): "Christmas",
    # 2025
    date(2025, 1, 26): "Republic Day",
    date(2025, 2, 26): "Mahashivratri",
    date(2025, 3, 14): "Holi",
    date(2025, 3, 31): "Id-Ul-Fitr (Ramadan Eid)",
    date(2025, 4, 6): "Shri Ram Navami",
    date(2025, 4, 10): "Shri Mahavir Jayanti",
    date(2025, 4, 14): "Dr. Baba Saheb Ambedkar Jayanti",
    date(2025, 4, 18): "Good Friday",
    date(2025, 5, 1): "Maharashtra Day",
    date(2025, 6, 7): "Bakri Id",
    date(2025, 7, 6): "Muharram",
    date(2025, 8, 15): "Independence Day / Parsi New Year",
    date(2025, 8, 27): "Shri Ganesh Chaturthi",
    date(2025, 10, 2): "Mahatma Gandhi Jayanti/Dussehra",
    date(2025, 10, 21): "Diwali Laxmi Pujan",
    date(2025, 10, 22): "Balipratipada",
    date(2025, 11, 5): "Prakash Gurpurb Sri Guru Nanak Dev",
    date(2025, 12, 25): "Christmas",
    # 2026
    date(2026, 1, 15): "Municipal Corporation Election - Maharashtra",
    date(2026, 1, 26): "Republic Day",
    date(2026, 2, 15): "Mahashivratri",
    date(2026, 3, 3): "Holi",
    date(2026, 3, 21): "Id-Ul-Fitr (Ramadan Eid)",
    date(2026, 3, 26): "Shri Ram Navami",
    date(2026, 3, 31): "Shri Mahavir Jayanti",
    date(2026, 4, 3): "Good Friday",
    date(2026, 4, 14): "Dr. Baba Saheb Ambedkar Jayanti",
    date(2026, 5, 1): "Maharashtra Day",
    date(2026, 5, 28): "Bakri Id",
    date(2026, 6, 26): "Muharram",
    date(2026, 8, 15): "Independence Day",
    date(2026, 9, 14): "Ganesh Chaturthi",
    date(2026, 10, 2): "Mahatma Gandhi Jayanti",
    date(2026, 10, 20): "Dussehra",
    date(2026, 11, 8): "Diwali Laxmi Pujan*",  # NSE's asterisk: a Muhurat session is held
    date(2026, 11, 10): "Diwali-Balipratipada",
    date(2026, 11, 24): "Prakash Gurpurb Sri Guru Nanak Dev",
    date(2026, 12, 25): "Christmas",
}

COVERED_YEARS = frozenset(d.year for d in HOLIDAYS)


def is_session(day: date) -> bool:
    """A regular NSE capital-market session: a weekday that is not a listed holiday."""
    return day.weekday() < 5 and day not in HOLIDAYS


def expected_sessions(end: date, count: int, observed: Iterable[date] = ()) -> list[date]:
    """The ``count`` sessions up to and including ``end``, oldest first.

    ``observed`` are days that actually have a bhavcopy; they count as sessions even when the
    calendar says otherwise (a Muhurat session on a holiday or a weekend).
    """
    extra = set(observed)
    out: list[date] = []
    day = end
    while len(out) < count:
        if is_session(day) or day in extra:
            out.append(day)
        day -= timedelta(days=1)
    return out[::-1]


def uncovered_years(days: Iterable[date]) -> list[int]:
    """Years in ``days`` for which no holiday list is committed (weekdays-only assumption)."""
    return sorted({d.year for d in days} - COVERED_YEARS)
