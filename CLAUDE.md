# CLAUDE.md — Mata Web Tools

## Conventions (mirror Panso)

- **One session per task.** Commit after each working step. Never hold uncommitted changes.
- **Grep-then-slice.** Never read `panso_local.py` or `index.html` whole. Grep for line anchors, read 40–80 line slices.
- **No monolith.** Each tool is a self-contained `public/tools/<name>.html` page (<500 lines). No shared JS bundle.
- **Never touch `C:\dev\Panso-Local`** until Phase C (redirect) is explicitly started.
- **Verbatim formula ports.** Never re-derive incentive/pay formulas. Copy exact code paths from Panso.
- **Context budget.** Run on Sonnet standard 200k. Never load CSVs or large files into context.

## Architecture

```
public/
  index.html          ← launcher (sign-in + 7-tool grid)
  tools/
    incentive.html    ← MG-Finance Incentive Calculator
    ...               ← future tools, one file each
app/
  tools_api.py        ← Python ThreadingHTTPServer, <800 lines
  access_layer.py     ← copied from Panso verbatim (do NOT edit; re-copy when Panso updates)
```

## Key paths (Cloud Run)

- `PANSO_ROOT=/data` (GCS FUSE mount, read-only)
- `PANSO_SKIP_AUTH=1` bypasses auth on localhost only (ignored when PANSO_ROOT=/data)

## Porting checklist (per tool)

1. Grep Panso for backend handler + frontend renderer line anchors
2. Read only those slices (never the whole file)
3. Copy handler into `tools_api.py`; copy renderer into `public/tools/<name>.html`
4. Generate synthetic test data in bash; verify formula on 2–3 known inputs
5. Commit, deploy, QA side-by-side vs live Panso (same period, same totals)
