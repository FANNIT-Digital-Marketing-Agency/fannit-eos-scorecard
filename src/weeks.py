"""Week model for the source-first scorecard.

A "week" is labeled by its starting Monday as "M/D" (e.g. "9/8"), runs
[Mon 00:00, next Mon 00:00) in America/Los_Angeles, and is reviewed as a
Mon..Sun block. This module is the single source of truth for:

  - which weeks exist (all Mondays of the scorecard year up to the current
    in-progress week), so the week picker no longer depends on which sheet
    cells happen to be populated;
  - the default week the dashboard renders (the most recent *completed*
    week); and
  - converting a label to a concrete datetime window and to trailing-week
    lists for trend strips.

Everything here is pure/deterministic given `today`, so it is unit-testable
and identical on the server and in the snapshot job.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

PT = ZoneInfo("America/Los_Angeles")
SCORECARD_YEAR = 2026

# The sheet's weekly columns start at the first Monday on/after Jan 1. For
# 2026 that is Jan 5 (Jan 1 2026 is a Thursday). Weeks before this are not
# tracked.
_FIRST_MONDAY = date(SCORECARD_YEAR, 1, 5)


def _today_pt() -> date:
    """Local calendar date in Pacific (wrapper so tests can stub)."""
    return datetime.now(PT).date()


def label_of(d: date) -> str:
    """A date -> its 'M/D' label (no zero padding, matching the sheet)."""
    return f"{d.month}/{d.day}"


def monday_of(d: date) -> date:
    """The Monday of the ISO week containing `d`."""
    return d - timedelta(days=d.weekday())


def current_week_monday(today: date | None = None) -> date:
    """Monday of the in-progress week."""
    return monday_of(today or _today_pt())


def default_week_label(today: date | None = None) -> str:
    """The most recent *completed* week: the Monday one week before the
    in-progress week. This is the dashboard default (you review the week
    that has fully elapsed, not a half-finished one).
    """
    return label_of(current_week_monday(today) - timedelta(days=7))


def parse_label(week_label: str) -> date:
    """'M/D' -> the concrete Monday date in SCORECARD_YEAR.

    Raises ValueError on a malformed label so callers can 400.
    """
    m, d = (int(x) for x in week_label.split("/"))
    return date(SCORECARD_YEAR, m, d)


def week_window(week_label: str) -> tuple[datetime, datetime]:
    """'M/D' (a Monday) -> [Mon 00:00 PT, next Mon 00:00 PT)."""
    mon = parse_label(week_label)
    start = datetime(mon.year, mon.month, mon.day, tzinfo=PT)
    return start, start + timedelta(days=7)


def week_dates(week_label: str) -> tuple[str, str]:
    """'M/D' -> ('YYYY-MM-DD' Monday, 'YYYY-MM-DD' Sunday) for date-only APIs."""
    mon = parse_label(week_label)
    return mon.isoformat(), (mon + timedelta(days=6)).isoformat()


def all_week_labels(today: date | None = None) -> list[str]:
    """Every Monday label from the first tracked week through the current
    in-progress week, chronological. Feeds the week picker.
    """
    end = current_week_monday(today)
    out: list[str] = []
    d = _FIRST_MONDAY
    while d <= end:
        out.append(label_of(d))
        d += timedelta(days=7)
    return out


def trailing_labels(week_label: str, n: int = 8) -> list[str]:
    """The n Monday labels ending at (and including) `week_label`,
    chronological, clamped to the first tracked week.
    """
    mon = parse_label(week_label)
    out: list[str] = []
    for i in range(n - 1, -1, -1):
        d = mon - timedelta(days=7 * i)
        if d >= _FIRST_MONDAY:
            out.append(label_of(d))
    return out


def is_current_week(week_label: str, today: date | None = None) -> bool:
    """True if the label is the in-progress week (data still changing)."""
    return parse_label(week_label) == current_week_monday(today)


def year_start_iso() -> str:
    """Jan 1 of the scorecard year, for YTD range queries."""
    return date(SCORECARD_YEAR, 1, 1).isoformat()
