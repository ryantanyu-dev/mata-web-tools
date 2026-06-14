# do-push.ps1 — Option B push for Mata Web Tools
# Run from C:\dev\Mata-Web-Tools in PowerShell
# Pre-authorized by Ryan (option B: normal push, no history rewrite)

Set-Location "C:\dev\Mata-Web-Tools"

Write-Host "`n=== Git status ===" -ForegroundColor Cyan
git status

Write-Host "`n=== Staging cloudbuild.yaml ===" -ForegroundColor Cyan
git add cloudbuild.yaml

Write-Host "`n=== Staging restored files (if modified) ===" -ForegroundColor Cyan
git add app/tools_api.py public/tools/incentive.html

Write-Host "`n=== Committing ===" -ForegroundColor Cyan
git commit -m "fix: replace hardcoded Firebase API key with Cloud Build substitution variable"

Write-Host "`n=== Pushing to origin ===" -ForegroundColor Cyan
git push origin master

Write-Host "`n=== Done. Check output above for push protection bypass if needed. ===" -ForegroundColor Green
