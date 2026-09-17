"""Agency configuration: per-agency identifiers for each upstream source.

Secrets (HL PITs, QBO refresh tokens, Teamwork API token, service account JSON)
live in Secret Manager and are fetched at runtime, not stored here.

Non-secret IDs (HL location IDs, pipeline IDs, stage IDs, GA4 property IDs)
live in this file because they are config, not credentials, and tracking them
in source control gives us auditable change history.
"""

from typing import TypedDict


# ---------------------------------------------------------------------------
# Agency keys used everywhere in the codebase.
# ---------------------------------------------------------------------------
# Tracked agencies. IPA and TMSA dropped 2026-09-17 (no longer tracked); their
# per-source config entries below are retained but unused so this is reversible.
AGENCIES = ("FANNIT", "HMC")


# ---------------------------------------------------------------------------
# HighLevel V2 API config
#
# - location_id:    sub-account ID; required in path/query of every HL V2 call
# - secret_name:    name of the Secret Manager secret holding the PIT for this
#                   location (PITs are location-scoped; one per sub-account)
# - pipeline_id:    the pipeline that drives discovery/strategy/won counts
#                   for this agency. Per Chris (2026-04-27): FANNIT uses
#                   "2: Active Sales", others use "💸 SALES PIPELINE"
# - won_stage_id:   the stage in pipeline_id that signals a closed-won deal
# ---------------------------------------------------------------------------
class HighLevelAgencyConfig(TypedDict):
    location_id: str
    secret_name: str
    pipeline_id: str
    pipeline_name: str
    won_stage_id: str


HIGHLEVEL: dict[str, HighLevelAgencyConfig] = {
    "FANNIT": {
        "location_id": "q2x8tokGpDhlOu8bgVLN",
        "secret_name": "highlevel-pit-fannit",
        "pipeline_id": "OeYgK000wVzh0pxH3edY",
        "pipeline_name": "2: Active Sales",
        "won_stage_id": "ff032485-ffef-4a8f-91b7-1f5db6596678",  # Closed - Won
    },
    "HMC": {
        "location_id": "LAkDi1yul6kxjPcqkLxb",
        "secret_name": "highlevel-pit-hmc",
        "pipeline_id": "UwK3pSQmvBJGzeNPfmoJ",
        "pipeline_name": "💸 SALES PIPELINE",
        "won_stage_id": "45fa598d-26a8-4b82-81aa-55242518a2cd",  # 💸 Closed and Won
    },
    "TMSA": {
        "location_id": "Fj5y9oD9dIJWGHRCeNCW",
        "secret_name": "highlevel-pit-tmsa",
        "pipeline_id": "5wfjI8JTTuesnVlFSWg5",
        "pipeline_name": "💸 SALES PIPELINE",
        "won_stage_id": "a2a57886-4de4-4701-9c21-8f6843facc28",  # 💸 Closed and Won
    },
    "IPA": {
        "location_id": "TTv1Bn2QeGBIKwcP72zA",
        "secret_name": "highlevel-pit-ipa",
        # Two "💸 SALES PIPELINE" pipelines exist in IPA. Chris confirmed
        # 2026-04-27: use the one with 63 opportunities (clean linear flow).
        "pipeline_id": "T4bqE5CGEaw8P2vU1H0z",
        "pipeline_name": "💸 SALES PIPELINE",
        "won_stage_id": "a8b13c1f-149c-49ff-a48e-876082b8a646",  # 🖐️ CLOSED AND WON
    },
}


# ---------------------------------------------------------------------------
# Calendar inclusion rules for discovery / strategy / planning call counts.
#
# Per Chris (2026-04-27):
#   - Calendar must be wired to the agency's named pipeline (calendar's
#     pipelineId == HIGHLEVEL[agency].pipeline_id).
#   - Calendar must be active (isActive=True).
#   - Calendar name must NOT contain "internal" (case-insensitive).
#   - Bucketing: name contains "discovery" -> discovery; name contains
#     "strategy" or "planning" -> strategy/planning.
#   - Appointment status must be "showed".
# ---------------------------------------------------------------------------
DISCOVERY_NAME_TOKENS = ("discovery",)
STRATEGY_NAME_TOKENS = ("strategy", "planning")
EXCLUDED_NAME_TOKENS = ("internal",)
APPOINTMENT_SHOWED_STATUS = "showed"


# ---------------------------------------------------------------------------
# GA4 config
#
# One property per agency. Service account auth (single shared SA) granted
# Viewer on each property out-of-band by Chris.
# ---------------------------------------------------------------------------
GA4_PROPERTY_IDS: dict[str, str] = {
    "FANNIT": "319269316",
    "HMC": "464894002",
    "TMSA": "473651067",
    "IPA": "496676704",
}
GA4_METRIC = "sessions"


# ---------------------------------------------------------------------------
# Teamwork config
#
# Single shared instance at fannit.teamwork.com. Per Chris: filter projects
# by Category = agency name AND Tag = "Onboarding".
# ---------------------------------------------------------------------------
TEAMWORK_DOMAIN = "fannit.teamwork.com"

