"""Source-first KPI engine.

For an (agency, week_label) it computes, per operational/financial KPI:
  - value : the selected week's number
  - ytd   : Jan 1 -> selected-week-end (sum for incremental, latest for snapshot)
  - weeks : up to 8 trailing weeks for the trend strip
  - source: provenance label shown on the card
  - status: "live" (sourced) or "unavailable" (source failed / no data)

Nothing here touches the Google Sheet. Goals and the churn stat are layered on
top by src/sheets/scorecard.py. Efficiency: each source is pulled with ONE
range/bulk call per render where possible (GA4 daily report, HL event range,
one won-opportunity fetch, one Command call per week window), then bucketed
locally. Results are cached per (agency, week): short TTL for the live week,
long TTL for immutable past weeks.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime

from .. import weeks as W
from ..config import QBO_AGENCIES
from . import agency_analytics, ga4, highlevel, qbo, teamwork

log = logging.getLogger("eos-scorecard.kpi_engine")

# KPI labels the engine supplies (must match scorecard.KPI_LABELS spellings).
TRAFFIC = "Website / LP Traffic"
DISCOVERY = "Discovery Calls"
NEW_SALES = "New Sales (15% of Discovery)"
ONBOARDING = "Clients in Onboarding"
AR_PAST_30 = "Total $ AR Past 30 Days"
CASH_COLLECTED = "Cash Collected"
CASH_ON_HAND = "Cash on Hand"

ENGINE_LABELS = (
    TRAFFIC, DISCOVERY, NEW_SALES, ONBOARDING,
    AR_PAST_30, CASH_COLLECTED, CASH_ON_HAND,
)

_LIVE_TTL = 300       # 5 min for the current / most-recent week
_PAST_TTL = 6 * 3600  # 6 h for immutable completed weeks
_cache: dict[tuple[str, str], tuple[float, dict]] = {}


@dataclass
class KpiResult:
    value: float | None = None
    ytd: float | None = None
    weeks: list[dict] = field(default_factory=list)  # [{"date": "9/8", "value": 3}]
    source: str = "—"
    status: str = "unavailable"  # "live" | "unavailable"


def _clamp_end_iso(sun_iso: str) -> str:
    """Command rejects a future `end`; clamp a week's Sunday to today."""
    today = W._today_pt().isoformat()
    return min(sun_iso, today)


def _windows(labels: list[str]) -> dict[str, tuple[datetime, datetime]]:
    return {lbl: W.week_window(lbl) for lbl in labels}


def _count_in_weeks(
    stamps: list[datetime], windows: dict[str, tuple[datetime, datetime]]
) -> dict[str, int]:
    counts = {lbl: 0 for lbl in windows}
    for dt in stamps:
        for lbl, (s, e) in windows.items():
            if s <= dt < e:
                counts[lbl] += 1
                break
    return counts


# --------------------------------------------------------------------------- #
# GA4 traffic (with Agency Analytics fallback)
# --------------------------------------------------------------------------- #
def _traffic(agency: str, week_label: str, labels: list[str]) -> KpiResult:
    start_iso = W.year_start_iso()
    _, sun_iso = W.week_dates(week_label)
    end_iso = _clamp_end_iso(sun_iso)

    source = "GA4"
    daily = ga4.daily_sessions(agency, start_iso, end_iso)
    if daily is None:
        daily = agency_analytics.daily_sessions(agency, start_iso, end_iso)
        source = "Agency Analytics"
    if daily is None:
        return KpiResult(source="GA4", status="unavailable")

    def week_sum(lbl: str) -> int:
        mon_iso, wsun_iso = W.week_dates(lbl)
        return sum(v for d, v in daily.items() if mon_iso <= d <= wsun_iso)

    trend = [{"date": lbl, "value": week_sum(lbl)} for lbl in labels]
    return KpiResult(
        value=week_sum(week_label),
        ytd=sum(daily.values()),
        weeks=trend,
        source=source,
        status="live",
    )


# --------------------------------------------------------------------------- #
# HighLevel: Discovery (shown appts) + New Sales (won)
# --------------------------------------------------------------------------- #
def _discovery(agency: str, week_label: str, windows: dict) -> KpiResult:
    start_pt = W.week_window(W.all_week_labels()[0])[0]  # Jan first-Monday 00:00
    end_pt = windows[week_label][1]
    starts = highlevel.discovery_shown_starts(agency, start_pt, end_pt)
    if starts is None:
        return KpiResult(source="HighLevel", status="unavailable")
    counts = _count_in_weeks(starts, windows)
    trend = [{"date": lbl, "value": counts[lbl]} for lbl in windows]
    ytd = sum(1 for dt in starts if start_pt <= dt < end_pt)
    return KpiResult(
        value=counts.get(week_label, 0), ytd=ytd, weeks=trend,
        source="HighLevel", status="live",
    )


