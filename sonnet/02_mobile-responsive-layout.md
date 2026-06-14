# Task 02 — Full mobile responsive pass (P0)

Read `00_README.md` first. Depends on task 01 being QA-passed. One task, one session.

## Problem (diagnosed by Opus 2026-06-13)

The incentive calculator is functionally inaccessible on a phone viewport (375–430px width).
Five concrete failure modes, in order of severity:

1. **`.ic-steps` 2-column grid collapses badly.** The `grid-template-columns: 3fr 2fr` layout
   makes the project search input ~220px and the multiplier/amount inputs ~145px on a 375px
   screen (minus 48px for padding). Both are usable but tight, and the Step labels are cramped.

2. **Results table overflows.** The 6-column table (`☐`, `Member`, `Hours`, `Hourly rate`,
   `Base (rate × hrs)`, `Incentive`) is ~650px wide and has no `overflow-x:auto` wrapper.
   It causes the entire page body to scroll horizontally — the worst mobile UX pattern.

3. **Project dropdown touch behavior.** Row selection uses `mousedown` + `e.preventDefault()`.
   On mobile Safari and Chrome, `mousedown` fires but the `e.preventDefault()` suppresses the
   expected tap behavior on some devices. `mouseenter`/`mouseleave` hover states don't fire at
   all on touch devices, leaving rows with no visual feedback on press.

4. **Period bar `<select>` min-width.** `min-width:200px` on the period-value select can cause
   the bar to overflow on screens narrower than ~430px when combined with the type select and
   Reload button.

5. **`#shell` 24px horizontal padding** on a 375px phone leaves 327px content width — workable
   but could be reduced to 16px on mobile to gain 16px.

## Objective

Make every part of the incentive calculator page comfortably usable at 375px (iPhone SE) width
without horizontal scroll, with tap-friendly interactions.

## Build

All changes in **`public/tools/incentive.html`** only. Add a `<style>` block of `@media`
overrides just before `</style>` in the `<head>`, and patch the two JS interaction points.

### A — CSS breakpoint additions

Append the following inside the existing `<style>` block (just before `</style>`):

```css
/* ── Mobile responsive (≤ 640px) ── */
@media (max-width: 640px) {
  #shell {
    padding: var(--sp-16) var(--sp-16);
  }
  .shell-hd {
    gap: var(--sp-8);
    margin-bottom: var(--sp-16);
  }
  .period-bar {
    gap: var(--sp-8);
    padding: var(--sp-10) var(--sp-12);
  }
  .period-bar select {
    min-width: 0;        /* override the 200px min-width on period-value */
    flex: 1;
    max-width: 100%;
  }
  /* Stack Steps to a single column */
  .ic-steps {
    grid-template-columns: 1fr;
    row-gap: 10px;
  }
  /* Summary bar: stack the total below the counts on very narrow screens */
  .ic-summary {
    gap: 12px;
  }
  .ic-summary > div:last-of-type {
    margin-left: 0;
    text-align: left;
  }
}
```

Note: `.period-bar select` already has no explicit `min-width` in the base CSS *except* the
inline `style="min-width:200px"` on the `#period-value` select element itself. That inline
style takes precedence over the `<style>` block, so you must **also** remove the inline
`min-width:200px` from the `#period-value` select's `style` attribute and replace it with a
CSS class or rely on the `@media` override. The cleanest fix: remove `min-width:200px` from
the inline style entirely and add `min-width: 200px` to `.period-bar select` in the base CSS
(outside the media query), then override it to `0` in the media query. This keeps the desktop
behavior identical.

Grep for `min-width:200px` to locate the exact line.

### B — Results table: wrap in overflow-x:auto

In `renderIncentiveResult()`, the table is built with this pattern (grep for `overflow:hidden`
to find the outer div):

```javascript
'<div style="background:var(--panel);border:1px solid var(--border);border-radius:8px;overflow:hidden">' +
  '<table style="width:100%;border-collapse:collapse;font-size:var(--fs-md)">' +
  ...
```

Replace the outer div's style to add horizontal scroll:

```javascript
'<div style="background:var(--panel);border:1px solid var(--border);border-radius:8px;overflow:hidden">' +
  '<div style="overflow-x:auto;-webkit-overflow-scrolling:touch">' +
    '<table style="width:100%;min-width:560px;border-collapse:collapse;font-size:var(--fs-md)">' +
    ...
    '</table>' +
  '</div>' +
```

Add a matching `</div>` after `</table>` (before the closing `</div>` of the outer panel div).
Set `min-width:560px` on the `<table>` so it doesn't collapse to an unreadable width when the
scroll container is narrower than the natural table width.

**Count your closing `</div>` tags carefully** — the table section ends with
`</tfoot></table></div>` currently. After the change it must be `</tfoot></table></div></div>`.

### C — Project dropdown: add touch support

In the `renderDropdown()` function, rows are created with `mousedown` selection and
`mouseenter`/`mouseleave` hover. Patch the event listener block (grep for
`ic-tt-row.*mousedown` to locate it):

Current pattern (approximately):
```javascript
ttDropdown.querySelectorAll('.ic-tt-row').forEach(function(row) {
  row.addEventListener('mousedown', function(e) {
    e.preventDefault();
    // ... toggle selection ...
    triggerCalc();
  });
  row.addEventListener('mouseenter', function() { ... });
  row.addEventListener('mouseleave', function() { ... });
});
```