# Confirmed via Teamwork API 2026-05-18:
#   /projects/api/v3/projectcategories.json and /tags.json
TEAMWORK_ONBOARDING_TAG_ID = 117305  # tag named "Onboarding"

# Map agency key -> Teamwork project Category ID (more robust than name match;
# note IPA's real category name is "Inspired Painter Marketing Agency").
TEAMWORK_AGENCY_CATEGORY_ID: dict[str, int] = {
    "FANNIT": 35408,  # "FANNIT"
    "HMC": 35409,     # "Hardscape Marketing Crew"
    "TMSA": 35410,    # "The Med Spa Agency"
    "IPA": 35411,     # "Inspired Painter Marketing Agency"
}


# ---------------------------------------------------------------------------
# QuickBooks Online config
#
# IPA does not have QBO at time of build. AR / Cash Collected / Cash on Hand
# render as "—" for IPA until that's set up.
#
# Realm IDs and refresh tokens come from Secret Manager at runtime. Shared
# Intuit developer app's client_id / client_secret also from Secret Manager.
# ---------------------------------------------------------------------------
QBO_AGENCIES_WITH_ACCESS = ("FANNIT", "TMSA", "HMC")


# ---------------------------------------------------------------------------
# Discovery calendars (source-first, 2026-09-16)
#
# The "Discovery Calls" KPI mirrors each subaccount's "Discovery Shown"
# widget on its HighLevel "Sales Performance Dashboard": count of appointments
# on ONE specific calendar with appointmentStatus == "showed", by start date.
# The public API does not expose the widget, so we reproduce it from the
# calendars/events endpoint against the exact calendar id below.
#
# FANNIT is confirmed (name "Discovery Call with FANNIT") and validated.
# HMC/TMSA/IPA are best-guess: the single active, agency-branded discovery
# calendar. Each subaccount has several active discovery-ish calendars, so
# these three need owner confirmation of which one the widget actually counts.
# ---------------------------------------------------------------------------
DISCOVERY_CALENDAR_ID: dict[str, str] = {
    "FANNIT": "BYLQV8YLL5yWU9cPSKcW",  # "Discovery Call with FANNIT" (confirmed 2026-09-16)
    "HMC": "wv99MSr9Nj8ruZz83OYB",     # "Hardscape Marketing Crew Discovery Call" (CONFIRM)
    "TMSA": "Kv2O6QUrBmZKdBmUIJ2n",    # "Discovery Call" (CONFIRM; also "Cold Outreach Discovery Call")
    "IPA": "3NrLbhWHOnEEzEgzRTfC",     # "Book a Discovery Call" (CONFIRM; also "Coldoutreach Discovery Call")
}
DISCOVERY_SHOWN_STATUS = "showed"


# ---------------------------------------------------------------------------
# Agency Analytics — GA4 sessions fallback (white-label reporting.fannit.com)
#
# Only used when the GA4 pull errors; a legitimate zero-sessions week is NOT
# a fallback trigger. The API key lives in Secret Manager (not yet created);
# until then the fallback is inert and GA4 remains primary.
# ---------------------------------------------------------------------------
AGENCY_ANALYTICS_SECRET_NAME = "agency-analytics-api-key"
AGENCY_ANALYTICS_BASE = "https://api.agencyanalytics.com/api/v1"
AGENCY_ANALYTICS_CLIENT_ID: dict[str, str] = {
    "FANNIT": "511746",
    "HMC": "1489824",
    "TMSA": "1554167",
    "IPA": "1631621",
}


# ---------------------------------------------------------------------------
# QBO financials via FANNIT Command (2026-09-16)
#
# This service does NOT talk to Intuit. FANNIT Command owns the production QBO
# connection and exposes the three financial figures over the SAME shared
# secret this service already holds (env EOS_SCORECARD_SECRET, secret
# `eos-api-shared-secret`). Re-authing Intuit here would invalidate Command's
# refresh token, so never mint a QBO grant from this project.
#
# Only FANNIT (Fannit LLC realm) is available. HMC/TMSA/IPA render the QBO
# trio as "unavailable".
# ---------------------------------------------------------------------------
COMMAND_FINANCIALS_URL = (
    "https://fannit-command-vqqa7p2eiq-uc.a.run.app/executive/api/financials"
)
COMMAND_SECRET_ENV = "EOS_SCORECARD_SECRET"
QBO_AGENCIES = ("FANNIT",)


# ---------------------------------------------------------------------------
# Google Sheet (workbook hosting goals + the churn stat)
#
# Source-first: the dashboard reads the sheet ONLY for annual goals (col F of
# each KPI row) and for the single company-wide churn value (Stats!B19).
# Everything else comes from live sources. The snapshot job still writes the
# operational weekly cells for the legacy manual view.
# ---------------------------------------------------------------------------
SCORECARD_SHEET_ID = "1QyyYNoNR05V8hxjGSBYfvPqWANx37kJiGw3ePx-hz8c"
SCORECARD_TAB_NAME = "2026 Scorecard"
UPSELLS_CHURN_TAB_GID = 130117843

# Churn: one company-wide value, shown identically on every agency + rollup.
CHURN_TAB_NAME = "Stats"
CHURN_CELL = "B19"
