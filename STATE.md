phase: EXECUTING
<!-- Opus 2026-06-27 (UI-feedback round): new tasks 12–16 authored from the live walkthrough.
     RUNNABLE NOW (in order, all but 15 touch incentive.html → sequential): 12 (period-picker port) →
     13 (incentive column distinct) → 14 (data-freshness header) → 15 (dept-docs single-source/D11).
     BLOCKED: 16 (remove Workload Matrix) — gated on Panso’s Workload tab going live + QA-green
     (Panso OPUS owns that build). Sonnet commits go into _pipeline/web-tools/_PENDING-COMMANDS.ps1
     (commit-only batch for Ryan); no pushes/deploys. Tasks 01–11 all qa-passed/live. -->
<!-- Opus 2026-06-27 (final QA): tasks 01–11 complete (Phase D done). Advisory: Panso-Local git ref broken;
     Panso _pipeline task-07 stamp to be applied Panso-side. -->
<!-- Opus 2026-06-27 (re-QA): Task 10 QA:PASS — P0 fixed (242b568); redirects green. qa/10-QA-BLOCKER.md kept for history. -->
<!-- Opus 2026-06-24 (ungate pass): 10 & 11 gate cleared (Panso P0 CSV incident closed + Jun 16+). -->
<!-- Opus 2026-06-24 (ungate pass): 10 & 11 gate cleared (Panso P0 CSV incident closed + Jun 16+). -->
<!-- Opus 2026-06-15: round authored. 08 (repo hygiene) + 09 (Dept Documents port, read-only). -->
<!-- Opus 2026-06-15: round authored. 08 (repo hygiene) + 09 (Dept Documents port, read-only). -->
<!-- Opus reconciled 2026-06-15: dev queue 01–07 all qa-passed + live.
     Working tree before task 08 = CRLF churn only (no .gitattributes) + minor housekeeping. -->

