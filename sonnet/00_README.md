# Mata Web Tools — Sonnet work queue

This folder is a **self-driving work queue** for making the MG-Finance Incentive Calculator
fully usable on mobile. Point a session titled **"Mata Web Tools (Sonnet)"** here and work
the numbered tasks in order. Each numbered file is one self-contained iteration; every
iteration is QA'd by a fresh session before the queue advances.

Repo: `C:\dev\Mata-Web-Tools`, Firebase Hosting site `mata-tools` → `mata-tools.web.app`,
Cloud Run service `mata-tools-api` (Python), project `panso-ph`, region `asia-southeast1`,
data in `gs://panso-ph-data` (FUSE-mounted at `/data`).

## How to work the queue

1. **Open `STATE.md` (repo root).** Pick the lowest-numbered task whose `status` is `pending`. Respect `depends-on`.
2. **One task per session.** Read the task file, execute it. Do not fold multiple tasks into one session.
3. **QA as a fresh session.** When the build is done, start a NEW session and run only that task's `## QA gate` block. Mark it `QA-passed` only when all boxes are green.
4. **Record it** in `STATE.md` (status → `qa-passed` or `qa-fail`, commit SHA, Cloud Run revision if deployed).
5. **Context tightens mid-task:** commit WIP, write a one-line resume note in `STATE.md`, stop. A fresh session resumes from that note.

## Global conventions (keep task files lean)

- **Single file per tool.** `public/tools/incentive.html` is the only frontend file for the incentive calculator. Never introduce a separate JS bundle or CSS file. All changes go in that file.
- **Backend:** `app/tools_api.py` is the only Python file to edit. `app/access_layer.py` is read-only — re-copy from Panso when it updates.
- **Context discipline (Sonnet, standard 200k):** never read `incentive.html` or `tools_api.py` whole unless the task explicitly requires it. Grep for line anchors, read 40–100 line slices. The files are <900 lines each — manageable — but still use slices to stay lean.
- **Verbatim formula ports.** Never re-derive incentive/pay formulas. Grep Panso for the exact code path and copy.
- **Deploy runbook:** commit/push from **Ryan's PowerShell** (`git push`); deploy via **Cloud Build** trigger (or manual `gcloud builds submit --config cloudbuild.yaml` from Cloud Shell). Cowork sandbox has no external network.
- **Side-effects:** batch deploy confirmations — do all the code work, then ask Ryan for one go-ahead before deploying.

## Task index

| # | File | Priority | Depends on | Objective |
|---|------|----------|------------|-----------|
| 01 | `01_fix-period-default.md` | P0 | — | Fix wrong default period (future half-period selected → "no hours"). |
| 02 | `02_mobile-responsive-layout.md` | P0 | 01 | Full mobile responsive pass: breakpoints, table scroll, touch events. |
| 03 | `03_mobile-card-view.md` | P1 | 02 | Replace 6-col table with card layout on narrow viewports. |
| 04 | `04_data-freshness-indicator.md` | P2 | 01 | Show "data as of [date]" so users know if data is stale. |
| 05 | `05_port-workload-matrix.md` | P1 | — | Port Panso's Workload tab → new `workload-matrix.html` tool (staff × client-project matrix, MT-Ops). Standalone — independent of 01–04. |
| 06 | `06_incentive-ui-polish.md` | P2 | 02 | Incentive Calculator polish: bigger project-clear (×) button, align Step 1/Step 2 inputs, update formula caption. |
| 07 | `07_fix-firebase-apikey-deploy.md` | **P0 BLOCKER** | — | Restore login: deployed `/api/firebase-config` serves empty `apiKey` (rev 00010). Cloud Shell redeploy with `_FIREBASE_API_KEY`. **Gates live-QA of 01–06 — do first.** |
| 08 | `08_repo-hygiene-gitattributes.md` | P2 | — | Add `.gitattributes` (LF normalize) + commit housekeeping; kills CRLF churn masking diffs. Repo-only, no deploy. |
| 09 | `09_port-dept-documents.md` | P2 | — | Port Panso's per-dept Documents tab → new read-only `dept-documents.html` tool. Standalone. Read-only (D11 open decision). |
| 10 | `10_panso-redirect-hr-tabs.md` | P1 | — | **⛔ GATED.** Panso Phase D2: redirect Calendar/Interns/PayMatrix tabs → Mata Web Tools (no backend deletion). Edits Panso repo. Gate: Panso P0 closed + Jun 16+. |
| 11 | `11_panso-strip-incentive.md` | P1 | 10 | **⛔ GATED.** Panso Phase D1: strip Incentive Calculator from Panso (destructive). Edits Panso repo. Gate: as 10 + task 10 deployed green. |

**Mobile-ready exit gate (after 01–02):** the incentive calculator selects the correct default period and the results table is readable and usable on a 375px iPhone viewport.

> **Runnable now:** 08 (repo hygiene) and 09 (Dept Documents port). **Tasks 10 & 11 are GATED** —
> they edit the Panso repo (Phase C/D) and must NOT run until the gate at the top of each file is
> met (Panso P0 CSV incident closed AND today ≥ Jun 16, 2026); 11 also waits on 10 deployed green.

> **Note:** tasks 01–04 + 06 are the incentive-calculator line; task 05 is a separate tool port sharing this executor-visible queue (per D08). 05 carries no dependency on the others; 06 depends on 02.

## QA discipline

A QA session runs after every iteration. It is a *different* session from the build session, runs only the `## QA gate` block, and the queue does not advance on a red result. A failed QA adds a follow-up iteration to the same task file.