Replace with:
```javascript
ttDropdown.querySelectorAll('.ic-tt-row').forEach(function(row) {
  // Use 'click' (fires on both mouse and touch) instead of 'mousedown'
  row.addEventListener('click', function(e) {
    e.preventDefault();
    e.stopPropagation();
    var proj = row.dataset.project;
    var idx  = _incentiveState.projects.indexOf(proj);
    if (idx === -1) {
      _incentiveState.projects.push(proj);
    } else {
      _incentiveState.projects.splice(idx, 1);
    }
    _incentiveState.project = _incentiveState.projects[0] || '';
    renderDropdown(ttInput._searchVal || '');
    triggerCalc();
  });
  // Mouse hover (desktop only — ignored on touch, that's fine)
  row.addEventListener('mouseenter', function() {
    if (!_incentiveState.projects.includes(row.dataset.project))
      row.style.background = 'var(--hover)';
  });
  row.addEventListener('mouseleave', function() {
    if (!_incentiveState.projects.includes(row.dataset.project))
      row.style.background = '';
  });
});
```

**Note on focus/blur:** The current `blur` listener hides the dropdown after 150ms
(`setTimeout(..., 150)`). The `mousedown` → `e.preventDefault()` pattern was needed to keep
focus on the input so the blur didn't fire before selection. With `click`, the blur fires
first (on the 150ms timer) then the click fires — but only when using a mouse. On touch,
`touchstart` → `touchend` → `click` fires without a focus-change event, so the 150ms timeout
is irrelevant. To be safe, increase the blur timeout to 250ms so mouse clicks on rows still
register before the dropdown hides:

```javascript
ttInput.addEventListener('blur', function() {
  setTimeout(function() {
    ttDropdown.style.display = 'none';
    ttInput._searchVal = '';
    updateInputDisplay();
  }, 250);  // was 150 — increased for click latency on slow devices
});
```

### D — Verify the `.ic-steps` grid change doesn't break the label alignment

The `.ic-steps` grid uses `grid-template-rows: auto auto` with 4 children arranged as:
- Row 1 Col 1: Step 1 label + subsidiary filter buttons
- Row 1 Col 2: Step 2 label
- Row 2 Col 1: Project search input
- Row 2 Col 2: Multiplier/Amount/Clear inputs

When collapsed to 1 column, the order becomes:
1. Step 1 label + filter buttons
2. Step 2 label  ← this will appear between the two controls, which is confusing

To fix: on mobile, re-order to: Step 1 label → Project input → Step 2 label → Controls.
Do this via `order` property in the `@media` block:

```css
@media (max-width: 640px) {
  /* ... existing rules ... */

  /* Re-order the 4 grid children for logical single-column flow */
  .ic-steps > div:nth-child(1) { order: 1; } /* Step 1 label + sub filters */
  .ic-steps > div:nth-child(2) { order: 3; } /* Step 2 label */
  .ic-steps > div:nth-child(3) { order: 2; } /* Project search input */
  .ic-steps > div:nth-child(4) { order: 4; } /* Multiplier + Amount + Clear */
}
```

This produces: Step 1 · Project → [project input] → Step 2 · Incentive level → [controls].

**Verify the children indices are correct** by grepping the `ic-steps` HTML build in
`renderFinanceIncentive`. The 4 direct children of `ic-steps` are built in this order:
`Step 1 label+sub-filter div`, `Step 2 label div`, `project input div`, `multiplier+amount div`.
Confirm before applying `nth-child` selectors.

### E — Commit

```
git add public/tools/incentive.html
git commit -m "feat: mobile responsive layout for incentive calculator"
```

Ask Ryan for one go-ahead to push + trigger Cloud Build deploy.

## QA gate — run as a FRESH, separate session

Use Chrome DevTools mobile emulation (iPhone SE = 375×667) or a real iPhone.

- [ ] **No horizontal scroll** on the main page at 375px width — the table scrolls internally
  but the page body does not scroll sideways.
- [ ] **`.ic-steps` shows single column** at ≤ 640px: Step 1 label → project input →
  Step 2 label → multiplier controls. Confirm the label ordering is logical.
- [ ] **Period bar** fits without overflow at 375px; the period-value dropdown renders and is
  fully tappable.
- [ ] **Project dropdown opens** on tap of the project input on a real or emulated touch device.
  Rows are selectable with a single tap. Multi-select (tapping multiple rows) works.
- [ ] **Selecting a project and viewing results** — the results table appears with horizontal
  scroll when the table is wider than the screen. The scroll container has visible overflow.
- [ ] **Desktop layout unchanged** at ≥ 900px — 2-column `.ic-steps`, table shows without
  scroll wrapper being visible, period bar in one row.
- [ ] **No JS console errors** on load, on project select, or on period switch at either
  viewport size.
- [ ] **Blur/click timing** — on desktop, clicking a row in the dropdown selects the project
  without the dropdown disappearing before selection registers (test: click a row, it should
  toggle, not dismiss with no selection change).

Mark `status: qa-passed` in `STATE.md` only when all boxes are green.
