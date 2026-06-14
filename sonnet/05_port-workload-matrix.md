# Task 05 — Port the Workload Matrix tool (P1)

Read `00_README.md` first. One task, one session. **Standalone tool port — does NOT depend on
01–04** (different tool entirely); a fresh Sonnet session may run this independently of the
incentive-mobile queue.

## Objective

Port Panso's **Dept → Workload** tab to a new self-contained Mata Web Tools page,
`public/tools/workload-matrix.html`, backed by a new `GET /api/ops/workload` handler in
`app/tools_api.py`, and flip the launcher's "Workload Matrix" tile from `coming-soon` to live.

The tool is a **staff × client-project matrix** for an arbitrary date range, grouping the
columns of two sibling departments (MT - Operations (Domestic) + MT - Sales (Domestic)) under
banner headers, with heat-map shaded hour cells and per-row / per-column / grand totals.

**Verbatim port.** Copy Panso's logic exactly — do NOT re-derive the aggregation, scoping,
sorting, or hour-rounding rules. The hour rounding throughout is `round(seconds / 360) / 10`
(one-decimal hours). Keep it identical so totals match Panso to the decimal.

## Source of truth (read-only — never edit `C:\dev\Panso-Local`)

Backend handler — `C:\dev\Panso-Local\app\panso_local.py`:
- **Lines 3676–3830** — the entire `/api/departments/workload` handler. This is the canonical
  logic: param parsing, sibling-dept member attribution (first-wins), row filtering, cell
  aggregation, project/user sorting, and the JSON response shape. Read this whole slice.
- Helper deps used by that handler (grep each; read a slice):
  - `exclude_leaves` — Panso L275 — **already in Mata** `tools_api.py:258`. Reuse.
  - `all_time_active_emails` — Panso L283 — **NOT in Mata yet → must be ported** (see Build step 1).
  - `load_departments` — Panso L774 — **already in Mata** `tools_api.py:364`. Reuse.
  - `rows_in_range` — Panso L1468 — **already in Mata** `tools_api.py:262`. Reuse.

Frontend renderer — `C:\dev\Panso-Local\app\static\index.html`:
- **Lines 6753–6981** — `renderDeptWorkloadBody(name)`: the complete tab (date inputs, presets,
  fetch, heat-map cell shading, dept-banner header rows, project rows, totals footer).
- **Lines 5293–5298** — `DEPT_WORKLOAD_SIBLINGS` (the Ops+Sales grouping). Copy verbatim.
- CSS: grep `C:\dev\Panso-Local\app\static\index.html` for the classes `dept-alloc`,
  `alloc-cell`, `alloc-group-banner`, `alloc-group-row`, `alloc-proj`, `alloc-user`,
  `alloc-proj-total`, `alloc-lead-pill`, `alloc-zero`, `dept-alloc-header`, `dept-alloc-range`,
  `dept-alloc-presets`, `dept-alloc-stats`, `dept-alloc-wrap` and copy those rules into the new
  page's `<style>`. The banner-row CSS comment is anchored at L1184.
- JS helpers the renderer calls (`esc`, `isoLocal`, `deptColor`, `INTERN_GREY`, the `PTF_TEAL`
  constant): grep Panso for each `def`/`const`; port the small ones into the new page. The Mata
  tool pages are self-contained single files — no shared bundle (see `CLAUDE.md`).

## Build

All new code goes in **two new/edited files only**: `app/tools_api.py` (backend) and
`public/tools/workload-matrix.html` (new frontend). Plus a one-line launcher edit in
`public/index.html`. Do NOT touch `app/access_layer.py` or any HR/incentive tool.

### Step 1 — Backend: port `all_time_active_emails`, then the handler

1. Grep `app/tools_api.py` for where the other helpers live (`exclude_leaves` at 258,
   `rows_in_range` at 262, `load_departments` at 364). Port **`all_time_active_emails`** from
   Panso `panso_local.py:283` verbatim into the same helper region. Read Panso L283 + its body
   first; copy exactly (it returns the set of emails active across all loaded weeks).
2. Add the handler. Mirror the **route + access-gate pattern of the existing HR handlers** —
   grep `tools_api.py` for `if u.path == "/api/hr/interns":` (L1057) to copy the structure:
   the `gate_request(...)` wrapping, the `can_access_scope(...)` 403 guard, and `_send_json`
   usage. Use path **`/api/ops/workload`** (follows Mata's dept-prefix convention:
   `/api/finance/...`, `/api/hr/...`).
3. **Access gate (important):** scope on the **canonical dept name**, not the launcher
   abbreviation:
   ```python
   if not can_access_scope(tier_info, "dept::MT - Operations (Domestic)"):
       return self._send_json({"error": "Restricted to MT - Operations (Domestic)."}, 403, origin=origin)
   ```
   `can_access_scope` matches `load_departments()[n]["name"]` (see `access_layer.py:42`) — the
   string must be the exact canonical dept name.
4. Paste the body of Panso's handler (L3690–3830) verbatim, adapting only the surrounding
   request/response idioms to Mata's (`parse_qs(u.query)`, `self._send_json(..., origin=origin)`,
   `dt.date.fromisoformat`). Keep params (`names`, `start`, `end`, `clients_only`), the
   first-wins sibling attribution, the `_num_prefix` numbered-project rule, the role-bucket
   sort (`role_rank = {"lead":0,"staff":1,"intern":2,"ptf":3}`), and the exact response keys:
   `dept_names, start, end, clients_only, projects, users, users_by_dept, matrix, total_seconds,
   total_hours`. Verify `re` and `dt` are already imported in `tools_api.py` (they are used by
   other handlers — grep to confirm).

