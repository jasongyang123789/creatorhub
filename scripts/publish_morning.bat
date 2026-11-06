@echo off
rem 生成晨报面板并发布到服务器(公网: http://82.156.230.223/morning/)
setlocal
cd /d "%~dp0.."
set PYTHONUTF8=1
".venv\Scripts\python.exe" -m app.morning_panel --out out\morning-panel --days 7 || exit /b 1
scp -o BatchMode=yes out\morning-panel\index.html out\morning-panel\data.json ubuntu@82.156.230.223:/tmp/ || exit /b 1
ssh -o BatchMode=yes ubuntu@82.156.230.223 "sudo cp /tmp/index.html /tmp/data.json /var/www/morning/ && sudo chown ubuntu:ubuntu /var/www/morning/* && echo DEPLOYED" || exit /b 1
echo.
echo 已发布: http://82.156.230.223/morning/
endlocal
