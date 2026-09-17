"""Reader for the `2026 Scorecard` tab in the All Accounts & KPIs workbook.

Each agency block in the tab is laid out the same way:

  Row N          | <AGENCY NAME>     | (year header band)
  Row N+1        | Own | KPI         | Goal (col F, annual) | Actual (G, YTD) | Hit (H) | per-month bands
  Rows N+2..N+9  | 8 KPI rows in fixed order

The per-month bands repeat: [Goal | week1 | week2 | week3 | (week4|week5) | Calendar Month Actual]
where the weekN cells are the actual weekly values written by the snapshot job
or entered manually.

This reader pulls per KPI:
  - annual_goal (col F)
  - ytd_actual (col G)
  - hit_pct (col H)
  - current_week_value: rightmost populated weekly cell (skips Goal / CMA cells)
  - current_week_date: the header label for that column (e.g. "4/27")
  - weekly_goal: annual_goal / 52 for incremental metrics; annual_goal for
    snapshot / rate metrics
  - weekly_hit_pct: current_week_value vs weekly_goal
"""

from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Optional

from .client import get_sheets_service
from .. import weeks as W
from ..config import (
    CHURN_CELL,
    CHURN_TAB_NAME,
    SCORECARD_SHEET_ID,
    SCORECARD_TAB_NAME,
)
from ..sources import kpi_engine

CHURN_LABEL = "Churn Over Last 12 Months"

SCORECARD_YEAR = 2026  # tab is named "2026 Scorecard"; revisit when year rolls over

VALID_PERIODS = ("Weekly", "LastMonth", "Q1", "Q2", "Q3", "Q4", "YTD")


def _today() -> date:
    """Wrapper so tests can stub. Returns the local-date today."""
    return date.today()


def last_completed_week_label(today: date | None = None) -> str:
    """The M/D label for the most recent Monday strictly before `today`.

    The sheet uses Monday-as-week-end labels (e.g. '4/27', '5/4'). If today
    is a Monday, the current week is just starting and we treat 'last
    completed' as the previous Monday (7 days earlier). This is the default
    week the dashboard renders so all KPI cards align on one consistent week
    instead of each landing on whichever weekly cell happens to be populated.
    """
    t = today or _today()
    dsm = t.weekday()  # Mon=0..Sun=6
    if dsm == 0:
        d = t - timedelta(days=7)
    else:
        d = t - timedelta(days=dsm)
    return f"{d.month}/{d.day}"


# Agency block layout in the 2026 Scorecard tab.
# Confirmed during scoping (2026-04-27); TMSA / IPA offsets to be verified
# the first time the reader runs against them.
# IPA and TMSA dropped 2026-09-17 (no longer tracked). Their block offsets are
# kept here commented for easy restore.
AGENCY_BLOCKS: dict[str, dict[str, int]] = {
    "FANNIT": {"header_row": 36, "kpi_rows_start": 38},
    "HMC": {"header_row": 73, "kpi_rows_start": 75},
    # "TMSA": {"header_row": 94, "kpi_rows_start": 96},
    # "IPA": {"header_row": 115, "kpi_rows_start": 117},
}


KPI_LABELS: list[str] = [
    "Website / LP Traffic",
    "Discovery Calls",
    "New Sales (15% of Discovery)",
    "Clients in Onboarding",
    "Churn Over Last 12 Months",
    "Total $ AR Past 30 Days",
    "Cash Collected",
    "Cash on Hand",
]

KPI_DATA_SOURCE: dict[str, str] = {
    "Website / LP Traffic": "GA4",
    "Discovery Calls": "HighLevel Calendar",
    "New Sales (15% of Discovery)": "HighLevel Pipeline",
    "Clients in Onboarding": "Teamwork",
    "Churn Over Last 12 Months": "Upsells & Churn Sheet",
    "Total $ AR Past 30 Days": "QuickBooks Online",
    "Cash Collected": "QuickBooks P&L",
    "Cash on Hand": "QuickBooks Balance Sheet",
}

# Some KPIs use percent values (Churn, Hit), some are dollars, some are counts.
# Drives display formatting on the frontend.
KPI_FORMAT: dict[str, str] = {
    "Website / LP Traffic": "number",
    "Discovery Calls": "number",
    "New Sales (15% of Discovery)": "number",
    "Clients in Onboarding": "number",
    "Churn Over Last 12 Months": "percent",
    "Total $ AR Past 30 Days": "currency",
    "Cash Collected": "currency",
    "Cash on Hand": "currency",
}

