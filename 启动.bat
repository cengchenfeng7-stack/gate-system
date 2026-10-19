@echo off
setlocal EnableDelayedExpansion
title 智能 Agent 群
cd /d "%~dp0"

echo ==================================================
echo            智能 Agent 群 · 一键启动
echo ==================================================
echo.

rem ---------- 初始化配置 ----------
if not exist ".env" (
  if exist ".env.example" copy /y ".env.example" ".env" >nul
  echo [初始化] 已创建 .env 配置文件，可编辑它修改模型与端口
  echo.
)

if not exist "logs" mkdir logs
if not exist "data" mkdir data

rem ---------- 读取 .env 配置 ----------
set PORT=3000
for /f "tokens=2 delims==" %%a in ('findstr /B "PORT=" .env 2^>nul') do set PORT=%%a
set BIND_HOST=127.0.0.1
for /f "tokens=2 delims==" %%a in ('findstr /B "BIND_HOST=" .env 2^>nul') do set BIND_HOST=%%a
for /f "tokens=* delims= " %%b in ("!PORT!") do set PORT=%%b
for /f "tokens=* delims= " %%b in ("!BIND_HOST!") do set BIND_HOST=%%b
if "!PORT!"=="" set PORT=3000
if "!BIND_HOST!"=="" set BIND_HOST=127.0.0.1

rem ---------- 检查重复启动 ----------
if exist "logs\server.pid" (
  set /p OLD_PID=<"logs\server.pid"
  tasklist /FI "PID eq !OLD_PID!" 2>nul | find "!OLD_PID!" >nul
  if not errorlevel 1 (
    echo [提示] 服务已在运行中 ^(PID: !OLD_PID!^)
    start "" "http://localhost:!PORT!"
    pause
    exit /b 0
  )
  del "logs\server.pid" >nul 2>&1
)

rem ---------- 端口占用检查 ----------
netstat -ano | findstr ":%PORT% " | findstr "LISTENING" >nul
if not errorlevel 1 (
  echo [错误] 端口 !PORT! 已被占用！
  echo 请关闭占用该端口的程序，或编辑 .env 里的 PORT 后重试。
  echo.
  pause
  exit /b 1
)

rem ---------- 启动服务（日志由服务自身追加写入 logs\server.log） ----------
echo [启动] 正在启动服务，端口 !PORT! ...
powershell -NoProfile -Command "$p = Start-Process -FilePath 'runtime\node.exe' -ArgumentList 'app\server.mjs' -WorkingDirectory '%CD%' -PassThru -WindowStyle Hidden; $p.Id | Out-File -FilePath 'logs\server.pid' -Encoding ascii"

if not exist "logs\server.pid" (
  echo [错误] 启动失败，请查看 logs\server.err.log
  echo.
  pause
  exit /b 1
)

rem ---------- 等待服务就绪 ----------
echo [等待] 等待服务响应...
set /a RETRY=0
:wait_loop
  ping -n 2 127.0.0.1 >nul
curl -s -o nul -w "%%{http_code}" "http://127.0.0.1:!PORT!/api/health" 2>nul | find "200" >nul
if not errorlevel 1 goto ready
set /a RETRY+=1
if !RETRY! GEQ 30 (
  echo [警告] 服务响应超时，可手动访问 http://localhost:!PORT!
  goto open_browser
)
goto wait_loop

:ready
echo [成功] 服务已启动！

:open_browser
echo.
echo --------------------------------------------------
echo   访问地址: http://localhost:!PORT!
if not "!BIND_HOST!"=="127.0.0.1" (
  echo   绑定地址: !BIND_HOST!  ^(其他设备可用 http://本机IP:!PORT! 访问^)
  echo   注意: 对外开放时管理后台 /admin 需要输入 ADMIN_PASSWORD
)
echo   管理后台: http://localhost:!PORT!/admin
echo   停止服务: 双击 "停止.bat"
echo   运行日志: logs\server.log
echo --------------------------------------------------
start "" "http://localhost:!PORT!"
echo.
echo 此窗口可以关闭，服务会继续在后台运行。
pause
