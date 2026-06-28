# Mata Web Tools

Department-specific tools for the Mata group — Firebase-hosted, same `panso-ph` project.

- **Hosting site:** `mata-tools` → `mata-tools.web.app` (future: `tools.mata.ph`)
- **Cloud Run service:** `mata-tools-api` (Python, read-only GCS mount)
- **Auth:** Shared Firebase Auth Google sign-in with Panso; scope grants in Firestore

## Tools

| # | Tool | Dept | Status |
|---|------|------|--------|
| 1 | MG-Finance Incentive Calculator | MG - Finance | ✅ Live |
| 2 | MG-HR Calendar | MG - HR | Coming soon |
| 3 | MG-HR Interns | MG - HR | Coming soon |
| 4 | MG-HR Pay Matrix | MG - HR | Coming soon |
| 5 | MT-Ops Workload Matrix | MT - Ops | Moved to Panso (task 16) |
| 6 | Agenda Trail | All | Coming soon |
| 7 | Dept Documents | All | Coming soon |

## Local dev

```bash
cd app
PANSO_SKIP_AUTH=1 PANSO_ROOT=C:\dev\Panso-Local python tools_api.py
# Open http://localhost:5056
```

## Deploy

See `DEPLOY.md` for Cloud Run + Firebase Hosting deploy steps.
