# FANNIT EOS L10 Scorecard — Administration & Management SOP

**Version:** 2026-09-17
**Status:** Active
**Owner:** Chris Fink (build owner). Operator escalation: see Section 12.

> Audience: technically competent maintainers unfamiliar with this codebase. For the architectural narrative and change log, see `BRIEF.md` in the repo root. This SOP is the operational runbook.

> **CURRENT STATE (2026-09-17):** Source-first is LIVE. KPIs are pulled live per week (GA4, HighLevel, Teamwork, and FANNIT Command for FANNIT financials); the sheet is read only for goals (col F) and churn (`Stats!B19`). The API is **gated** behind the shared secret (`X-EOS-Secret`) or the signed iframe token, not public. Deployed revision `eos-scorecard-00009-l4f` (commit `c2f141d`). Where sections below describe sheet-first sourcing or an ungated API, defer to `SCORECARD_REFINEMENT_BRIEF.md` (authoritative current spec).

---

## 1. System Overview

The EOS L10 Scorecard is a read-only weekly KPI dashboard for the four FANNIT-family agencies (FANNIT, TMSA, HMC, IPA). It pulls live metrics from GA4, HighLevel, and Teamwork, displays them per agency per week, and writes the pulled values back into the existing `2026 Scorecard` tab of the `All Accounts & KPIs - FANNIT` Google Sheet so the legacy manual view stays populated.

- **Where it lives:** Google Cloud Run, project `fannit-eos-scorecard`, region `us-central1`, single service `eos-scorecard`.
- **Triggers:** (a) HTTP page loads hit the read API on demand; (b) the snapshot job is triggered manually via `POST /internal/snapshot` today (weekly Cloud Scheduler cron is planned but not yet created).
- **Produces:** a JSON API + static dashboard UI, plus weekly-cell writes into the Google Sheet.
- **Consumers:** Chris and agency leadership viewing the dashboard; anyone who reads the `2026 Scorecard` sheet tab.

There are **no AI/LLM calls anywhere in this system.** It is pure API aggregation.

---

## 2. Architecture Diagram (text-based)

```
                 ┌─────────────────────── DASHBOARD READ PATH ───────────────────────┐
  Browser  ──►  GET /  (static/index.html, app.js, styles.css)
  Browser  ──►  GET /api/scorecard?agency=FANNIT&date=5/11
                      │
                      ▼
              src/sheets/scorecard.py  read_agency_kpis()
                      │  1. read goals + weekly cells from "2026 Scorecard" tab
                      │     (Google Sheets API v4, ADC = runtime SA)
                      │  2. if selected week == current/last-completed:
                      │        src/sources/aggregate.py  live_metrics()  (10-min TTL cache)
                      │             ├─ src/sources/ga4.py        ──► GA4 Data API (sessions)
                      │             ├─ src/sources/highlevel.py  ──► HighLevel V2 (calendars/events, opportunities)
                      │             └─ src/sources/teamwork.py   ──► Teamwork v3 (projects)
                      │        overrides Discovery / New Sales / Onboarding / Traffic
                      ▼
              JSON  { agency, selected_week, available_weeks, kpis[] }

                 ┌─────────────────────── SNAPSHOT WRITE PATH ───────────────────────┐
  Manual / (future Cloud Scheduler)  ──►  POST /internal/snapshot[?date=M/D]
                      ▼
              src/snapshot.py  run_snapshot()
                      │  for each agency: aggregate.live_metrics()
                      │  resolve week column from the KPI header row
                      │  Sheets values.batchUpdate  ──► writes Traffic / Discovery /
                      │                                  New Sales / Onboarding cells
                      ▼
              "2026 Scorecard" tab updated (legacy continuity)
```

External services touched: **Google Sheets API v4**, **GA4 Data API** (`analyticsdata.googleapis.com`), **GA4 Admin API** (`analyticsadmin.googleapis.com`, one-time SA grant only), **HighLevel V2 API** (`services.leadconnectorhq.com`), **Teamwork v3 API** (`fannit.teamwork.com`), **Google Secret Manager**. No AI model is called.

---

## 3. Environment & Infrastructure

### 3.1 Deployment platform

