# FANNIT EOS Scorecard — Source-First Refinement Brief

**For:** a fresh Claude agent picking this up cold. Assume no prior context.
**Written:** 2026-08-16.
**Goal:** rebuild how the dashboard sources its data so each KPI comes live from its authoritative system, not the Google Sheet. The sheet is reduced to two roles only: annual goals, and one churn cell.

---

## 0. Read these first (repo background)

The repo already has three docs. Read them for architecture, deploy, and history. This brief supersedes their **sourcing model** (they describe the old sheet-first behavior).

- `G:\fannit-eos-scorecard\BRIEF.md` — architecture, IDs, sheet layout, change log.
- `G:\fannit-eos-scorecard\SOP.md` — deploy, rollback, troubleshooting, credential rotation.
- `G:\fannit-eos-scorecard\HANDOFF.md` — prior session state and re-auth checklist.

Key source files: `main.py` (FastAPI endpoints), `src/config.py` (all non-secret IDs), `src/sheets/scorecard.py` (the sheet reader and the current live-override logic), `src/sources/aggregate.py` (live-metrics aggregator + cache), `src/sources/{ga4,highlevel,teamwork}.py`, `src/snapshot.py`.

---

## 1. What you are building, in one paragraph

Today the dashboard reads almost everything from the `2026 Scorecard` Google Sheet tab and only overrides four KPIs live for the current week. That is wrong. Each KPI must be sourced from its real system for any selected week, including the trend history and the year-to-date figures. The sheet must be read ONLY for annual goals (per KPI) and for one company-wide churn value. Everything else comes from GA4, HighLevel, Teamwork, and QuickBooks Online.

---

## 2. System context

- **Repo:** `https://github.com/FANNIT-Digital-Marketing-Agency/fannit-eos-scorecard` (local clone `G:\fannit-eos-scorecard`).
- **GCP project:** `fannit-eos-scorecard` (us-central1).
- **Service:** Cloud Run `eos-scorecard`. Public URL `https://eos-scorecard-btpczli7ra-uc.a.run.app`.
- **Runtime SA:** `eos-scorecard-runtime@fannit-eos-scorecard.iam.gserviceaccount.com`. It reads all secrets from Secret Manager in THIS project and has Viewer on the four GA4 properties and Editor on the sheet.
- **Stack:** Python 3.11 + FastAPI + gunicorn on Cloud Run. Vanilla HTML/CSS/JS frontend in `static/`. No framework.
- **Deploy:** manual, `gcloud builds submit --config=cloudbuild.yaml --project=fannit-eos-scorecard --substitutions=COMMIT_SHA=$(git rev-parse --short HEAD)`. No GitHub auto-trigger.
- **Agencies:** FANNIT, HMC, TMSA, IPA.

---

## 3. The sourcing model (the core spec)

| KPI | Authoritative source | Exact definition | Sheet cell read? |
|---|---|---|---|
| Website / LP Traffic | GA4 sessions; **Agency Analytics** on GA4 error | GA4 `sessions` for the week. On any GA4 error, pull Sessions from Agency Analytics for the same week. A legitimate zero is NOT an error. | No |
| Discovery Calls | HighLevel | Count of appointments on the "Discovery Call With `<agency>`" calendar with status **Shown**, counted by appointment start date in the week. Mirrors the HL "Sales Performance Dashboard" widget "Discovery Shown." | No |
| New Sales | HighLevel | Count of opportunities **marked Won** with won-date inside the week window, using each agency's pipeline and won stage. | No |
| Clients in Onboarding | Teamwork | Count of **active** projects in the agency's project category tagged **Onboarding**. Current value only; no history API (see cross-cutting rules). | No |
| Churn (trailing 12mo) | Google Sheet | Single company-wide value from the **Stats tab, cell `B19`** (gid 12480116). Shown identically on every agency and the rollup. | Yes, this one cell |
| Total $ AR Past 30 Days | QuickBooks Online (FANNIT only) | AgedReceivables report: sum of the **31-60 + 61-90 + 90+** buckets (money more than 30 days overdue), as of week-end. | No |
| Cash Collected | QuickBooks Online (FANNIT only) | Profit and Loss report, **cash basis**, total **Income** line, for the week. | No |
| Cash on Hand | QuickBooks Online (FANNIT only) | Today's cash balance (the QBO home "Today's Cash Balance") for the current week; Balance Sheet cash accounts as of week-end for past weeks. | No |
| Annual goals (all KPIs) | Google Sheet | Column F of each KPI row in the agency block. Manually maintained. | Yes |

---

## 4. Cross-cutting rules (apply to all KPIs)

