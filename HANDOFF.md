# FANNIT EOS L10 Scorecard — Session Handoff

**Generated:** 2026-05-18
**For:** a fresh Claude session in a new account, picking up cold.
**Goal of this document:** zero ramp-up. Read the listed docs in order, do the re-auth checklist, then act.

This file captures **only** what isn't already in the repo's permanent docs: session-specific decisions, drift from spec, and open threads. Do not duplicate the brief or SOP here; they are the source of truth.

---

> **ACTIVE WORK (2026-08-16): source-first refinement.** Before doing anything with data sourcing, read `SCORECARD_REFINEMENT_BRIEF.md` in the repo root. It is the current, agreed spec (each KPI sourced live from its system, sheet used only for goals + the `Stats!B19` churn cell) and it supersedes the sheet-first sourcing described below. All sourcing decisions are already made there; do not re-litigate them.

## 1. Read these first, in this order

| Order | File (absolute path) | Why |
|---|---|---|
| 1 | `G:\fannit-eos-scorecard\BRIEF.md` | Master spec. Architecture, all per-agency IDs, sheet layout, change log, known issues. **Start here.** |
| 2 | `G:\fannit-eos-scorecard\SOP.md` | Operational runbook: deploy, rollback, troubleshooting per source, credential rotation, maintenance scenarios. |
| 3 | `G:\fannit-eos-scorecard\src\config.py` | Single source of truth for non-secret per-agency IDs (HL locations / pipelines / won-stages, GA4 property IDs, Teamwork category IDs). |
| 4 | `G:\fannit-eos-scorecard\src\sheets\scorecard.py` | The sheet reader. `AGENCY_BLOCKS` row offsets, weekly-column discovery, the live-override block at the bottom of `read_agency_kpis()`. |
| 5 | `G:\fannit-eos-scorecard\src\sources\aggregate.py` | TTL cache + live-week gating. Read this before changing any source behavior. |
| 6 | `G:\fannit-eos-scorecard\main.py` | FastAPI endpoints. Small file; read it once. |
| 7 | `G:\fannit-eos-scorecard\Dockerfile`, `cloudbuild.yaml`, `requirements.txt` | Deployment surface. |

Org-level mirror of the brief (kept in sync): `FANNIT-Digital-Marketing-Agency/fannit-system-docs` → `briefs/EOS_SCORECARD_MASTER_BRIEF.md`. Use the repo copy as canonical.

---

## 2. System snapshot (as of 2026-05-18)

- **Repo:** `https://github.com/FANNIT-Digital-Marketing-Agency/fannit-eos-scorecard`
- **Local working copy:** `G:\fannit-eos-scorecard`
- **Latest commit on `main`:** `6a4c75b` (Add SOP). HEAD is fully pushed.
- **Cloud Run revision live:** `eos-scorecard-00008-vqc` (commit `d965b3d`, "ga4live"). The two later commits (`9c72721`, `6a4c75b`) are docs only — no redeploy needed for them.
- **Dashboard URL:** `https://eos-scorecard-btpczli7ra-uc.a.run.app`
- **GCP project:** `fannit-eos-scorecard` (us-central1, under `fannit.com` org)
- **Runtime SA:** `eos-scorecard-runtime@fannit-eos-scorecard.iam.gserviceaccount.com`

### 2.1 Drift from spec — what BRIEF describes vs what's true

| Topic | Brief says | Reality | Action |
|---|---|---|---|
| KPI sourcing | "4 of 8 KPIs sourced live" | True for **one widget on one card under one week filter** (the current-week big number). Goals, YTD, hit %, trend strip, week picker still come from the sheet for all 8 KPIs. | See §5 open threads — Chris flagged this and the rewrite decision is unresolved. |
| Auth-gating on snapshot | "Tracked as an open item" | Still ungated. `POST /internal/snapshot` is public. | Harden before wiring the Cloud Scheduler cron. |
| Cloud Scheduler | "Planned" | Not created. `gcloud scheduler jobs list --location=us-central1 --project=fannit-eos-scorecard` returns 0. | Snapshots are manual today. |
| GitHub auto-deploy | "Pending UI auth" | No Cloud Build trigger connected to GitHub. | Manual `gcloud builds submit` per deploy. |
| Cloudflare Access | "Planned" | Dashboard is fully public. | OK for now; revisit if data sensitivity warrants. |