# Metric behavior, drives how weekly_goal and hit % are computed per KPI:
#   incremental: sum across weeks; weekly_goal = annual_goal / 52
#   snapshot:    point-in-time; weekly_goal = annual_goal (the target balance)
#   rate:        trailing-12mo rate; weekly_goal = annual_goal (the target rate)
KPI_TYPE: dict[str, str] = {
    "Website / LP Traffic": "incremental",
    "Discovery Calls": "incremental",
    "New Sales (15% of Discovery)": "incremental",
    "Clients in Onboarding": "snapshot",
    "Churn Over Last 12 Months": "rate",
    "Total $ AR Past 30 Days": "snapshot",
    "Cash Collected": "incremental",
    "Cash on Hand": "snapshot",
}

# Cells in row 37 (header row) that are NOT weekly date columns. Used to
# distinguish weekly cells from Goal / Calendar Month Actual cells.
NON_WEEK_HEADER_LABELS = {
    "goal",
    "actual",
    "hit",
    "kpi",
    "own",
    "calendar month actual",
    "",
}


@dataclass
class WeekValue:
    date: str
    value: float | None


@dataclass
class Kpi:
    label: str
    source: str
    fmt: str  # "number" / "currency" / "percent"
    metric_type: str  # "incremental" / "snapshot" / "rate"
    annual_goal: float | None
    ytd_actual: float | None
    hit_pct: float | None  # YTD against annual goal (formula in sheet)
    current_week_value: float | None
    current_week_date: str | None
    weekly_goal: float | None  # pro-rated for incremental, target for snapshot/rate
    weekly_hit_pct: float | None  # current week against weekly goal
    is_live: bool = False  # True when value came from a live source pull
    status: str = "unavailable"  # "live" | "sheet" | "unavailable"
    weeks: list[WeekValue] = field(default_factory=list)  # trailing weeks trend


