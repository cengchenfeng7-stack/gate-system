@echo off
title 停止 智能 Agent 群
cd /d "%~dp0"

if not exist "logs\server.pid" (
  echo [提示] 未找到运行中的服务^(没有 logs\server.pid 文件^)
  echo.
  pause
  exit /b 0
)

set /p PID=<"logs\server.pid"
echo 正在停止服务 ^(PID: %PID%^) ...

tasklist /FI "PID eq %PID%" 2>nul | find "%PID%" >nul
if errorlevel 1 (
  echo [提示] 进程 %PID% 已不存在
) else (
  rem ---------- 先尝试优雅退出，再强制结束 ----------
  rem 发送 Ctrl+Break 让 Node 有机会 checkpoint 数据库
  taskkill /PID %PID% >nul 2>&1
  set /a WAIT=0
  :wait_stop
  timeout /t 1 /nobreak >nul
  tasklist /FI "PID eq %PID%" 2>nul | find "%PID%" >nul
  if errorlevel 1 goto stopped
  set /a WAIT+=1
  if !WAIT! GEQ 5 taskkill /PID %PID% /F /T >nul 2>&1
  if !WAIT! GEQ 8 goto stopped
  goto wait_stop
)

:stopped
tasklist /FI "PID eq %PID%" 2>nul | find "%PID%" >nul
if errorlevel 1 (
  echo [成功] 服务已停止，数据库已写回 data\chat.db
) else (
  echo [警告] 停止失败，请到任务管理器手动结束 node.exe
)

del "logs\server.pid" >nul 2>&1
echo.
pause