---

## 3. Data layer + integration map (no secret values — pointers only)

### 3.1 Google Sheet (data layer)

- Workbook name: `All Accounts & KPIs - FANNIT`
- Sheet ID: `1QyyYNoNR05V8hxjGSBYfvPqWANx37kJiGw3ePx-hz8c`
- Tabs used by code: `2026 Scorecard` (read + write), `Upsells & Churn` gid `130117843` (not currently read; reserved)
- Access: workbook shared with the runtime SA as **Editor** (granted out-of-band, not in code). If revoked, re-share via the Sheets share dialog.
- Block row offsets per agency are in `src/sheets/scorecard.py AGENCY_BLOCKS`. FANNIT 36/38-45, HMC 73/75-82, TMSA 94/96-103, IPA 115/117-124.

### 3.2 GA4 (live)

- Property IDs in `src/config.py GA4_PROPERTY_IDS`: FANNIT `319269316`, HMC `464894002`, TMSA `473651067`, IPA `496676704`.
- Auth: runtime SA has Viewer on all 4 properties via Admin API access bindings (granted 2026-05-18). **No token stored anywhere.** Re-grant procedure if a binding is removed: BRIEF §13 item 6 and SOP §11.4. **Critical gotcha:** the gcloud CLI cannot mint Analytics scope tokens (Workspace policy blocks it). Use the OAuth Playground (Google-verified, not blocked) with scope `https://www.googleapis.com/auth/analytics.manage.users`, then POST to `analyticsadmin.googleapis.com/v1alpha/properties/{id}/accessBindings`. `v1alpha` (not `v1beta`) and `analytics.manage.users` (not `analytics.edit`) — both are non-obvious.

### 3.3 HighLevel (live)

- PITs stored in Secret Manager: `highlevel-pit-fannit`, `highlevel-pit-hmc`, `highlevel-pit-tmsa`, `highlevel-pit-ipa`.
- Location IDs / pipeline IDs / won-stage IDs in `src/config.py HIGHLEVEL`.
- Calendar inclusion rule and event/opportunity logic in `src/sources/highlevel.py`. The original pipeline-link rule was relaxed because HL calendar objects don't expose `pipelineId`; current rule is name-based (`discovery` / `strategy` / `planning` substrings, excluding `internal`, `isActive=true`, `appointmentStatus="showed"`).
- IPA has two pipelines named `💸 SALES PIPELINE`; the canonical one is pinned by ID in config (`T4bqE5CGEaw8P2vU1H0z`).

### 3.4 Teamwork (live)

- Token stored in Secret Manager: `teamwork-api-token`. Single shared token; inherits the creating user's project visibility.
- Instance: `fannit.teamwork.com`.
- Category IDs in `src/config.py TEAMWORK_AGENCY_CATEGORY_ID`. Onboarding tag id `117305`.
- **Limitation:** Teamwork API exposes only current state, not historical. The live-override is therefore gated to the current/last-completed week (±8 days). Older weeks read whatever the snapshot wrote to the sheet at the time.

### 3.5 QuickBooks Online (not yet built)

