# Task 01 — Fix the default period selection (P0)

Read `00_README.md` first. One task, one session.

## Problem (root cause — diagnosed by Opus 2026-06-13)

`list_available_periods()` in `app/tools_api.py` includes `jun2026b` (Jun 16–30) in the
pay-period list even when today is still in the first half (today = Jun 13). It does so
because the exclusion guard is:

```python
if y == today.year and s > today and not (s.month == today.month):
    continue
```

`s.month == today.month` is True for Jun 2026b (`s = 2026-06-16`, `today.month = 6`), so the
guard does NOT fire and the period is included.

The frontend in `public/tools/incentive.html`, function `populatePeriodValues(type)`, then
reverses the list (most-recent first) and picks `list[0]` as the default:

```javascript
var list = (STATE.periods[type] || []).slice().reverse();
// ...
STATE.period.value = list[0].value;  // ← defaults to jun2026b
valueSelect.value  = list[0].value;
```

Since Jun 2026b starts June 16 (3 days in the future), there are zero time entries → the
calculator shows "No hours logged … in this period." every time it loads.

## Objective

Make the calculator default to the most recent pay period whose **start date ≤ today**, so a
user opening it on Jun 13 lands on Jun 2026a (Jun 1–15) — the live period — not Jun 2026b.

## Build

All changes are in **`public/tools/incentive.html`** only. Do not touch the backend.

The API already returns `start` (ISO date string) for every period object. Use it.

### Step 1 — Locate `populatePeriodValues` in `incentive.html`

Grep for `populatePeriodValues` to get the line number. Read a 20-line slice around it.
Current code (approximately lines 674–690 as of the diagnosed state):

```javascript
function populatePeriodValues(type) {
  var list = (STATE.periods[type] || []).slice().reverse();
  valueSelect.innerHTML = list.map(function(p) {
    return '<option value="' + esc(p.value) + '">' + esc(p.label) + '</option>';
  }).join('');
  if (list.length) {
    STATE.period.type  = type;
    STATE.period.value = list[0].value;
    valueSelect.value  = list[0].value;
  }
}
```

### Step 2 — Replace with the fixed version

Replace the body of `populatePeriodValues` with:

```javascript
function populatePeriodValues(type) {
  var list = (STATE.periods[type] || []).slice().reverse();
  var todayStr = new Date().toISOString().slice(0, 10); // "2026-06-13"
  valueSelect.innerHTML = list.map(function(p) {
    return '<option value="' + esc(p.value) + '">' + esc(p.label) + '</option>';
  }).join('');
  if (list.length) {
    STATE.period.type = type;
    // Default to the most recent period that has already started (start <= today).
    // Falls back to list[0] (most recent) if every period is in the future.
    var defaultPeriod = list.find(function(p) { return p.start <= todayStr; }) || list[0];
    STATE.period.value = defaultPeriod.value;
    valueSelect.value  = defaultPeriod.value;
  }
}
```

No other changes to this function. The `list` array elements already include `start` (the API
returns it from `list_available_periods()`). Verify with: grep `"start"` in the
`list_available_periods()` return block in `tools_api.py`.

### Step 3 — Verify the option label order is preserved

The `<option>` elements still render in reversed order (most recent at top). Only the default
*selection* changes. Confirm the `valueSelect.innerHTML` line is unchanged.

### Step 4 — Commit

```
git add public/tools/incentive.html
git commit -m "fix: default period to most recent started (not future jun2026b)"
```

Do NOT push or deploy yet — ask Ryan for one go-ahead to push + trigger deploy.

## QA gate — run as a FRESH, separate session

Run this block standalone. Do not read the build session's context.

Setup: open `mata-tools.web.app/tools/incentive.html` (or local dev server) and sign in.
Today's date for QA: check `new Date().toISOString().slice(0,10)` in browser console.

- [ ] **Period dropdown defaults to Jun 2026a** (Jun 1–15) when today is Jun 13, 2026 — NOT Jun 2026b.
- [ ] **Jun 2026b is still present** in the dropdown (just not the default). Manually selecting it
  shows the "No hours" message (correct — period is in the future / no entries yet).
- [ ] **Jun 2026a shows actual data** for at least one project with known hours (e.g. pick any
  active MT project and confirm hours appear).
- [ ] **Switching period type** (pay period → week → month) each time re-selects a sensible
  default (most recent started period in that type's list).
- [ ] **Reload button** still works — after manually selecting a different period and hitting
  Reload, the result refreshes for the manually selected period, not the default.
- [ ] **No JS errors** in browser console on load or on period type switch.
- [ ] Local dev (`PANSO_SKIP_AUTH=1 PANSO_ROOT=C:\dev\Panso-Local python tools_api.py`) shows
  the same behavior — default lands on the current period, not a future one.

Mark `status: qa-passed` in `STATE.md` only when all boxes are green.
