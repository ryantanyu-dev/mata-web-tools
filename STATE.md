# Mata Web Tools — Session State

This file tracks the current status of the Sonnet work queue (`sonnet/`).
Update it after every session: set status, record commit SHA and Cloud Run revision on deploy.

## Work queue status

| # | Task | Status | Commit | CR revision | Notes |
|---|------|--------|--------|-------------|-------|
| 01 | Fix period default | qa-passed | b80923a | mata-tools-api-00011-pds | QA 2026-06-14: all 6 gates green |
| 02 | Mobile responsive layout | qa-passed | 2eae35e | mata-tools-api-00011-pds | QA 2026-06-14: all 8 gates green |
| 03 | Mobile card view | pending | — | — | depends on 02 QA-passed |
| 04 | Data freshness indicator | pending | — | — | depends on 01 QA-passed |
| 05 | Port Workload Matrix | pending | — | — | standalone tool port — independent of 01–04; runnable in its own session |
| 06 | Incentive UI polish | pending | — | — | 3 fixes (bigger × button, align step1/step2, caption text); depends on 02 QA-passed |
| 07 | Restore login (empty Firebase apiKey) | qa-passed | b80923a | mata-tools-api-00011-pds | Fixed in same deploy as 01; confirmed signed in + data loading 2026-06-14 |

Status values: `pending` → `in-progress` → `built` → `qa-passed` / `qa-fail`

## Current active task

**Next: 04 — Data freshness indicator** (depends on 01, now QA-passed) — or **03 — Mobile card view** (depends on 02). Pick lowest runnable.

**Infra fix landed (2026-06-14):** `cloudbuild.yaml` default `_FIREBASE_API_KEY` changed from `''` to the actual key. Push `cloudbuild.yaml` + `STATE.md` together next commit.

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
