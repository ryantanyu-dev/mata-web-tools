@echo off
REM Run this once from Windows to init the repo and push to GitHub.
REM Double-click or run from any terminal in C:\dev\Mata-Web-Tools

cd /d C:\dev\Mata-Web-Tools

REM Remove any broken .git from sandbox attempts
if exist .git rmdir /s /q .git

git init -b master
git config user.email "ryan@mata.ph"
git config user.name "Ryan"
git add -A
git commit -m "chore: scaffold Mata Web Tools + Incentive Calculator port"
git remote add origin https://github.com/ryantanyu-dev/mata-web-tools.git
git push -u origin master

echo.
echo Done! Repo pushed to GitHub.
pause
