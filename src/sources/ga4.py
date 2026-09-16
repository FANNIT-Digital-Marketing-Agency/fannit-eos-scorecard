"""GA4 source: Website / LP Traffic (sessions).

One property per agency (src.config.GA4_PROPERTY_IDS). Auth is Application
Default Credentials -> the Cloud Run runtime service account, which was
granted Viewer on all 4 GA4 properties via the Analytics Admin API
(accessBindings, 2026-05-18). No per-request token needed.

A week label "M/D" (a Monday) maps to the Mon..Sun 7-day range in the
property's own timezone, which is what GA4 runReport expects (date-only
YYYY-MM-DD strings).
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from functools import lru_cache

from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    DateRange,
    Dimension,
    Metric,
    RunReportRequest,
)

from ..config import GA4_PROPERTY_IDS

log = logging.getLogger("eos-scorecard.ga4")
SCORECARD_YEAR = 2026


@lru_cache(maxsize=1)
def _client() -> BetaAnalyticsDataClient:
    # Uses ADC; in Cloud Run that's the runtime SA (has Viewer on properties).
    return BetaAnalyticsDataClient()


def _week_dates(week_label: str) -> tuple[str, str]:
    """'M/D' (Monday) -> ('YYYY-MM-DD' Monday, 'YYYY-MM-DD' Sunday)."""
    m, d = (int(x) for x in week_label.split("/"))
    mon = date(SCORECARD_YEAR, m, d)
    sun = mon + timedelta(days=6)
    return mon.isoformat(), sun.isoformat()


def weekly_sessions(agency: str, week_label: str) -> int | None:
    """Total sessions for the agency's GA4 property for the given week.

    Returns None on any failure so the caller falls back to the sheet
    (graceful degradation).
    """
    pid = GA4_PROPERTY_IDS.get(agency)
    if not pid:
        return None
    start, end = _week_dates(week_label)
    try:
        resp = _client().run_report(
            RunReportRequest(
                property=f"properties/{pid}",
                date_ranges=[DateRange(start_date=start, end_date=end)],
                metrics=[Metric(name="sessions")],
            )
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("GA4 fail %s (%s): %s", agency, pid, exc)
        return None
    if not resp.rows:
        return 0
    try:
        return int(resp.rows[0].metric_values[0].value)
    except (ValueError, IndexError, AttributeError):
        return None


def daily_sessions(agency: str, start_iso: str, end_iso: str) -> dict[str, int] | None:
    """Sessions per calendar day for [start_iso, end_iso] (inclusive), keyed
    by 'YYYY-MM-DD'. One report call covers a whole range, so the engine can
    bucket it into weeks (current + trend) and sum it for YTD without 50+
    per-week calls.

    Returns {} for a valid empty range (a real zero, NOT an error) and None on
    any GA4 failure so the caller can fall back to Agency Analytics.
    """
    pid = GA4_PROPERTY_IDS.get(agency)
    if not pid:
        return None
    try:
        resp = _client().run_report(
            RunReportRequest(
                property=f"properties/{pid}",
                date_ranges=[DateRange(start_date=start_iso, end_date=end_iso)],
                dimensions=[Dimension(name="date")],
                metrics=[Metric(name="sessions")],
            )
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("GA4 daily fail %s (%s): %s", agency, pid, exc)
        return None
    out: dict[str, int] = {}
    for row in resp.rows:
        try:
            ymd = row.dimension_values[0].value  # 'YYYYMMDD'
            iso = f"{ymd[0:4]}-{ymd[4:6]}-{ymd[6:8]}"
            out[iso] = int(row.metric_values[0].value)
        except (ValueError, IndexError, AttributeError):
            continue
    return out
