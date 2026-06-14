# Task 03 — Mobile card layout for results table (P1)

Read `00_README.md` first. Depends on task 02 being QA-passed. One task, one session.

## Problem

After task 02, the results table is horizontally scrollable on mobile — which is functional
but not ideal. A 6-column table with 5–20 rows requires persistent horizontal swiping to see
all columns. On a phone, a per-member card layout is much more scannable.

## Objective

On narrow viewports (≤ 640px), render each team member as a vertical card instead of a table
row. Desktop behavior (table) is completely unchanged — same HTML structure, toggled via CSS
`display` rules. No new data fetched; same `items` array, same checkboxes, same recomputeTotals
logic.

## Build

All changes in **`public/tools/incentive.html`** only.

### Strategy

The cleanest approach: generate BOTH the table rows (existing) AND card elements for each
member inside `renderIncentiveResult()`, then use CSS to show only the appropriate one per
viewport. This avoids JS viewport detection and keeps `recomputeTotals()` targeting the same
`tr[data-row-idx]` and `.ic-row-cb` selectors it already uses.

The cards live inside a `<div id="ic-cards">` that is `display:none` on desktop and
`display:block` on mobile; the table is the reverse.

### A — Add CSS (in `<style>`, inside the existing mobile media query from task 02)

```css
/* Card layout — mobile only */
#ic-cards { display: none; }

@media (max-width: 640px) {
  /* Hide table, show cards */
  #ic-table-wrap { display: none !important; }
  #ic-cards      { display: block; }

  .ic-card {
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: var(--sp-12) var(--sp-14);
    margin-bottom: var(--sp-8);
    background: var(--panel);
    display: grid;
    grid-template-columns: 32px 1fr;
    gap: var(--sp-8) var(--sp-10);
    align-items: start;
  }
  .ic-card.ineligible { opacity: 0.55; }
  .ic-card-cb   { grid-column: 1; grid-row: 1 / 3; display: flex; align-items: flex-start; padding-top: 3px; }
  .ic-card-body { grid-column: 2; }
  .ic-card-name { font-weight: 600; font-size: var(--fs-md); }
  .ic-card-email { font-size: var(--fs-sm); color: var(--muted); margin-bottom: var(--sp-6); }
  .ic-card-fields {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: var(--sp-4) var(--sp-12);
    font-size: var(--fs-sm);
  }
  .ic-card-field label { color: var(--muted); display: block; }
  .ic-card-field value { font-weight: 600; }
  .ic-card-incentive value { color: var(--good); font-size: var(--fs-md); }
}
```

### B — Generate card HTML in `renderIncentiveResult()`

In the function, after building `rowsHtml` (the table rows), build a parallel `cardsHtml`:

```javascript
var cardsHtml = ordered.map(function(it, i) {
  var fte      = !!it.is_fte;
  var rateVal  = it.has_rate ? '₱' + it.hourly_rate.toFixed(2) : '<span class="muted" style="color:#b91c1c">—</span>';
  var baseVal  = it.has_rate ? '₱' + it.base.toFixed(2) : '<span class="muted">—</span>';
  var incVal   = (fte && it.has_rate) ? '<strong style="color:var(--good)">₱' + it.incentive.toFixed(2) + '</strong>' : '<span class="muted">—</span>';
  var roleTag  = fte ? '' :
    '<span style="margin-left:6px;font-size:var(--fs-9-5);font-weight:700;color:#92400e;background:#fef3c7;border:1px solid #fcd34d;border-radius:999px;padding:1px var(--sp-7);white-space:nowrap">not eligible · ' + esc(it.role || 'non-FTE') + '</span>';
  var defChecked = (fte && it.has_rate) ? 'checked' : '';
  var cbAttrs    = fte ? '' : 'disabled ';
  return '<div class="ic-card' + (fte ? '' : ' ineligible') + '" data-row-idx="' + i + '" data-hours="' + it.hours + '" data-base="' + it.base + '" data-incentive="' + it.incentive + '" data-has-rate="' + (it.has_rate?1:0) + '" data-fte="' + (fte?1:0) + '">' +
    '<div class="ic-card-cb">' +
      '<input type="checkbox" class="ic-row-cb" ' + defChecked + ' ' + cbAttrs + ' style="accent-color:var(--good);cursor:' + (fte ? 'pointer' : 'not-allowed') + ';width:18px;height:18px">' +
    '</div>' +
    '<div class="ic-card-body">' +
      '<div class="ic-card-name">' + esc(it.user_name) + roleTag + '</div>' +
      '<div class="ic-card-email">' + esc(it.user_email) + '</div>' +
      '<div class="ic-card-fields">' +
        '<div class="ic-card-field"><label>Hours</label><value>' + it.hours.toFixed(2) + 'h</value></div>' +
        '<div class="ic-card-field"><label>Hourly rate</label><value>' + rateVal + '</value></div>' +
        '<div class="ic-card-field"><label>Base</label><value>' + baseVal + '</value></div>' +
        '<div class="ic-card-field ic-card-incentive"><label>Incentive</label><value>' + incVal + '</value></div>' +
      '</div>' +
    '</div>' +
  '</div>';
}).join('');
```