- **No code, no secrets, no Intuit Developer app exists yet.** Blocked entirely on Chris registering the dev app and sending `client_id` / `client_secret`. The wiring playbook is in BRIEF §11 and SOP §11.5.
- Note: a per-Claude-session QBO MCP connector was added during the previous session for ad-hoc reads. It is **not** a substitute for the production OAuth integration (it's session-scoped to Claude, not callable from Cloud Run). Treat it as a side tool.

### 3.6 Secret Manager inventory

Run to confirm: `gcloud secrets list --project=fannit-eos-scorecard --format="value(name)"`. Expected today:
```
highlevel-pit-fannit
highlevel-pit-hmc
highlevel-pit-ipa
highlevel-pit-tmsa
teamwork-api-token
```
All grant `secretAccessor` to the runtime SA. To add a new secret, see SOP §11.4.

---

## 4. Re-auth checklist for the new Claude account

Run these in order. Each is "one-time per new account / per workstation."

### 4.1 gcloud (required)

The new account must auth as a user with at least Editor on the GCP project `fannit-eos-scorecard`. Default Chris (`chris@fannit.com`) is the owner.

```
gcloud auth login
gcloud config set project fannit-eos-scorecard
gcloud auth application-default login
```

**Do NOT try to include the Analytics scope in the ADC login** — gcloud's fixed scope allowlist blocks it and Workspace policy blocks the gcloud OAuth client app. The runtime SA already has GA4 access permanently; you only need to redo this if you ever need to re-grant the SA (see §3.2 above).

### 4.2 GitHub (required for push)

The new account must have push access on `FANNIT-Digital-Marketing-Agency/fannit-eos-scorecard`. Confirm by visiting the repo on github.com; if "Settings" is visible, you're in. Otherwise ask Chris to add the account.

```
git config --global user.name "<name>"
git config --global user.email "<email matching the GitHub account>"
git -C "G:\fannit-eos-scorecard" remote -v
# expect: origin https://github.com/FANNIT-Digital-Marketing-Agency/fannit-eos-scorecard.git
```

### 4.3 MCP connectors (Claude-account-scoped — re-add as needed)

These connectors were used in prior sessions and are tied to the Claude account, not the project. Add them in the new account if you'll do the same kind of work:

- **Google Drive MCP** (server id `1986a8a1-4768-488f-bd91-f427c08887b2` in prior sessions): used to read/write Drive files when Chris asks for Drive uploads. Authenticate as the Google account that owns the target Drive resources.
- **QuickBooks MCP** (`2bb90a06-...`): ad-hoc QBO reads from within a Claude conversation. Useful for spot-checking; **not** a production integration.
- Optional: Airtable (`fea01914-...`), Ahrefs/SEO suite (`5e21eb7f-...`), web search (`brave-search`), Firebase plugin, PageSpeed Insights. Not required for the core scorecard work.

None of these affect the deployed Cloud Run service; they're Claude-side tools only.

### 4.4 Local `.env` for development

Production does **not** use a `.env` file (Cloud Run sets only `GCP_PROJECT` via `cloudbuild.yaml`; everything else comes from Secret Manager at runtime). For local dev:

```
copy G:\fannit-eos-scorecard\.env.example G:\fannit-eos-scorecard\.env
```

Then set `GCP_PROJECT=fannit-eos-scorecard`. **Leave the credential fields blank.** Code reads from Secret Manager using your ADC. Filling them in would only matter if you were running outside of any Google environment, which isn't the case here.

`.env` is gitignored; the template `.env.example` is committed.

### 4.5 Optional: Python deps for local source testing

```
cd G:\fannit-eos-scorecard
pip install -r requirements.txt
```

Source clients (HL, Teamwork, GA4) work locally under user ADC. The **Sheets read path does not** — local ADC lacks the Sheets scope and `read_agency_kpis()` returns 403. Test sheet-touching changes by deploying to a temporary Cloud Run revision instead.

---

## 5. Current state + open threads

### 5.1 What's deployed and behaving

- 4 KPI cards show "● LIVE" on the current/last-completed week: Traffic, Discovery, New Sales, Onboarding (all 4 agencies).
- Snapshot job written and proven (`POST /internal/snapshot` last verified writing 16 cells for week 5/11).
- Week picker, last-completed-week default, sheet layout handling all stable.
- HL pager safety valve, Teamwork tag-filter fallback, GA4 graceful-degradation all in place.

### 5.2 Open architectural decision — DO NOT IGNORE

In the final substantive exchange of the prior session, Chris flagged that **the sheet is still a data source for more than just churn**, contradicting his earlier directive ("if the All Accounts sheet is still a data source for anything except churn rate, we have a problem"). The honest per-widget audit:

- The big number is live only for the **current/last-completed-week view** of the 4 live KPIs.
- Annual goals, YTD, YTD Hit %, the 8-week trend strip, and the week-picker dropdown all read the sheet for all 8 KPIs.
- For older weeks the big number also falls back to the sheet.

The proposed fix (offered in the prior session, awaiting Chris's call) is a rewrite that:
- Computes YTD by summing source pulls Jan 1 → today (GA4, HL). Teamwork onboarding has no history, so its trend strip stays sheet-derived; that's an upstream data limit, not engineering.
- Computes the trend strip per week from source pulls, cached.
- Generates the week-picker from a static "all Mondays of the year" list, not the sheet.
- Moves annual goals **off the sheet**.

**The blocker:** Chris was asked to pick where annual goals live going forward and has not answered. Options floated:
1. `src/config.py` constants (edit + redeploy; git audit trail)
2. Airtable
3. A dedicated tiny goals sheet that the code owns end-to-end
4. Firestore doc per agency

A fresh session should **re-surface this question to Chris before doing any source-first rewrite**. Do not unilaterally pick.

### 5.3 Other open threads

- **QBO not built.** Blocked on Chris registering the Intuit Developer app and sending `client_id` / `client_secret`. Wiring playbook in BRIEF §11 and SOP §11.5. The deferred work is for AR / Cash Collected / Cash on Hand (3 KPIs).
- **Snapshot endpoint unauth'd.** Must add an OIDC check before wiring the Cloud Scheduler cron. Low blast radius today (idempotent, bounded cells), but real risk if discovered.
- **Cloud Scheduler cron not created.** Snapshots are manual via curl until both the auth gate and a scheduler job are in place.
- **GitHub → Cloud Build auto-trigger not connected.** Deploys are manual. Connection is a one-time UI auth Chris hasn't done.
- **Cloudflare Access not in front.** Dashboard is public. Acceptable for current usage; revisit before sharing wider.
- **GA4 Strategy/Planning calls computed but not displayed.** A 9th KPI card or merging with Discovery is a UI decision Chris hasn't made.
- **Period tabs (Q1/Q2/Q3/Q4/Last Month/YTD)** are disabled in the UI. Implementation requires proper period aggregation (sum-vs-latest-vs-time-weighted-goal per metric type). Deferred until the sources-first rewrite decision is made (since YTD aggregation depends on whether YTD comes from the sheet or from source pulls).

---

## 6. Immediate next steps (suggested order)

1. **Verify the system is alive end-to-end** before touching anything:
   ```
   curl -s https://eos-scorecard-btpczli7ra-uc.a.run.app/api/agencies
   curl -s "https://eos-scorecard-btpczli7ra-uc.a.run.app/api/scorecard?agency=FANNIT" | python -m json.tool
   ```
   Confirm `is_live:true` on Traffic, Discovery, New Sales, Onboarding for the current/last-completed week.

2. **Surface the open architectural question to Chris** (§5.2). Get an answer on where annual goals live. Without that, source-first refactors are blocked.

3. **If/when Chris sends the QBO Intuit app keys**, follow SOP §11.5: store in Secret Manager, mint per-realm refresh tokens via the Intuit OAuth Playground, build `src/sources/qbo.py`, register in `aggregate.py` and `snapshot.py`. Same shape as the existing source modules.

4. **Harden `/internal/snapshot`** with an OIDC check before creating the Cloud Scheduler weekly cron (Mon 06:00 PT America/Los_Angeles).

5. **Connect GitHub → Cloud Build** (one-time UI auth at `https://console.cloud.google.com/cloud-build/triggers/connect?project=fannit-eos-scorecard`) so future commits auto-deploy. Note the 2nd-gen-trigger `serviceAccount` requirement (must use REST API, not `gcloud builds triggers create`; see Chris's standing memory note).

6. **Keep BRIEF.md + SOP.md current** — every architecture change or new known issue updates the brief and gets mirrored to `FANNIT-Digital-Marketing-Agency/fannit-system-docs/briefs/EOS_SCORECARD_MASTER_BRIEF.md` (clone, copy, commit `Sync EOS Scorecard brief - <date>`, push).

---

## Style and behavior preferences (Chris-specific)

- **No em dashes** in any written content. Use commas, periods, semicolons, parens. Hyphens and en dashes OK. This is a hard rule from his memory file.
- **Be direct and concise.** Chris does not want hedged answers, long preambles, or apology theater. Short, honest, action-oriented.
- **Flag your own gaps.** When something is inferred, say so. When something needs his decision, ask cleanly with options.
- **Default to terse.** Match his energy; he gives short answers and expects short answers.

---

*No secrets are stored in this file. Every credential reference is a name or a pointer (Secret Manager secret name, IAM binding location, IAM email); no raw tokens, keys, or refresh values appear anywhere here.*
