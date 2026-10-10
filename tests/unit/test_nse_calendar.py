"""The NSE trading calendar behind the raw gate's session-completeness check."""

from collections import Counter
from datetime import date

from fbc.sources import nse_calendar


def test_holiday_lists_are_complete_as_published():
    # Entries in NSE's holiday-master (CM) for each year, weekend entries included.
    assert Counter(d.year for d in nse_calendar.HOLIDAYS) == {2024: 21, 2025: 18, 2026: 20}
    assert sorted(nse_calendar.COVERED_YEARS) == [2024, 2025, 2026]


def test_weekends_and_holidays_are_not_sessions():
    assert nse_calendar.is_session(date(2026, 10, 1))
    assert not nse_calendar.is_session(date(2026, 10, 2))  # Mahatma Gandhi Jayanti (Friday)
    assert not nse_calendar.is_session(date(2026, 10, 3))  # Saturday
    assert not nse_calendar.is_session(date(2026, 1, 15))  # Maharashtra municipal elections


def test_expected_sessions_skip_holidays_and_weekends():
    assert nse_calendar.expected_sessions(date(2026, 10, 5), 3) == [
        date(2026, 9, 30),
        date(2026, 10, 1),
        date(2026, 10, 5),
    ]
    # The 10 sessions of the 9 October 2026 run (docs/verification.md).
    window = nse_calendar.expected_sessions(date(2026, 10, 9), 10)
    assert window[0] == date(2026, 9, 25) and window[-1] == date(2026, 10, 9)
    assert date(2026, 10, 2) not in window


def test_a_bhavcopy_on_a_holiday_counts_as_a_session():
    # Muhurat trading on Diwali Laxmi Pujan (21 Oct 2025, a listed holiday).
    muhurat = date(2025, 10, 21)
    assert nse_calendar.expected_sessions(date(2025, 10, 23), 3) == [
        date(2025, 10, 17),
        date(2025, 10, 20),
        date(2025, 10, 23),
    ]
    assert nse_calendar.expected_sessions(date(2025, 10, 23), 3, observed=[muhurat]) == [
        date(2025, 10, 20),
        muhurat,
        date(2025, 10, 23),
    ]


def test_years_without_a_holiday_list_are_reported():
    assert nse_calendar.uncovered_years([date(2026, 12, 31), date(2027, 1, 4)]) == [2027]
    assert nse_calendar.uncovered_years([date(2026, 10, 1)]) == []
