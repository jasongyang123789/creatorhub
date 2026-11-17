@echo off
rem 工作台守护: 8001不在(崩溃/重启后)自动拉起, 15秒巡检
:loop
netstat -ano | findstr /C:":8001" | findstr "LISTENING" >nul
if errorlevel 1 (
  cd /d "G:\VerifiedRepos\creatorhub"
  ".venv\Scripts\python.exe" creatorhub.py --port 8001 --no-open
)
timeout /t 15 /nobreak >nul
goto loop