1. **Week window:** Monday to Sunday, America/Los_Angeles. Weeks are labeled by their starting Monday (`M/D`). Default view is the **previous completed week**. A week picker lets the user select any other week.
2. **History and trend:** compute every week from source. GA4, HighLevel, and QBO can all be queried for any past week, so recompute per week (cache short). **Teamwork onboarding is the exception:** it has no historical API, so it shows the current count for the current week and stays blank for past weeks. Do not fake onboarding history.
3. **YTD and Hit %:** compute from source, not from the sheet.
   - Incremental KPIs (Traffic, Discovery, New Sales, Cash Collected): YTD = sum from Jan 1 to the selected week-end (one date-range query where the API allows). Prefer a single range query over 52 weekly calls.
   - Snapshot KPIs (Onboarding, AR, Cash on Hand): YTD "actual" = the latest value.
   - Hit % = YTD divided by the annual goal (goal from the sheet).
   - Churn: `Stats!B19` compared to its goal.
4. **On source failure:** retry with backoff, then show the card as **"unavailable."** No sheet fallback. No stale cache. A card that cannot be sourced says so plainly.
5. **Sheet write-back stays:** the snapshot job keeps writing pulled weekly values into the `2026 Scorecard` weekly cells for the legacy manual view, even though the dashboard no longer reads them.
6. **QBO scope:** FANNIT only for now. HMC has no API access (different QBO account); IPA has no QBO. Those three agencies show the QBO trio as "unavailable."
7. **Never reference the sheet cell** for any KPI except churn (`Stats!B19`) and goals (col F). This is the whole point of the refactor.

---

## 5. Per-source implementation detail

### GA4 (+ Agency Analytics fallback)
- Metric `sessions`, one property per agency. SA already has Viewer via Admin API bindings.
- On GA4 error, call Agency Analytics for the same week and pull the **Sessions** metric.
- Agency Analytics is a white-label instance at `reporting.fannit.com`. Per-agency client IDs: FANNIT `511746`, HMC `1489824`, TMSA `1554167`, IPA `1631621`.
- The Agency Analytics API key must live in Secret Manager (see section 7). A Claude-side AA connector does NOT count; Cloud Run cannot use it.

