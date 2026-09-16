"""HighLevel V2 source.

Provides three weekly metrics per agency:
  - discovery_calls: calendar events shown on discovery calendars
  - strategy_calls:  calendar events shown on strategy/planning calendars
  - new_sales:        opportunities that became "won" during the week

Calendar inclusion rule (Chris, 2026-04-27, with the pipeline-link clause
relaxed because the HL calendar object does NOT expose pipelineId):
  - calendar.isActive is True
  - calendar.name does NOT contain "internal" (case-insensitive)
  - discovery bucket: name contains "discovery"
  - strategy bucket:  name contains "strategy" or "planning"
  - appointmentStatus == "showed"

Week window: [Monday 00:00, next Monday 00:00) in America/Los_Angeles.
Event startTime is ISO8601 with offset; opportunity lastStatusChangeAt is
UTC ("...Z"). Both normalized to PT before the window check.
"""

from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

from ..config import DISCOVERY_CALENDAR_ID, DISCOVERY_SHOWN_STATUS, HIGHLEVEL
from .secrets import get_secret

log = logging.getLogger("eos-scorecard.highlevel")

BASE = "https://services.leadconnectorhq.com"
CAL_VERSION = "2021-04-15"
OPP_VERSION = "2021-07-28"
PT = ZoneInfo("America/Los_Angeles")
HTTP_TIMEOUT = 30

DISCOVERY_TOKENS = ("discovery",)
STRATEGY_TOKENS = ("strategy", "planning")
EXCLUDE_TOKENS = ("internal",)
SHOWED = "showed"


def _pit(agency: str) -> str:
    return get_secret(HIGHLEVEL[agency]["secret_name"])


def _headers(agency: str, version: str) -> dict:
    return {
        "Authorization": f"Bearer {_pit(agency)}",
        "Version": version,
        "Accept": "application/json",
    }


def _classify(name: str) -> str | None:
    """Returns 'discovery', 'strategy', or None for a calendar name."""
    n = (name or "").lower()
    if any(t in n for t in EXCLUDE_TOKENS):
        return None
    if any(t in n for t in DISCOVERY_TOKENS):
        return "discovery"
    if any(t in n for t in STRATEGY_TOKENS):
        return "strategy"
    return None


def _list_calendars(agency: str) -> list[dict]:
    loc = HIGHLEVEL[agency]["location_id"]
    r = requests.get(
        f"{BASE}/calendars/",
        headers=_headers(agency, CAL_VERSION),
        params={"locationId": loc},
        timeout=HTTP_TIMEOUT,
    )
    r.raise_for_status()
    return r.json().get("calendars", [])


def _events(agency: str, calendar_id: str, start_ms: int, end_ms: int) -> list[dict]:
    loc = HIGHLEVEL[agency]["location_id"]
    r = requests.get(
        f"{BASE}/calendars/events",
        headers=_headers(agency, CAL_VERSION),
        params={
            "locationId": loc,
            "calendarId": calendar_id,
            "startTime": start_ms,
            "endTime": end_ms,
        },
        timeout=HTTP_TIMEOUT,
    )
    r.raise_for_status()
    return r.json().get("events", [])


def _won_opportunities(agency: str) -> list[dict]:
    cfg = HIGHLEVEL[agency]
    out: list[dict] = []
    page = 1
    while True:
        r = requests.get(
            f"{BASE}/opportunities/search",
            headers=_headers(agency, OPP_VERSION),
            params={
                "location_id": cfg["location_id"],
                "pipeline_id": cfg["pipeline_id"],
                "status": "won",
                "limit": 100,
                "page": page,
            },
            timeout=HTTP_TIMEOUT,
        )
        r.raise_for_status()
        body = r.json()
        batch = body.get("opportunities", [])
        out.extend(batch)
        total = (body.get("meta") or {}).get("total", len(out))
        if len(batch) < 100 or len(out) >= total:
            break
        page += 1
        if page > 50:  # safety valve
            break
    return out


def _to_pt(iso: str) -> datetime | None:
    if not iso:
        return None
    s = iso.strip()
    # Python's fromisoformat (3.11+) accepts trailing 'Z' and numeric offsets.
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=PT)
    return dt.astimezone(PT)


def discovery_shown_starts(
    agency: str, start_pt: datetime, end_pt: datetime
) -> list[datetime] | None:
    """PT start-datetimes of "shown" appointments on the agency's Discovery
    calendar (config.DISCOVERY_CALENDAR_ID) within [start_pt, end_pt).

    Mirrors the "Discovery Shown" widget: one calendar, appointmentStatus ==
    "showed", dated by appointment start. One range call serves a whole span,
    so the engine buckets it into weeks (current + trend) and sums for YTD.

    Returns None on failure (-> KPI "unavailable"), never an empty-list-as-error.
    """
    cal = DISCOVERY_CALENDAR_ID.get(agency)
    if not cal:
        return None
    try:
        evs = _events(
            agency,
            cal,
            int(start_pt.timestamp() * 1000),
            int(end_pt.timestamp() * 1000),
        )
    except requests.RequestException as exc:
        log.warning("HL discovery events fail %s: %s", agency, exc)
        return None
    out: list[datetime] = []
    for e in evs:
        if e.get("appointmentStatus") != DISCOVERY_SHOWN_STATUS:
            continue
        st = _to_pt(e.get("startTime", ""))
        if st is not None and start_pt <= st < end_pt:
            out.append(st)
    return out


def won_dates(agency: str) -> list[datetime] | None:
    """PT datetimes at which the agency's pipeline opportunities became won
    (lastStatusChangeAt while in the configured won stage). The full list is
    fetched once; the engine buckets it by week for current / trend / YTD.

    Returns None on failure (-> KPI "unavailable").
    """
    won_stage = HIGHLEVEL[agency]["won_stage_id"]
    try:
        opps = _won_opportunities(agency)
    except requests.RequestException as exc:
        log.warning("HL opportunities fail %s: %s", agency, exc)
        return None
    out: list[datetime] = []
    for o in opps:
        if o.get("pipelineStageId") != won_stage:
            continue
        changed = _to_pt(o.get("lastStatusChangeAt", ""))
        if changed is not None:
            out.append(changed)
    return out


def weekly_metrics(agency: str, week_start_pt: datetime, week_end_pt: datetime) -> dict:
    """Back-compat helper for the snapshot job: {'discovery_calls',
    'new_sales', 'strategy_calls'} for the [start, end) week. Discovery now
    uses the specific Discovery calendar (not a name heuristic). Strategy is
    no longer a card and is always None. Each metric is None on its own
    failure (graceful degradation).
    """
    starts = discovery_shown_starts(agency, week_start_pt, week_end_pt)
    discovery = None if starts is None else len(starts)

    won = won_dates(agency)
    new_sales = (
        None
        if won is None
        else sum(1 for d in won if week_start_pt <= d < week_end_pt)
    )

    return {
        "discovery_calls": discovery,
        "strategy_calls": None,
        "new_sales": new_sales,
    }