### C — Add card container to the rendered HTML

In `document.getElementById('ic-result').innerHTML = ...`, after the table `</div>` (outer
panel div), add:

```javascript
... + '</div>' + // closes the table outer panel div
'<div id="ic-cards">' + cardsHtml + '</div>' +
...
```

Wrap the table's outer div in a wrapper with `id="ic-table-wrap"`:

```javascript
'<div id="ic-table-wrap">' +
  '<div style="background:var(--panel);border:1px solid var(--border);border-radius:8px;overflow:hidden">' +
    '<div style="overflow-x:auto;-webkit-overflow-scrolling:touch">' +
      '<table ...>' + ... + '</table>' +
    '</div>' +
  '</div>' +
'</div>' +
```

### D — Ensure `recomputeTotals()` works for both layouts

`recomputeTotals()` queries `#ic-result tbody tr` and `.ic-row-cb`. The card checkboxes also
have class `ic-row-cb`, so the checkbox event wiring (`.ic-row-cb` `change` listener and
`#ic-cb-all` toggle) will already fire for card checkboxes too.

However, `recomputeTotals()` reads `data-*` attributes from `tr` elements:
```javascript
var rows = document.querySelectorAll('#ic-result tbody tr');
```

This only captures table rows. The cards also have `data-row-idx`, `data-hours`, `data-base`,
`data-has-rate`, `data-fte` attributes (identical structure). Change the selector to capture
both:

```javascript
var rows = document.querySelectorAll('#ic-result tbody tr, #ic-result .ic-card');
```

This is the only change needed to `recomputeTotals()`. Test that `checked.length`,
`sumBase`, and `sumHours` compute correctly on both mobile (cards) and desktop (table rows).

The `incCell` update in `recomputeTotals()` targets `tr.querySelector('td:last-child')`. For
cards, the incentive cell is `.ic-card-incentive value`. Update that block:

```javascript
rows.forEach(function(tr) {
  var cb      = tr.querySelector('.ic-row-cb');
  var hasRate = tr.dataset.hasRate === '1';
  var base    = parseFloat(tr.dataset.base) || 0;
  // Support both table row (td:last-child) and card (.ic-card-incentive value)
  var incCell = tr.querySelector('td:last-child') || tr.querySelector('.ic-card-incentive value');
  if (!incCell) return;
  // ... rest unchanged ...
});
```

### E — "Select all" checkbox for cards

The `#ic-cb-all` header checkbox (`checked` toggle all) targets `.ic-row-cb`. Cards also use
`.ic-row-cb`. No change needed — it already works for cards.

### F — Commit

```
git add public/tools/incentive.html
git commit -m "feat: mobile card layout for incentive results"
```

Ask Ryan for go-ahead to push + deploy.

## QA gate — run as a FRESH, separate session

Use Chrome DevTools iPhone SE emulation (375×667) AND test at 900px desktop width.

**Mobile (375px):**
- [ ] Cards render for each team member — name, email, hours, rate, base, incentive.
- [ ] Non-FTE members show role tag and are visually dimmed (`opacity: 0.55`).
- [ ] Missing-rate members show "—" in Hourly rate and Base; incentive shows "—".
- [ ] Tapping a card's checkbox toggles it and `recomputeTotals()` updates the summary bar
  correctly (FTE count, multiplier, total incentive).
- [ ] "Select all" header checkbox (if visible — it's in the table header, hidden on mobile)
  test via the `#ic-cb-all` element directly: `document.getElementById('ic-cb-all').click()`
  in console → all card checkboxes toggle.
- [ ] Table is NOT rendered / `#ic-table-wrap` is `display:none`.
- [ ] Multiplier and Amount inputs still update the incentive values in the cards on change.

**Desktop (≥ 900px):**
- [ ] Table renders correctly (unchanged from task 02).
- [ ] Cards div (`#ic-cards`) is `display:none`.
- [ ] `recomputeTotals()` still works — table checkboxes update the summary bar.

**Both:**
- [ ] Selecting multiple projects (multi-select) works — merged items display as both cards
  (mobile) and table rows (desktop).
- [ ] No JS console errors.

Mark `status: qa-passed` in `STATE.md` only when all boxes are green.