- **Cloud Run** managed service `eos-scorecard` in `fannit-eos-scorecard` / `us-central1`.
- Container: `python:3.11-slim`, `gunicorn` with a `uvicorn` worker, port `8080` (see `Dockerfile`).
- `--allow-unauthenticated` (public). Cloudflare Access in front is planned, not yet in place.
- Image registry: Artifact Registry `us-central1-docker.pkg.dev/fannit-eos-scorecard/eos-scorecard/`.
- Current live revision at time of writing: `eos-scorecard-00008-vqc`.
- Public URL: `https://eos-scorecard-btpczli7ra-uc.a.run.app`

### 3.2 Environment variables

Production sets only one env var on the Cloud Run service (see `cloudbuild.yaml`):

```
GCP_PROJECT=fannit-eos-scorecard      # used by src/sources/secrets.py
```

All other configuration is code (`src/config.py`), and all credentials are pulled from Secret Manager at runtime. `.env.example` documents the full local-dev variable surface but production does **not** use a `.env` file. Local dev only needs ADC (`gcloud auth application-default login`) plus, for source testing, access to Secret Manager.

### 3.3 Scheduled jobs

- **None exist yet.** `gcloud scheduler jobs list --project=fannit-eos-scorecard --location=us-central1` returns 0 items.
- Planned: a weekly Cloud Scheduler job hitting `POST /internal/snapshot`, Monday 06:00 America/Los_Angeles. Until that is created, the snapshot must be triggered manually (Section 6.3).

### 3.4 Service accounts

```
eos-scorecard-runtime@fannit-eos-scorecard.iam.gserviceaccount.com
```
Cloud Run runtime identity. Has: `secretmanager.secretAccessor` on each project secret; Editor on the source Google Sheet (granted via the Sheets share dialog); Viewer on all 4 GA4 properties (granted via Admin API).

```
909120368032-compute@developer.gserviceaccount.com
```
Cloud Build default SA. Has project-level `run.admin`, `iam.serviceAccountUser`, `artifactregistry.writer`, `logging.logWriter`.

---

## 4. Credentials & Access

| Credential | Used for | Stored where | Refresh behavior |
|---|---|---|---|
| `highlevel-pit-fannit` | HL PIT, FANNIT sub-account | Secret Manager | Static PIT; rotate manually if revoked |
| `highlevel-pit-hmc` | HL PIT, HMC | Secret Manager | Same |
| `highlevel-pit-tmsa` | HL PIT, TMSA | Secret Manager | Same |
| `highlevel-pit-ipa` | HL PIT, IPA | Secret Manager | Same |
| `teamwork-api-token` | Teamwork v3 API (shared instance) | Secret Manager | Static token; inherits creating user's project visibility |
| GA4 property access | GA4 Data API reads | IAM access binding on each property (no secret) | Permanent; SA binding does not expire |
| Google Sheets access | read goals/weeks, write snapshot | Sheet shared with runtime SA as Editor | Permanent unless un-shared |
| `GCP_PROJECT` env var | Secret Manager project resolution | Cloud Run env var (`cloudbuild.yaml`) | n/a |
| QBO `client_id`/`client_secret`/refresh tokens | **Not yet provisioned** | Planned Secret Manager `qbo-*` | OAuth refresh tokens auto-renew on use once built |

- **Who has access:** Chris owns the GCP project, the GitHub org `FANNIT-Digital-Marketing-Agency` (hosts this repo and `fannit-system-docs`), the Google Sheet, the HL sub-accounts, the Teamwork instance, and the GA4 properties. Request access through him.
- **No OAuth tokens are stored in production.** The only OAuth flow used was a one-time OAuth Playground token to grant the runtime SA on GA4 properties (Section 7, item 6). It was short-lived and is not persisted anywhere.
- To list current secrets:
  ```
  gcloud secrets list --project=fannit-eos-scorecard --format="value(name)"
  ```

---

## 5. Deployment & Updates

### 5.1 Deploy a change

