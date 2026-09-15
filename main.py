"""FANNIT EOS L10 Scorecard - main Cloud Run entry point.

Hosts:
  - Frontend (static index.html + assets) at /
  - Dashboard read API at /api/*
  - Internal snapshot job trigger at /internal/snapshot (called by Cloud Scheduler)
"""

import os
import logging
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from src.auth import require_read_auth, require_secret_header
from src.sheets.scorecard import AGENCY_BLOCKS, kpis_to_payload

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("eos-scorecard")

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="FANNIT EOS Scorecard", version="0.1.0")


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/api/agencies", dependencies=[Depends(require_read_auth)])
def list_agencies():
    """Which agencies the dashboard knows how to render right now."""
    return {"agencies": sorted(AGENCY_BLOCKS.keys())}


@app.get("/api/scorecard", dependencies=[Depends(require_read_auth)])
def get_scorecard(agency: str = "FANNIT", date: str | None = None):
    """Returns the scorecard payload for one agency for a specific week.

    Args:
        agency: FANNIT / HMC / TMSA / IPA.
        date: M/D label of the week to display (e.g. '4/27'). If omitted,
            defaults to last completed week (most recent Monday strictly
            before today).
    """
    if agency not in AGENCY_BLOCKS:
        return JSONResponse(
            status_code=404,
            content={
                "error": "agency_not_mapped",
                "agency": agency,
                "available": sorted(AGENCY_BLOCKS.keys()),
            },
        )
    try:
        return kpis_to_payload(agency, week_label=date)
    except Exception as exc:  # noqa: BLE001
        log.exception("Failed to read scorecard for %s", agency)
        return JSONResponse(
            status_code=500,
            content={"error": "sheet_read_failed", "agency": agency, "detail": str(exc)},
        )


@app.post("/internal/snapshot", dependencies=[Depends(require_secret_header)])
def trigger_snapshot(date: str | None = None):
    """Pull live source metrics and write them into the 2026 Scorecard tab.

    Triggered weekly by Cloud Scheduler (Mon 06:00 PT) or manually. `date` is
    an optional M/D week label; defaults to last completed week. Requires the
    X-EOS-Secret header; the signed iframe token is NOT accepted here.
    """
    try:
        from src.snapshot import run_snapshot

        return run_snapshot(week_label=date)
    except Exception as exc:  # noqa: BLE001
        log.exception("snapshot failed")
        return JSONResponse(
            status_code=500, content={"error": "snapshot_failed", "detail": str(exc)}
        )


# Serve the static frontend last so /api/* and /healthz win when paths overlap.
if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
else:
    log.warning("Static directory %s does not exist; frontend not mounted", STATIC_DIR)

    @app.get("/")
    def fallback_root():
        return JSONResponse(
            {
                "service": "fannit-eos-scorecard",
                "status": "scaffold",
                "message": "Static dir missing; backend up.",
            }
        )
