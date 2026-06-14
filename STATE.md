# Mata Web Tools — Session State

This file tracks the current status of the Sonnet work queue (`sonnet/`).
Update it after every session: set status, record commit SHA and Cloud Run revision on deploy.

## Work queue status

| # | Task | Status | Commit | CR revision | Notes |
|---|------|--------|--------|-------------|-------|
| 01 | Fix period default | qa-passed | b80923a | mata-tools-api-00011-pds | QA 2026-06-14: all 6 gates green |
| 02 | Mobile responsive layout | qa-passed | 2eae35e | mata-tools-api-00011-pds | QA 2026-06-14: all 8 gates green |
| 03 | Mobile card view | qa-passed | c7e8432 | mata-tools-api-00013-q2k | QA 2026-06-14: all 8 gates green |
| 04 | Data freshness indicator | qa-passed | 43224d4 | mata-tools-api-00015-42k | QA 2026-06-14: all 7 gates green (incl. empty-state footer fix at 43224d4) |
| 05 | Port Workload Matrix | qa-passed | 6b2ab74 | mata-tools-api-00016-rkc | QA 2026-06-15 live: all 10 gates green. Hosting was missing (cloudbuild.yaml is CR-only); fixed via firebase deploy --only hosting 2026-06-15. Local parity 2026-06-14: 28 proj, 8 users, 561.0h vs Panso. |
| 06 | Incentive UI polish | qa-passed | f5f5371 | — (hosting only) | QA 2026-06-14: all 4 gates green; × btn 20px, inputs ±2px aligned, caption exact |
| 07 | Restore login (empty Firebase apiKey) | qa-passed | b80923a | mata-tools-api-00011-pds | Fixed in same deploy as 01; confirmed signed in + data loading 2026-06-14 |

Status values: `pending` → `in-progress` → `built` → `qa-passed` / `qa-fail`

## Current active task

**All tasks qa-passed.** Queue complete as of 2026-06-14. Tasks 01–07 all green.

## Diagnosis summary (Opus, 2026-06-13)

### Why "No hours logged on 69 MCIA Interactive Screens in Jun 2026b"

**Root cause — period default bug (code):**
`list_available_periods()` in `tools_api.py` includes `jun2026b` (Jun 16–30) in the period
list even on Jun 13 because the exclusion guard skips it: `not (s.month == today.month)` is
False when both are June. The frontend reverses the list and auto-selects the most recent
period as default — which is `jun2026b`, a future period with zero entries.

**Secondary cause — data pipeline (infra, not fixable here):**
The GCS data (`gs://panso-ph-data/time-entries.csv`) is only as fresh as the last Panso sync.
If Panso's entries-sync job (Panso roadmap task 04) hasn't run, even the correct period may
show stale or incomplete hours. Task 04 in this queue adds a "data as of" indicator; the
actual pipeline fix belongs to Panso.

### Mobile UX gaps identified

1. **`.ic-steps` 2-column grid** collapses badly at narrow widths — addressed in task 02.
2. **Results table overflows** — no `overflow-x:auto` wrapper — addressed in task 02.
3. **Touch interaction** — dropdown uses `mousedown`/`mouseenter` which don't fire reliably on
   touch devices — addressed in task 02.
4. **Period-value select `min-width:200px`** inline style can overflow on narrow screens —
   addressed in task 02.
5. **6-column table unreadable on phone** — addressed in task 03 (card layout).

### Architecture context

- Firebase Hosting `mata-tools` site → `mata-tools.web.app`
- Cloud Run `mata-tools-api`, project `panso-ph`, region `asia-southeast1`
- Data: GCS `gs://panso-ph-data` FUSE-mounted at `/data` in Cloud Run
- Deploy: `cloudbuild.yaml` (Cloud Build trigger) — push from Ryan's PowerShell, run from
  Cloud Shell: `gcloud builds submit --config cloudbuild.yaml` or Cloud Build trigger on push
- All frontend logic lives in `public/tools/incentive.html` (~714 lines, single file)
- All backend logic lives in `app/tools_api.py` (~862 lines)

## Resume notes

_(Write one-line resume notes here when a session cuts off mid-task)_