There is **no CI/CD trigger** (GitHub-to-Cloud-Build connection is an open item). Deploys are manual from the local working copy `G:\fannit-eos-scorecard\`:

1. Commit your change:
   ```
   git add -A && git commit -m "your message"
   git push origin main
   ```
2. Build + deploy via Cloud Build:
   ```
   gcloud builds submit --config=cloudbuild.yaml --project=fannit-eos-scorecard --substitutions=COMMIT_SHA=$(git rev-parse --short HEAD)
   ```
3. `cloudbuild.yaml` runs: docker build → push to Artifact Registry → `gcloud run deploy eos-scorecard`. Typical duration 2-3 minutes.

### 5.2 Roll back

```
gcloud run revisions list --service=eos-scorecard --region=us-central1 --project=fannit-eos-scorecard

gcloud run services update-traffic eos-scorecard \
  --to-revisions=<PRIOR_REVISION_NAME>=100 \
  --region=us-central1 --project=fannit-eos-scorecard
```

### 5.3 Test locally before deploying

- Source clients (HighLevel, Teamwork, GA4) can be tested locally: they use Secret Manager + plain HTTP / the GA4 SDK and work under `gcloud` user ADC.
- The **Sheets read path cannot be fully tested locally** unless your local ADC has the Sheets scope. By default `gcloud auth application-default login` ADC lacks it, so `read_agency_kpis()` raises `ACCESS_TOKEN_SCOPE_INSUFFICIENT` locally. This is a known local-only limitation; the deployed runtime SA reads the sheet fine. Validate sheet-touching changes against the deployed service, not locally.
- Quick local source test pattern:
  ```python
  import sys, os
  sys.path.insert(0, r"G:/fannit-eos-scorecard")
  os.environ["GCP_PROJECT"] = "fannit-eos-scorecard"
  from src.sources import highlevel, teamwork, ga4
  # call weekly_metrics / onboarding_count / weekly_sessions with a week label
  ```

### 5.4 Build steps that must run

None beyond `cloudbuild.yaml`. No frontend build step (the UI is vanilla HTML/CSS/JS served statically by FastAPI). `requirements.txt` is installed in the Docker build.

---

## 6. Routine Administration Tasks

### 6.1 Health check

```
curl -s https://eos-scorecard-btpczli7ra-uc.a.run.app/healthz
# expect: {"ok": true}

curl -s "https://eos-scorecard-btpczli7ra-uc.a.run.app/api/agencies"
# expect: {"agencies":["FANNIT","HMC","IPA","TMSA"]}
```

Spot-check live data and provenance:

```
curl -s "https://eos-scorecard-btpczli7ra-uc.a.run.app/api/scorecard?agency=FANNIT" \
  | python -m json.tool
# each kpi has "is_live": true|false  — Traffic/Discovery/New Sales/Onboarding
# should be is_live:true for the current/last-completed week
```

### 6.2 View logs

```
gcloud run services logs read eos-scorecard \
  --region=us-central1 --project=fannit-eos-scorecard --limit=100

# live tail
gcloud beta run services logs tail eos-scorecard \
  --region=us-central1 --project=fannit-eos-scorecard
```

Logger names to grep for: `eos-scorecard.highlevel`, `eos-scorecard.teamwork`, `eos-scorecard.ga4`, `eos-scorecard.aggregate`, `eos-scorecard.snapshot`, `eos-scorecard.scorecard`.

### 6.3 Run the snapshot manually (bypass the missing cron)

```
# default = last completed week
curl -s -X POST -H "Content-Length: 0" \
  https://eos-scorecard-btpczli7ra-uc.a.run.app/internal/snapshot | python -m json.tool

# a specific week
curl -s -X POST -H "Content-Length: 0" \
  "https://eos-scorecard-btpczli7ra-uc.a.run.app/internal/snapshot?date=5/11" | python -m json.tool