### Step 2 — Frontend: new self-contained page

Create `public/tools/workload-matrix.html`. Model its shell (sign-in/auth bootstrap, header,
fetch-to-`/api/...`, error states) on an **existing tool page** — read `public/tools/interns.html`
for the Mata auth + fetch pattern (it is dept-scoped like this one). Then port the Workload body:

- Copy `DEPT_WORKLOAD_SIBLINGS` (Panso L5293–5298) verbatim.
- Port `renderDeptWorkloadBody` (Panso L6760–6981) into the page, repointing its fetch to
  `/api/ops/workload?names=<encoded siblings>&start=&end=&clients_only=true` (the page is for a
  fixed dept, `MT - Operations (Domestic)`, so set `name` to that constant).
- Default range = first-of-month → today (local), with the four presets (This week / This month /
  Last month / YTD). Heat-map cell shading and the green→red >8h threshold copy verbatim.
- Port the required helpers (`esc`, `isoLocal`, `deptColor`, `INTERN_GREY`, `PTF_TEAL`) and the
  `dept-alloc*` / `alloc-*` CSS into the page.

### Step 3 — Mobile (bake it in now — don't repeat the incentive mobile-debt)

The matrix is intrinsically wide. Wrap the table in a horizontal-scroll container
(`overflow-x:auto`; Panso uses `.dept-alloc-wrap`) so it is usable on a 375px viewport — the
first column (Project) should stay readable and the matrix scrolls horizontally. Ensure the
date inputs + preset buttons wrap rather than overflow on narrow screens.

### Step 4 — Launcher: flip the tile live + fix the grant string

In `public/index.html`, the `TOOLS` array entry for `Workload Matrix` (around L177–183) currently
has `grant: 'dept::MT - Ops'`, `url: null`, `live: false`. Change to:
```js
{
  name:  'Workload Matrix',
  dept:  'MT - Ops',
  grant: 'dept::MT - Operations (Domestic)',   // canonical — must match can_access_scope
  url:   '/tools/workload-matrix.html',
  live:  true,
},
```
`dept:` is just the display label (leave the short form). `grant:` MUST be the canonical name or
the tile renders for nobody (and admins only). Confirm the canonical string against
`load_departments()` output.

### Step 5 — Local verification with synthetic data

Run the API locally:
```
PANSO_SKIP_AUTH=1 PANSO_ROOT=C:\dev\Panso-Local python app/tools_api.py
```
(The sandbox has no GCS; pointing `PANSO_ROOT` at the read-only Panso-Local data is the local
substitute — same as the incentive task's local check.)

- Hit `/api/ops/workload?names=MT - Operations (Domestic),MT - Sales (Domestic)&start=2026-06-01&end=2026-06-15&clients_only=true`
  and confirm a JSON matrix comes back with non-empty `projects` and `users`.
- Cross-check 2–3 cells against Panso's own `/api/departments/workload` for the **same params**
  (run Panso locally too). The hour values must match to the decimal.

### Step 6 — Commit

```
git add app/tools_api.py public/tools/workload-matrix.html public/index.html
git commit -m "feat: port Workload Matrix tool (staff x client-project matrix, MT-Ops)"
```
Do NOT push or deploy yet — batch one go-ahead to Ryan for push + Cloud Build deploy.

## QA gate — run as a FRESH, separate session

Run this block standalone; do not read the build session's context. Verify **live, signed-out
where access matters, cache-busted** (incognito or `?v=<timestamp>`), per the queue contract.

- [ ] **Backend parity:** `/api/ops/workload` returns the same hour values as Panso's
  `/api/departments/workload` for identical `names/start/end/clients_only` params — spot-check
  the grand total + 3 individual cells, exact to one decimal.
- [ ] **`all_time_active_emails` ported,** not stubbed — confirm the function body matches Panso
  L283 (grep both).
- [ ] **Matrix renders** on the live page: dept banner row (OPERATIONS / SALES) spans the right
  column counts; LEAD pills, intern grey, PTF teal coloring all present; heat-map shading with
  the >8h green→red flip.
- [ ] **Date range + presets work:** changing From/To reloads; This week / This month / Last
  month / YTD each set the expected range; end < start shows the inline error and does not fetch.
- [ ] **Empty state:** a range with no numbered client projects shows the "No numbered client
  projects between …" banner, not a JS error.
- [ ] **Access control (signed-out / wrong-dept):** an MT-Ops member (or admin) sees the tile and
  the tool loads; a signed-in user WITHOUT `dept::MT - Operations (Domestic)` and not admin does
  NOT see the tile in the launcher AND gets a 403 from `/api/ops/workload` if hit directly.
- [ ] **Launcher grant fixed:** the tile's `grant` is `dept::MT - Operations (Domestic)` (not the
  old `dept::MT - Ops`); confirm an MT-Ops grant actually surfaces the tile.
- [ ] **Mobile (375px):** the matrix scrolls horizontally inside its wrapper, the Project column
  stays readable, and the date controls wrap without overflowing.
- [ ] **No JS errors** in the console on load, range change, or preset click.
- [ ] **Local dev** (`PANSO_SKIP_AUTH=1 PANSO_ROOT=C:\dev\Panso-Local python app/tools_api.py`)
  shows the same matrix as live for the same params.

Mark `status: qa-passed` in repo-root `STATE.md` only when all boxes are green. On any red, add a
`05b-…` follow-up note to this file and leave the row `qa-fail` with the delta.
