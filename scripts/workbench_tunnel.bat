@echo off
rem 开机自启: 把本机工作台(8001)挂到服务器(反向隧道), 公网经 https://82.156.230.223 访问
:loop
ssh -o BatchMode=yes -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -N -R 9001:127.0.0.1:8001 ubuntu@82.156.230.223
timeout /t 10 /nobreak >nul
goto loop