<!-- D11 RESOLVED 2026-06-27 (Sonnet, task 15): Dept Documents = read-only on a shared single
     bucket. Panso writes gs://panso-ph-data/data/dept-documents/ (R/W FUSE); Mata reads same path
     (readonly=true FUSE). Drift impossible by construction. No Firestore needed. -->

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
| 08 | Repo hygiene (.gitattributes + housekeeping) | qa-passed | a353595 | — | QA 2026-06-15: all 5 gates green. Working tree clean after reset --hard. |
| 09 | Port Dept Documents (read-only) | qa-passed | b744112 | mata-tools-api-00017-7w7 | QA 2026-06-27 live: all 10 gates green. Tile LIVE→/tools/dept-documents.html (public:true); read-only DOM (0 forms/inputs/buttons); /save absent; helpers verbatim vs Panso L2148–2231; MG-HR parity items:[] = Panso mg-hr.json; admin picker 12 depts re-fetches; empty-state present; anon API→401 (no leak); no app console errors. (G9 375px via shipped @media(max-width:520px); dept-scope 403 code-verified L1657.) |
| 10 | Panso D2: redirect HR tabs | qa-passed | b570ad9 | panso-api-00068-snl | Re-QA 2026-06-27 live (after P0 fix `242b568`, app at 659,174 b): all 6 gates green. Calendar/Interns/Pay-Matrix tabs → "This tool has moved to Mata Web Tools" notice (link→mata-tools.web.app, no old view); leaves/birthdays/milestones → HR Calendar notice; MG-Finance Incentive renders calculator normally (untouched); no console errors on clean UI nav; defs intact 5596/5790/6059, zero dispatch. App loads + period(89)/dept(27) dropdowns populate — P0 SyntaxError gone. (Earlier qa-block was a pre-existing bug from commit 259624a/Panso task 23, now fixed.) |
| 11 | Panso D1: strip Incentive | qa-passed | aac1b35 | panso rev 1139e87e | QA 2026-06-27 live (628,734 b, down ~30KB): all 6 gates green. MG-Finance Incentive tab GONE (dept tabs = overview/documents only); app loads clean (89/27 dropdowns, no SyntaxError — PARSE OK); `/api/finance/incentive`→404 + zero src refs to renderFinanceIncentive/renderIncentiveResult; `openPayBasisModal` + pay-matrix helpers (panso_local L219/228) preserved; task-10 HR redirects still intact (no regression); Mata Incentive Calculator unaffected (defaults Jun 2026b = current period). ⚠ Advisory: Panso repo local git ref is broken (HEAD unresolvable) — flagged to Panso pipeline; does not affect deploy. |
| 12 | Incentive: port Panso period picker + ‹/› steppers | qa-passed | (see COMMIT 1/2) | — | [A1] pp-tabs + pp-cluster + stepPeriod() ported verbatim from Panso (markup/CSS/JS). Picker delimited for mata-hr IAM reuse. 41938→51442 B. Parse OK. ⚠ mata-hr: intern-allowance-matrix.html needs the identical picker block. QA: PASS 2026-06-27 |
| 13 | Incentive: make incentive column visually distinct | qa-passed | (see COMMIT 1/2) | — | [A2] .ic-inc-col (green left-border + #f0fdf4 bg) on TH/TD body/TD footer; mobile .ic-card-incentive enhanced. Styling only — no formula change. +672 B. Parse OK. QA: PASS 2026-06-27 |
| 14 | Incentive: persistent "Data as of" freshness indicator | qa-passed | (see COMMIT 1/2 + 2/2) | — | [A3] Header chip (.data-freshness-chip, id="data-freshness") populated from periodsData.data_synced_at (DATA_DIR mtime, UTC). tools_api.py list_available_periods() returns data_synced_at. Results-footer data_as_of unchanged. Parse OK / py_compile OK. QA: PASS 2026-06-27 |
| 15 | Dept Documents: single-source sync w/ Panso (D11) | qa-passed | (see COMMIT 2/2) | — | **D11 RESOLVED: read-only single shared bucket.** Panso writes gs://panso-ph-data/data/dept-documents/ (R/W FUSE); Mata reads same path (readonly=true FUSE, cloudbuild.yaml L25-26). Drift impossible by construction. docs_dir() gains D11 comment + mkdir try/except guard. dept-documents.html: "Synced from department's Panso documents." UI hint added. py_compile OK, Parse OK. QA: PASS 2026-06-27 |
| 16 | Remove Workload Matrix from Mata | queued · BLOCKED (Panso) | — | — | [feedback 16b] Workload Matrix is moving INTO Panso as a per-dept tab (Panso OPUS owns the build). Mata’s half: fully remove it — `public/tools/workload-matrix.html` (delete), launcher tile in `public/index.html` (L178–183), `/api/ops/workload` handler (tools_api.py:1522) + `all_time_active_emails` helper (264; **confirmed used only by workload** → remove). ⚠ GATE: do NOT run until Panso’s Workload tab is LIVE + QA-green (else coverage gap). Block-dependency on the Panso pipeline. |

Status values: `pending` → `in-progress` → `built` → `qa-passed` / `qa-fail` (`queued · GATED`/`BLOCKED` = pre-authored, blocked by a gate)

## Current active task

**Tasks 12–15 qa-passed (Opus live QA 2026-06-27). Task 16 BLOCKED (Panso Workload tab not yet live).**

Commit batch staged in `_pipeline/web-tools/_PENDING-COMMANDS.ps1` (2 commits, commit-only):
- COMMIT 1/2: incentive.html (tasks 12+13+14) — period picker, column distinction, freshness chip
- COMMIT 2/2: tools_api.py + dept-documents.html (tasks 14b+15) — data_synced_at, D11 docs_dir

**16 — BLOCKED (Panso):** remove Workload Matrix from Mata once Panso’s Workload tab is live + QA-green.

Rails: NUL-guard + JS parse-gate edited HTML, `py_compile` tools_api.py, byte-size sentinel,
grep-then-slice, commit-only into `_pipeline/web-tools/_PENDING-COMMANDS.ps1` (Ryan runs it). No deploys.

Open items for Ryan (not blocking):
- **Panso git** — `C:\dev\Panso-Local` local git ref is broken (HEAD unresolvable); Panso pipeline
  should repair before its next commit. (Deploy unaffected.)
- **Panso `_pipeline/STATE.md`** — apply the D1-strip stamp (task 07 done, `aac1b35`) Panso-side.

## Diagnosis summary (Opus, 2026-06-13)

### Why "No hours logged on 69 MCIA Interactive Screens in Jun 2026b"

**Root cause — period default bug (code):**
`list_available_periods()` in `tools_api.py` includes `jun2026b` (Jun 16–30) in the period
list even on Jun 13 because the exclusion guard skips it: `not (s.month == today.month)` is
False when both are June. The frontend reverses the list and auto-selects the most recent
period as default — which is `jun2026b`, a future period with 