def _new_sales(agency: str, week_label: str, windows: dict) -> KpiResult:
    won = highlevel.won_dates(agency)
    if won is None:
        return KpiResult(source="HighLevel", status="unavailable")
    year_start = W.week_window(W.all_week_labels()[0])[0]
    end_pt = windows[week_label][1]
    counts = _count_in_weeks(won, windows)
    trend = [{"date": lbl, "value": counts[lbl]} for lbl in windows]
    ytd = sum(1 for dt in won if year_start <= dt < end_pt)
    return KpiResult(
        value=counts.get(week_label, 0), ytd=ytd, weeks=trend,
        source="HighLevel", status="live",
    )


# --------------------------------------------------------------------------- #
# Teamwork onboarding (live snapshot; no history)
# --------------------------------------------------------------------------- #
def _onboarding(agency: str, week_label: str, labels: list[str]) -> KpiResult:
    count = teamwork.onboarding_count(agency)
    if count is None:
        return KpiResult(source="Teamwork", status="unavailable")
    # The count is "as of now", so it only applies to the most-recent completed
    # week and the in-progress week. Older weeks have no history.
    default_mon = W.parse_label(W.default_week_label())

    def applies(lbl: str) -> bool:
        return W.parse_label(lbl) >= default_mon

    trend = [
        {"date": lbl, "value": count if applies(lbl) else None} for lbl in labels
    ]
    value = count if applies(week_label) else None
    return KpiResult(
        value=value, ytd=count, weeks=trend, source="Teamwork", status="live",
    )


# --------------------------------------------------------------------------- #
# Financials via FANNIT Command (FANNIT only)
# --------------------------------------------------------------------------- #
def _financials(agency: str, week_label: str, labels: list[str]) -> dict:
    unavailable = {
        AR_PAST_30: KpiResult(source="QuickBooks (FANNIT only)", status="unavailable"),
        CASH_COLLECTED: KpiResult(source="QuickBooks (FANNIT only)", status="unavailable"),
        CASH_ON_HAND: KpiResult(source="QuickBooks (FANNIT only)", status="unavailable"),
    }
    if agency not in QBO_AGENCIES:
        return unavailable

    src = "FANNIT Command (QBO)"
    # One Command call per week window gives all three figures for that week.
    per_week: dict[str, dict | None] = {}
    for lbl in labels:
        mon_iso, sun_iso = W.week_dates(lbl)
        per_week[lbl] = qbo.financials(mon_iso, _clamp_end_iso(sun_iso))

    sel = per_week.get(week_label)

    # Cash Collected (incremental): YTD is one Jan1->end call.
    ytd_body = qbo.financials(W.year_start_iso(), _clamp_end_iso(W.week_dates(week_label)[1]))

    def trend(field: str) -> list[dict]:
        return [
            {"date": lbl, "value": (per_week[lbl] or {}).get(field)} for lbl in labels
        ]

    def snap_result(field: str) -> KpiResult:
        val = (sel or {}).get(field)
        status = "live" if sel is not None else "unavailable"
        return KpiResult(value=val, ytd=val, weeks=trend(field), source=src, status=status)

    cash_collected = KpiResult(
        value=(sel or {}).get("cash_collected"),
        ytd=(ytd_body or {}).get("cash_collected"),
        weeks=trend("cash_collected"),
        source=src,
        status="live" if sel is not None else "unavailable",
    )

    return {
        AR_PAST_30: snap_result("ar_past_30"),
        CASH_COLLECTED: cash_collected,
        CASH_ON_HAND: snap_result("cash_on_hand"),
    }


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def compute(agency: str, week_label: str) -> dict[str, KpiResult]:
    """All engine KPIs for the agency/week. Cached per (agency, week)."""
    key = (agency, week_label)
    now = time.time()
    hit = _cache.get(key)
    ttl = _LIVE_TTL if _is_live_week(week_label) else _PAST_TTL
    if hit and now - hit[0] < ttl:
        return hit[1]

    labels = W.trailing_labels(week_label, 8)
    windows = _windows(labels)

    results: dict[str, KpiResult] = {}
    for label, fn in (
        (TRAFFIC, lambda: _traffic(agency, week_label, labels)),
        (DISCOVERY, lambda: _discovery(agency, week_label, windows)),
        (NEW_SALES, lambda: _new_sales(agency, week_label, windows)),
        (ONBOARDING, lambda: _onboarding(agency, week_label, labels)),
    ):
        try:
            results[label] = fn()
        except Exception as exc:  # noqa: BLE001 -- one KPI failure is not fatal
            log.warning("engine %s %s/%s failed: %s", label, agency, week_label, exc)
            results[label] = KpiResult(status="unavailable")

    try:
        results.update(_financials(agency, week_label, labels))
    except Exception as exc:  # noqa: BLE001
        log.warning("engine financials %s/%s failed: %s", agency, week_label, exc)
        for lbl in (AR_PAST_30, CASH_COLLECTED, CASH_ON_HAND):
            results.setdefault(lbl, KpiResult(status="unavailable"))

    _cache[key] = (now, results)
    return results


def _is_live_week(week_label: str) -> bool:
    """The in-progress week and the most-recent completed week are still
    changing; everything older is immutable."""
    try:
        return W.parse_label(week_label) >= W.parse_label(W.default_week_label())
    except (ValueError, TypeError):
        return True