```

Response reports `cells_written` and a per-agency list of `Label=value@CELL`. Idempotent: re-running overwrites the same cells.

### 6.4 Single vs batch

There is no single-record mode. `read_agency_kpis()` always reads one agency block. `run_snapshot()` always processes all four agencies for one week. To limit a snapshot to one week, pass `?date=M/D`.

---

## 7. Known Issues & Limitations

| # | Issue | Impact / workaround |
|---|---|---|
| 1 | Multi-line sheet header `Calendar\nMonth\nActual` once leaked into the weekly-column list | Fixed (commit `92dae7b`): `_get_week_columns()` whitespace-normalizes and requires `/` in the header. If a new column header without a slash is added, it is ignored by design. |
| 2 | HL PIT cannot call `/oauth/installedLocations` (401) | Do not use that endpoint to discover locations; location IDs are hard-coded in `src/config.py`. |
| 3 | `/healthz` occasionally serves a Google 404 (StaticFiles mount fallthrough) | Cosmetic; `/api/*` and `/` are reliable. Use `/api/agencies` as the practical liveness probe. |
| 4 | IPA has no QBO; AR / Cash Collected / Cash on Hand render from the sheet | By design until QBO is built. Each source degrades independently. |
| 5 | IPA HL has two pipelines named `💸 SALES PIPELINE` | `src/config.py` pins the 63-opportunity one (`T4bqE5CGEaw8P2vU1H0z`). Do not change without re-verifying. |
| 6 | GA4 UI rejects service-account emails; gcloud refuses Analytics scope (Workspace policy) | Resolved one-time via OAuth Playground (`analytics.manage.users`) + `POST .../v1alpha/properties/{id}/accessBindings`. `accessBindings` is **v1alpha** (v1beta 404s) and needs **`analytics.manage.users`** (not `analytics.edit`). SA access is now permanent; no recurring action. |
| 7 | Live override only applies to the current / last-completed week | Older weeks intentionally show sheet values (what the snapshot stamped then). Teamwork onboarding has no historical query, so live calls for old weeks would be wrong. Controlled by `aggregate.is_live_week()` (±8 days). |
| 8 | `POST /internal/snapshot` is not auth-gated | Anyone who knows the URL can trigger a sheet write. Low blast radius (idempotent, bounded cells) but must be hardened with an OIDC check before wiring the public Cloud Scheduler cron. Open item. |
| 9 | Churn, AR, Cash Collected, Cash on Hand are NOT live | Churn is sheet-sourced by design (vetted trailing-12mo formula). The QBO trio is blocked on Intuit Developer app registration. |
| 10 | TMSA/IPA agency-block row offsets in `AGENCY_BLOCKS` were confirmed once | If the sheet layout shifts (rows inserted above a block), `src/sheets/scorecard.py AGENCY_BLOCKS` must be updated by hand. |

Hard limits to be aware of: HighLevel API rate limits (the opportunities pager caps at 50 pages / 5000 won opps as a safety valve in `highlevel._won_opportunities`); GA4 Data API quotas (well within free tier at this volume); Google Sheets API write quota (the snapshot does one `batchUpdate` per agency, 4/week, negligible).

---

## 8. Troubleshooting Guide

### 8.1 System not responding at all

- **Symptom:** dashboard blank, `curl /api/agencies` times out or 5xx.
- **Likely cause:** bad revision deployed, or Cloud Run service down.
- **Diagnosis:**
  ```
  gcloud run services describe eos-scorecard --region=us-central1 --project=fannit-eos-scorecard --format="value(status.url,status.latestReadyRevisionName)"
  gcloud run services logs read eos-scorecard --region=us-central1 --project=fannit-eos-scorecard --limit=50
  ```
- **Fix:** roll back to the last good revision (Section 5.2). Then diagnose the bad commit locally.

### 8.2 A KPI shows `(sheet)` when it should be `● LIVE`

- **Symptom:** Traffic/Discovery/New Sales/Onboarding card shows `(sheet)` for the current week.
- **Likely cause:** the source pull failed (auth, network, rate limit) and the code fell back to the sheet (intended graceful degradation); or the selected week is older than the live window.
- **Diagnosis:** check logs for `eos-scorecard.highlevel`, `eos-scorecard.ga4`, `eos-scorecard.teamwork` warnings. Confirm the picked week is the current/last-completed one (live window is ±8 days, `aggregate.is_live_week`).
- **Fix:** address the underlying source error (Sections 8.3-8.5). The dashboard stays up either way; this is a soft failure by design.

### 8.3 HighLevel API errors

- **Symptom:** Discovery/New Sales stuck on sheet values; logs show `HL ... fail`.
- **Likely cause:** revoked/expired PIT, wrong location ID, HL rate limit, or HL API version drift.
- **Diagnosis:**
  ```
  PIT=$(gcloud secrets versions access latest --secret=highlevel-pit-fannit --project=fannit-eos-scorecard)
  curl -s -H "Authorization: Bearer $PIT" -H "Version: 2021-04-15" \
    "https://services.leadconnectorhq.com/calendars/?locationId=q2x8tokGpDhlOu8bgVLN" | head -c 300
  ```
  401 = bad/expired PIT. 403 = scope. 429 = rate limit.
- **Fix:** if PIT bad, mint a new Private Integration Token in that HL sub-account (scopes: View Calendars, View Calendar Events, View Opportunities, View Pipelines) and rotate the secret (Section 11.4). If rate-limited, wait; the 10-min cache limits call volume.

### 8.4 GA4 errors

- **Symptom:** Traffic on sheet fallback; logs show `GA4 fail`.
- **Likely cause:** SA access binding removed from a property, or property ID changed, or Data API quota.
- **Diagnosis:** confirm the binding exists. With a fresh Playground `analytics.readonly` token:
  ```
  curl -s -X POST -H "Authorization: Bearer <TOKEN>" -H "Content-Type: application/json" \
    -d '{"dateRanges":[{"startDate":"2026-05-11","endDate":"2026-05-17"}],"metrics":[{"name":"sessions"}]}' \
    "https://analyticsdata.googleapis.com/v1beta/properties/319269316:runReport"
  ```
- **Fix:** if the binding was removed, re-grant it (Section 11.4 / Brief Section 13 item 6): OAuth Playground with `analytics.manage.users`, then `POST https://analyticsadmin.googleapis.com/v1alpha/properties/{id}/accessBindings` with `{"user":"eos-scorecard-runtime@fannit-eos-scorecard.iam.gserviceaccount.com","roles":["predefinedRoles/viewer"]}`.

### 8.5 Teamwork errors

- **Symptom:** Onboarding count wrong or on sheet fallback; logs show `Teamwork ... fail`.
- **Likely cause:** token revoked, or the creating user lost visibility into an agency's project category, or category/tag IDs changed.
- **Diagnosis:**
  ```
  TOK=$(gcloud secrets versions access latest --secret=teamwork-api-token --project=fannit-eos-scorecard)
  AUTH=$(printf '%s:x' "$TOK" | base64)
  curl -s -H "Authorization: Basic $AUTH" \
    "https://fannit.teamwork.com/projects/api/v3/projectcategories.json?pageSize=100"
  ```
  Confirm category IDs still match `TEAMWORK_AGENCY_CATEGORY_ID` and the `Onboarding` tag is still `117305`.
- **Fix:** rotate the token if revoked (Section 11.4). If IDs changed, update `src/config.py` and redeploy.

### 8.6 Sheet read/write failures

- **Symptom:** `/api/scorecard` returns `{"error":"sheet_read_failed"}` or snapshot reports `write error`.
- **Likely cause:** runtime SA no longer has Editor on the sheet, sheet ID changed, or a tab was renamed.
- **Diagnosis:** check logs for the Sheets HttpError detail. Confirm the sheet (`1QyyYNoNR05V8hxjGSBYfvPqWANx37kJiGw3ePx-hz8c`) is still shared with `eos-scorecard-runtime@fannit-eos-scorecard.iam.gserviceaccount.com` as Editor, and tabs `2026 Scorecard` / `Upsells & Churn` exist with those exact names.
- **Fix:** re-share the sheet with the runtime SA (Editor, do not notify). If a tab was renamed, update `SCORECARD_TAB_NAME` in `src/config.py`.

### 8.7 Empty or wrong values on cards

- **Symptom:** all cards `—`, or all weeks show identical numbers.
- **Likely cause:** identical-across-weeks means the sheet's weekly cells were never populated for that week and the snapshot has not run; `—` means no value for the selected week and no live override.
- **Diagnosis:** pick the last-completed week in the picker; confirm `is_live:true` for the four live KPIs via the API. Run the snapshot manually (Section 6.3) and re-check.
- **Fix:** if live KPIs are correct but sheet KPIs are flat, that is expected for Churn/QBO until those are wired. If everything is flat, the snapshot has not been writing; run it manually and investigate source errors.

### 8.8 Deployment failure

- **Symptom:** `gcloud builds submit` fails.
- **Likely cause:** Docker build error (bad `requirements.txt` pin or syntax error), or Cloud Build SA missing a role.
- **Diagnosis:** read the build log URL printed by the command, or:
  ```
  gcloud builds list --project=fannit-eos-scorecard --limit=3
  ```
- **Fix:** fix the code/requirements and resubmit. If IAM, re-grant the Cloud Build SA roles in Section 3.4.

---

## 9. Data Model

**This system uses Google Sheets as its data layer, not Airtable.**

### 9.1 Workbook

- Name: `All Accounts & KPIs - FANNIT`
- Sheet ID: `1QyyYNoNR05V8hxjGSBYfvPqWANx37kJiGw3ePx-hz8c`

### 9.2 Tabs

| Tab | gid | Read/Write | Purpose |
|---|---|---|---|
| `2026 Scorecard` | (main) | READ goals + weekly cells; WRITE snapshot values | Goals + weekly actuals for all 4 agencies |
| `Upsells & Churn` | `130117843` | (not currently read by code) | Raw churn event log; churn % is a sheet formula elsewhere |

### 9.3 Agency block offsets in `2026 Scorecard` (`src/sheets/scorecard.py AGENCY_BLOCKS`)

| Agency | Header row | KPI rows |
|---|---|---|
| FANNIT | 36 | 38-45 |
| HMC | 73 | 75-82 |
| TMSA | 94 | 96-103 |
| IPA | 115 | 117-124 |

Within a block: column `E` = KPI label, `F` = annual goal (read-only to the job), `G` = YTD actual (formula), `H` = Hit % (formula), then per-month bands of `Goal | week cells | Calendar Month Actual`. The snapshot writes ONLY weekly cells. KPI row order is fixed (8 rows): Website/LP Traffic, Discovery Calls, New Sales (15% of Discovery), Clients in Onboarding, Churn Over Last 12 Months, Total $ AR Past 30 Days, Cash Collected, Cash on Hand.

### 9.4 Cross-system reference IDs (`src/config.py`)

HighLevel (location / pipeline / won-stage):
```
FANNIT  loc q2x8tokGpDhlOu8bgVLN  pipe OeYgK000wVzh0pxH3edY  won ff032485-ffef-4a8f-91b7-1f5db6596678
HMC     loc LAkDi1yul6kxjPcqkLxb  pipe UwK3pSQmvBJGzeNPfmoJ  won 45fa598d-26a8-4b82-81aa-55242518a2cd
TMSA    loc Fj5y9oD9dIJWGHRCeNCW  pipe 5wfjI8JTTuesnVlFSWg5  won a2a57886-4de4-4701-9c21-8f6843facc28
IPA     loc TTv1Bn2QeGBIKwcP72zA  pipe T4bqE5CGEaw8P2vU1H0z  won a8b13c1f-149c-49ff-a48e-876082b8a646
```
GA4 property IDs: FANNIT `319269316`, HMC `464894002`, TMSA `473651067`, IPA `496676704`.
Teamwork category IDs: FANNIT `35408`, HMC `35409`, TMSA `35410`, IPA `35411`. Onboarding tag: `117305`.

---

## 10. External Service Notes

| Service | Role here | Limits / cost | Docs |
|---|---|---|---|
| Google Sheets API v4 | Read goals/weeks, write snapshot cells | Generous read quota; 4 batched writes/week | developers.google.com/sheets/api |
| GA4 Data API | Weekly `sessions` per property | Free-tier quota ample at this volume | developers.google.com/analytics/devguides/reporting/data/v1 |
| GA4 Admin API (v1alpha) | One-time SA access binding only | Not called at runtime | developers.google.com/analytics/devguides/config/admin/v1 |
| HighLevel V2 API | Calendars/events (calls), opportunities (won) | Per-token rate limits; mitigated by 10-min cache + pager safety valve | highlevel.stoplight.io |
| Teamwork v3 API | Active onboarding project count | Token inherits creating user's project visibility | apidocs.teamwork.com |
| Google Secret Manager | All HL + Teamwork tokens | Negligible cost; cached in-process | cloud.google.com/secret-manager |

API version headers that matter: HL calendars `Version: 2021-04-15`, HL opportunities `Version: 2021-07-28`. GA4 Admin accessBindings is `v1alpha` only.

---

## 11. Common Maintenance Scenarios

### 11.1 Add a new agency

1. Add its block to the `2026 Scorecard` tab (same 8-row shape).
2. Add row offsets to `AGENCY_BLOCKS` in `src/sheets/scorecard.py`.
3. Add the agency to `AGENCIES` and the HL/GA4/Teamwork maps in `src/config.py`.
4. Mint and store its HL PIT: Section 11.4 pattern with secret name `highlevel-pit-<agency>`.
5. Grant the runtime SA Viewer on its GA4 property (Section 11.4, GA4 sub-section).
6. Commit + deploy (Section 5.1).

### 11.2 Change a goal

Edit cell `F` of the relevant KPI row in the agency's block in the `2026 Scorecard` tab. No deploy needed; picked up on next request.

### 11.3 Change a calendar inclusion rule or metric definition

Discovery/strategy classification lives in `src/sources/highlevel.py` (`DISCOVERY_TOKENS`, `STRATEGY_TOKENS`, `EXCLUDE_TOKENS`, `SHOWED`). The live week window is `src/sources/aggregate.py _week_window` / `is_live_week`. Edit, test the source locally (Section 5.3), commit, deploy.

### 11.4 Rotate a credential

HL or Teamwork token:
```
printf '%s' 'NEW_TOKEN_VALUE' | gcloud secrets versions add SECRET_NAME \
  --project=fannit-eos-scorecard --data-file=-
```
The runtime SA reads `latest`; the next request (after the 10-min cache expires, or a cold start) picks it up. No redeploy required.

GA4 SA access (if a binding was removed):
1. OAuth Playground (`https://developers.google.com/oauthplayground`) → scope `https://www.googleapis.com/auth/analytics.manage.users` → authorize as the GA4-admin Google account → exchange for `access_token`.
2. For each property ID:
   ```
   curl -s -X POST -H "Authorization: Bearer <ya29 TOKEN>" -H "Content-Type: application/json" \
     -d '{"user":"eos-scorecard-runtime@fannit-eos-scorecard.iam.gserviceaccount.com","roles":["predefinedRoles/viewer"]}' \
     "https://analyticsadmin.googleapis.com/v1alpha/properties/<PID>/accessBindings"
   ```

### 11.5 Wire QBO (the remaining integration)

1. Chris registers an Intuit Developer app (QuickBooks Online and Payments, scope `com.intuit.quickbooks.accounting`), production keys, redirect URI `https://developer.intuit.com/v2/OAuth2Playground/RedirectUrl`.
2. Store `qbo-client-id`, `qbo-client-secret` in Secret Manager; mint per-realm refresh tokens (FANNIT/TMSA/HMC; IPA has no QBO) via the Intuit OAuth Playground; store as `qbo-refresh-token-<agency>`.
3. Build `src/sources/qbo.py` (AR aging, P&L cash collected, balance sheet cash on hand), add to `aggregate.py` `LIVE_KPI_LABELS` + `live_metrics`, add to `snapshot.py _LIVE_LABEL_ROW_OFFSET`.
4. Deploy.

### 11.6 Set up the weekly cron (when snapshot endpoint is hardened)

After adding OIDC auth to `/internal/snapshot`, create a Cloud Scheduler job (Mon 06:00 PT) POSTing to the endpoint with an OIDC token for an invoker SA. Update Brief + this SOP when done.

---

## 12. Contacts & Escalation

- **Build owner / primary escalation:** Chris Fink [NEEDS CONFIRMATION: preferred contact channel]
- **Operator backups:** [NEEDS CONFIRMATION: who besides Chris can run/redeploy]
- **GitHub:** repo `FANNIT-Digital-Marketing-Agency/fannit-eos-scorecard` (org admin: Chris)
- **Vendor support:**
  - Google Cloud (Cloud Run, Secret Manager, Sheets/Analytics APIs): GCP Console support for project `fannit-eos-scorecard`
  - HighLevel: agency HL account support
  - Teamwork: Teamwork support for `fannit.teamwork.com`
  - Intuit Developer (QBO, once built): developer.intuit.com support
- **Source-of-truth docs:** `BRIEF.md` (repo root) and the mirror at `FANNIT-Digital-Marketing-Agency/fannit-system-docs` → `briefs/EOS_SCORECARD_MASTER_BRIEF.md`

---

*End of SOP. Keep this file in sync with `BRIEF.md` whenever architecture, credentials, or procedures change.*