def _to_float(v) -> float | None:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s:
        return None
    # Tolerate stray "$", ",", "%" if a cell happened to be string-formatted.
    s = s.replace("$", "").replace(",", "").replace("%", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def _col_index_to_letter(idx_1based: int) -> str:
    s = ""
    n = idx_1based
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _get_week_columns(header_row: int, max_col: str = "DZ") -> list[tuple[int, str]]:
    """Reads the agency's KPI-header row (e.g. row 37 for FANNIT) and returns
    [(absolute_col_index_1based, date_label), ...] for cells that are weekly
    date columns. Skips Goal, Hit, Calendar Month Actual, and blanks.
    """
    rng = f"'{SCORECARD_TAB_NAME}'!I{header_row}:{max_col}{header_row}"
    svc = get_sheets_service()
    resp = (
        svc.spreadsheets()
        .values()
        .get(
            spreadsheetId=SCORECARD_SHEET_ID,
            range=rng,
            valueRenderOption="FORMATTED_VALUE",
        )
        .execute()
    )
    rows = resp.get("values", [])
    if not rows:
        return []
    headers = rows[0]
    out: list[tuple[int, str]] = []
    for i, h in enumerate(headers):
        col_idx = 9 + i  # I = column 9 (1-based)
        if h is None:
            continue
        # Headers can have line breaks (e.g. "Calendar\nMonth\nActual").
        # Collapse all whitespace before checking against our skip-list.
        normalized = " ".join(str(h).split()).lower()
        if not normalized:
            continue
        if normalized in NON_WEEK_HEADER_LABELS:
            continue
        # Only treat as a week column if the label looks like a date
        # (contains a slash, e.g. "1/5", "4/27"). This is a stricter check
        # than just "skip known labels" and protects against unexpected
        # header content leaking through.
        if "/" not in normalized:
            continue
        out.append((col_idx, normalized))
    return out


def _compute_weekly_goal(annual_goal: float | None, metric_type: str) -> float | None:
    if annual_goal is None:
        return None
    if metric_type == "incremental":
        return annual_goal / 52.0
    return annual_goal  # snapshot or rate


def _compute_weekly_hit_pct(
    current: float | None, weekly_goal: float | None
) -> float | None:
    if current is None or weekly_goal is None or weekly_goal == 0:
        return None
    return current / weekly_goal


def _read_goals(agency: str) -> dict[str, float | None]:
    """Annual goals (col F) for the agency's 8 KPI rows, keyed by KPI label.

    This is one of only two sheet reads left in the read path (the other is
    churn). Everything else is sourced live.
    """
    block = AGENCY_BLOCKS[agency]
    start = block["kpi_rows_start"]
    end = start + len(KPI_LABELS) - 1
    rng = f"'{SCORECARD_TAB_NAME}'!E{start}:F{end}"
    resp = (
        get_sheets_service()
        .spreadsheets()
        .values()
        .get(
            spreadsheetId=SCORECARD_SHEET_ID,
            range=rng,
            valueRenderOption="UNFORMATTED_VALUE",
        )
        .execute()
    )
    rows = resp.get("values", [])
    goals: dict[str, float | None] = {}
    for i, label in enumerate(KPI_LABELS):
        row = rows[i] if i < len(rows) else []
        goals[label] = _to_float(row[1]) if len(row) > 1 else None
    return goals


def _read_churn() -> float | None:
    """The single company-wide churn value from Stats!B19."""
    rng = f"'{CHURN_TAB_NAME}'!{CHURN_CELL}"
    resp = (
        get_sheets_service()
        .spreadsheets()
        .values()
        .get(
            spreadsheetId=SCORECARD_SHEET_ID,
            range=rng,
            valueRenderOption="UNFORMATTED_VALUE",
        )
        .execute()
    )
    rows = resp.get("values", [])
    if rows and rows[0]:
        return _to_float(rows[0][0])
    return None


def read_agency_kpis(
    agency: str,
    week_label: str | None = None,
    weeks_history: int = 8,
) -> tuple[list[Kpi], list[str]]:
    """Returns (kpis, available_week_labels) for one agency and week.

    Source-first: the operational and financial KPIs come live from
    src.sources.kpi_engine; annual goals come from the sheet (col F); churn is
    the single Stats!B19 value. YTD and Hit % are computed from source. The
    week list is the deterministic Monday sequence (src.weeks), not whatever
    sheet cells happen to be populated.
    """
    if agency not in AGENCY_BLOCKS:
        raise ValueError(
            f"Agency block for '{agency}' not yet mapped in AGENCY_BLOCKS. "
            f"Available: {list(AGENCY_BLOCKS)}"
        )

    target_label = week_label or W.default_week_label()
    goals = _read_goals(agency)
    churn_value = _read_churn()
    engine = kpi_engine.compute(agency, target_label)

    out: list[Kpi] = []
    for label in KPI_LABELS:
        annual_goal = goals.get(label)
        metric_type = KPI_TYPE.get(label, "incremental")
        weekly_goal = _compute_weekly_goal(annual_goal, metric_type)

        if label == CHURN_LABEL:
            # One company-wide trailing-12mo rate; same on every agency.
            value = churn_value
            ytd = churn_value
            weeks = []
            source = "Stats sheet"
            status = "sheet"
            is_live = False
        elif label in engine:
            r = engine[label]
            value = r.value
            ytd = r.ytd
            weeks = [WeekValue(date=w["date"], value=w["value"]) for w in r.weeks]
            source = r.source
            status = r.status
            is_live = status == "live"
        else:
            value = ytd = None
            weeks = []
            source = KPI_DATA_SOURCE.get(label, "—")
            status = "unavailable"
            is_live = False

        out.append(
            Kpi(
                label=label,
                source=source,
                fmt=KPI_FORMAT.get(label, "number"),
                metric_type=metric_type,
                annual_goal=annual_goal,
                ytd_actual=ytd,
                hit_pct=_compute_weekly_hit_pct(ytd, annual_goal),
                current_week_value=value,
                current_week_date=target_label,
                weekly_goal=weekly_goal,
                weekly_hit_pct=_compute_weekly_hit_pct(value, weekly_goal),
                is_live=is_live,
                status=status,
                weeks=weeks[-weeks_history:],
            )
        )

    return out, W.all_week_labels()


def kpis_to_payload(agency: str, week_label: str | None = None) -> dict:
    """JSON-friendly payload for /api/scorecard. Defaults to the most recent
    completed week so every card lands on the same aligned week."""
    kpis, available_weeks = read_agency_kpis(agency, week_label=week_label)
    selected = week_label or W.default_week_label()
    return {
        "agency": agency,
        "selected_week": selected,
        "available_weeks": available_weeks,
        "kpis": [asdict(k) for k in kpis],
    }