### HighLevel
- **Discovery:** appointments on the calendar named "Discovery Call With `<agency>`" (FANNIT's is "Discovery Call With Fannit"), status **Shown**, dated by appointment start. Discover each subaccount's Discovery calendar ID by listing that location's calendars via the API and matching the name; store or resolve the ID per agency. HighLevel's public API does NOT expose dashboard widgets, so you reproduce the "Discovery Shown" widget from the appointments endpoint, you do not read the widget.
- **New Sales:** opportunities with status Won whose won-date falls in the week, per agency pipeline and won stage.
- API version headers matter: calendars `Version: 2021-04-15`, opportunities `Version: 2021-07-28`.

### Teamwork
- Instance `fannit.teamwork.com`. Onboarding count = active projects in the agency's category, tagged Onboarding (tag id `117305`).
- No historical query. Current week only.

### QuickBooks Online (FANNIT only) — SUPERSEDED 2026-09-16: pull from FANNIT Command, do NOT talk to Intuit

Do not register an Intuit app and do not mint QBO tokens for this service. FANNIT Command already owns a production QBO connection (Intuit app "FANNIT Command", Fannit LLC realm), and Intuit invalidates the prior refresh token when the same app+realm is authorized again, so a second grant would break Command. Command therefore exposes the three scorecard figures over the SAME shared secret this service already uses for its own API.

Endpoint (live on Command as of 2026-09-16, commit 4db8faa):

    GET https://fannit-command-vqqa7p2eiq-uc.a.run.app/executive/api/financials?start=YYYY-MM-DD&end=YYYY-MM-DD
    Header: X-EOS-Secret: <value of env EOS_SCORECARD_SECRET>

Response:

    {"start": "...", "end": "...", "cash_collected": 42991.25,
     "ar_total": 80848.74, "ar_past_30": 8476.43, "cash_on_hand": 280235.74}

Semantics: cash_collected = cash-basis P&L total Income for [start, end]; ar_past_30 = AgedReceivables 31-60 + 61-90 + 91+ buckets as of `end`; cash_on_hand = Balance Sheet "Total Bank Accounts" as of `end`. Errors: 401 bad secret, 400 bad dates (end must not be in the future — clamp the current week's end to today), 502 QBO failure (retry with backoff then show "unavailable"), 503 not configured. Command caches identical (start, end) windows for 15 minutes; still keep the scorecard-side cache to avoid 8 calls per page render.

How to use per KPI:
- Cash Collected (week): one call per week window; YTD = one call with start=Jan 1, end=week-end.
- AR Past 30 / Cash on Hand (snapshot KPIs): one call per week (any start, e.g. the week's Monday); use `ar_past_30` / `cash_on_hand` as-of values. YTD "actual" = latest week's value.
- FANNIT agency only; HMC/TMSA/IPA show the QBO trio as "unavailable" as already decided.

The auth secret already exists on this service as env `EOS_SCORECARD_SECRET` (secret `eos-api-shared-secret` in this project), the same value Command holds. Verified round trip 2026-09-16. No new secrets needed for QBO. `qbo-client-id` / `qbo-client-secret` / `qbo-refresh-token-fannit` from section 7 are OBSOLETE — do not create them.

### Churn
- Read `Stats!B19` (gid 12480116) from sheet `1QyyYNoNR05V8hxjGSBYfvPqWANx37kJiGw3ePx-hz8c`. One value, shown on every agency and the rollup.

### Goals
- Column F of each KPI row in the agency block on the `2026 Scorecard` tab.

---

## 6. Concrete IDs and config

**Sheet:** ID `1QyyYNoNR05V8hxjGSBYfvPqWANx37kJiGw3ePx-hz8c`. Tab `2026 Scorecard`. Churn cell `Stats!B19` (gid 12480116). Goals = col F.
Agency blocks (header row / KPI rows): FANNIT 36 / 38-45, HMC 73 / 75-82, TMSA 94 / 96-103, IPA 115 / 117-124.

**HighLevel** (location / pipeline / won-stage):
- FANNIT `q2x8tokGpDhlOu8bgVLN` / `OeYgK000wVzh0pxH3edY` (2: Active Sales) / `ff032485-ffef-4a8f-91b7-1f5db6596678`
- HMC `LAkDi1yul6kxjPcqkLxb` / `UwK3pSQmvBJGzeNPfmoJ` / `45fa598d-26a8-4b82-81aa-55242518a2cd`
- TMSA `Fj5y9oD9dIJWGHRCeNCW` / `5wfjI8JTTuesnVlFSWg5` / `a2a57886-4de4-4701-9c21-8f6843facc28`
- IPA `TTv1Bn2QeGBIKwcP72zA` / `T4bqE5CGEaw8P2vU1H0z` / `a8b13c1f-149c-49ff-a48e-876082b8a646` (IPA has two "SALES PIPELINE" pipelines; this ID is the correct 63-opportunity one)
- Discovery calendar IDs: to be discovered per location by name match ("Discovery Call With `<agency>`").

**GA4 property IDs:** FANNIT `319269316`, HMC `464894002`, TMSA `473651067`, IPA `496676704`. Metric `sessions`.

**Teamwork:** domain `fannit.teamwork.com`. Onboarding tag `117305`. Category IDs: FANNIT `35408`, HMC `35409`, TMSA `35410`, IPA `35411`.

**Agency Analytics:** `reporting.fannit.com`. Client IDs: FANNIT `511746`, HMC `1489824`, TMSA `1554167`, IPA `1631621`. Metric Sessions.

**QBO:** FANNIT realm only. Realm ID: PENDING (owner to provide). Reports as in section 5.

---

## 7. Secrets

Cloud Run reads secrets ONLY from Secret Manager in project `fannit-eos-scorecard` (via the `GCP_PROJECT` env var). A secret in another project or a Claude-side connector will not work.

**Present today:** `highlevel-pit-fannit`, `highlevel-pit-hmc`, `highlevel-pit-tmsa`, `highlevel-pit-ipa`, `teamwork-api-token`. (GA4 uses an IAM binding, not a secret.)

**Missing, must be created before the AA fallback works:**
- `agency-analytics-api-key`

(2026-09-16: the three qbo-* secrets are no longer needed — QBO data now comes from the FANNIT Command endpoint over the existing shared secret; see the QBO section above.)

**Rule: never accept secrets pasted into chat.** The owner adds them himself from his own terminal. Create + grant pattern:
```
SA=eos-scorecard-runtime@fannit-eos-scorecard.iam.gserviceaccount.com; PROJ=fannit-eos-scorecard
for S in qbo-client-id qbo-client-secret qbo-refresh-token-fannit agency-analytics-api-key; do
  gcloud secrets create "$S" --replication-policy=automatic --project=$PROJ 2>/dev/null
  gcloud secrets add-iam-policy-binding "$S" --member="serviceAccount:$SA" --role="roles/secretmanager.secretAccessor" --project=$PROJ
done
```
Then, per secret: `printf '%s' 'VALUE' | gcloud secrets versions add <name> --data-file=- --project=fannit-eos-scorecard`.
Non-secret identifiers (QBO realm ID, AA client IDs) go in `config.py`, not Secret Manager.

---

## 8. Prerequisites before writing code

1. **gcloud re-auth** (a fresh account or workstation cannot prompt in-session): `gcloud auth login && gcloud config set project fannit-eos-scorecard && gcloud auth application-default login`.
2. **Verify secrets** with `gcloud secrets list`. Confirm the four HL PITs and teamwork token, and check whether the QBO and AA secrets exist yet.
3. **Discover Discovery calendar IDs** per agency via the HL calendars API using each PIT, matching "Discovery Call With `<agency>`". Confirm the mapping.
4. **Local testing caveat:** source clients (HL, Teamwork, GA4) work under user ADC locally. The **Sheets read path does not** locally (missing scope); test sheet-touching changes against a deployed revision.

---

## 9. Suggested build order

1. Invert the reader: build each KPI per week from its source. Read the sheet only for goals (col F) and churn (`Stats!B19`).
2. Rework `highlevel.py`: Discovery by specific calendar + status Shown; New Sales by Won date.
3. Add Agency Analytics module and wire it as the GA4 fallback.
4. Add `qbo.py` (FANNIT realm): AgedReceivables, cash-basis P&L Income, cash balance. Persist rotated refresh token.
5. Compute YTD and Hit % from source (range query for incremental, latest for snapshot).
6. Failure handling: retry with backoff, then "unavailable." Remove all sheet fallback for the non-churn KPIs.
7. Frontend: "unavailable" state and a per-card source badge; churn identical across agencies; QBO trio unavailable for HMC/TMSA/IPA.
8. Keep the snapshot write-back to the sheet.
9. Deploy, verify each KPI's provenance via `/api/scorecard`.

---

## 10. Codebase gotchas (do not relearn these the hard way)

- Multi-line sheet headers ("Calendar\nMonth\nActual") once leaked into the weekly-column list; the reader normalizes whitespace and requires a "/" in week headers. Do not undo that.
- HL PITs cannot call `/oauth/installedLocations` (401). Location IDs are hard-coded in config.
- IPA has two pipelines named "SALES PIPELINE"; the correct one is pinned in config.
- GA4 SA access was granted via the OAuth Playground with `analytics.manage.users` and the Admin API `accessBindings` v1alpha endpoint. gcloud cannot mint an analytics-scoped token. Only relevant if a binding is lost.
- On Windows Git Bash, `gcloud projects list` output has CRLF line endings; strip `\r` before using values in a loop or you get "not a valid project" errors.

---

## 11. Decisions already made (do NOT re-ask the owner)

- History computed from source per week; Teamwork onboarding current-only.
- QBO built now, FANNIT only; HMC/IPA unavailable for the QBO trio.
- Churn is a single company-wide value from `Stats!B19`, shown everywhere.
- Discovery mirrors the HL "Discovery Shown" widget: calendar "Discovery Call With `<agency>`", status Shown, by start date.
- New Sales = opportunities marked Won, dated by won date.
- Onboarding = per-agency category, active, Onboarding tag.
- AR = 30+ overdue buckets. Cash Collected = cash basis. Cash on Hand = today's balance (current), balance-sheet as-of for history.
- Traffic fallback = Agency Analytics Sessions, only on GA4 error.
- On failure: retry then "unavailable," no sheet fallback.
- Keep the sheet write-back. Goals stay in the sheet (col F).

---

## 12. Blocked / pending owner input

- **QBO: UNBLOCKED 2026-09-16.** No Intuit app needed. Pull the three financial KPIs from the FANNIT Command endpoint documented in section 5 (same shared secret this service already holds). `src/sources/qbo.py` becomes a thin HTTP client of Command.
- **Agency Analytics API key:** must be stored as `agency-analytics-api-key`. The fallback is inert until then; GA4 primary still works.

## 13. Also fold into this build (state as of 2026-09-16)

- The shared-secret AUTH ENFORCEMENT commit (225ecc6) is on main but was never deployed — the API endpoints are still public. This refactor's deploy ships it; the Command side (token-minting iframe) has been live since 2026-09-15, so nothing breaks. Cloud Scheduler job `eos-scorecard-weekly-snapshot` (Mon 06:00 PT) is already enabled and sends the header.
- In FANNIT Command the scorecard now lives under the Executive nav dropdown, Owner only (was top-level, CSM tier). The scorecard page itself is unchanged; only who reaches the Command iframe page changed.
- BRIEF.md / HANDOFF.md / SOP.md in this repo still describe the old sheet-first sourcing and say the API is ungated. Update them at the end of this build.

---

## 13. Definition of done

- Each of Traffic, Discovery, New Sales, Onboarding pulls live per selected week, with a visible source badge, and no sheet cell involved.
- Churn shows the `Stats!B19` value on every agency and the rollup.
- Goals still come from the sheet; YTD and Hit % are computed from source.
- QBO trio is live for FANNIT (once creds land) and "unavailable" for HMC/TMSA/IPA.
- A source outage shows "unavailable," never a stale sheet number.
- The snapshot job still writes weekly values into the `2026 Scorecard` tab.
- `/api/scorecard?agency=FANNIT` shows the correct provenance per KPI.
