phase: EXECUTING
<!-- Opus 2026-06-24: task 09 QA:PASS (all 10 gates green) — see row 09 Notes. Queue 01–09 done.
     Nothing runnable remains except GATED 10 & 11. GATED (do NOT run): 10 (Panso HR-tab redirects) +
     11 (Panso incentive strip) — gate = Panso P0 closed AND today ≥ Jun 16; 11 also waits on 10 deployed
     green. These edit the Panso repo (Phase C/D). Today is Jun 24 (date part met); confirm Panso P0
     closed before unblocking 10/11. -->
<!-- Opus 2026-06-15: round authored. 08 (repo hygiene) + 09 (Dept Documents port, read-only). -->
<!-- Opus reconciled 2026-06-15: dev queue 01–07 all qa-passed + live.
     Working tree before task 08 = CRLF churn only (no .gitattributes) + minor housekeeping. -->

<!-- OPEN DECISION D11 (for Ryan): Dept Documents ported read-only (no add/delete) because Cloud Run
     /data is read-only — mirrors D03 Pay-Matrix precedent. Confirm, or request a writable (Firestore)
     follow-up. Logged in _pipeline/web-tools/STATE.md open-decisions. -->

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
| 09 | Port Dept Documents (read-only) | qa-passed | b744112 | mata-tools-api-00017-7w7 | QA 2026-06-24: all 10 gates green (live + code). Tile LIVE→/tools/dept-documents.html (public:true); read-only DOM (0 forms/inputs/buttons); no `/save` route; helpers verbatim vs Panso L2148–2231; parity MG-HR→items:[] = Panso mg-hr.json (documents:[]); admin picker=12 depts re-fetches on change; empty-state "No documents saved yet."; anon API→401 (no leak); no app console errors (lone error = browser-extension msg-channel artifact). Verification notes: dept-scope 403 (non-admin querying other dept) code-verified at handler L1657 + anon-401 confirmed live (couldn't impersonate a 2nd non-admin account); 375px verified via responsive CSS @media(max-width:520px) — resize_window didn't change render viewport. |
| 10 | Panso D2: redirect HR tabs | queued · GATED | — | — | Edits Panso repo. Gate: Panso P0 closed + Jun 16+. |
| 11 | Panso D1: strip Incentive | queued · GATED | — | — | Edits Panso repo. Gate: as 10 + task 10 deployed green. |

Status values: `pending` → `in-progress` → `built` → `qa-passed` / `qa-fail` (`queued · GATED` = pre-authored, blocked by a gate)

## Current active task

**Queue 01–09 all qa-passed/live (09 QA:PASS 2026-06-24).** No ungated work remains.
**10 & 11 are GATED** — they edit the Panso repo. Date gate (Jun 16+) is now met (today Jun 24), but the
**Panso P0 CSV-incident gate is unconfirmed** — do NOT run 10/11 until Ryan confirms Panso P0 is closed.
When confirmed: run 10 (HR-tab redirects), deploy+QA green, then 11 (incentive strip).

## Diagnosis summary (Opus, 2026-06-13)

### Why "No hours logged on 69 MCIA Interactive Screens in Jun 2026b"

**Root cause — period default bug (code):**
`list_available_periods()` in `tools_api.py` includes `jun2026b` (Jun 16–30) in the period
list even on Jun 13 because the exclusion guard skips it: `not (s.month == today.month)` is
False when both are June. The frontend reverses the list and auto-selects the most recent
period as default — which is `jun2026b`, a future period with zero entries.
