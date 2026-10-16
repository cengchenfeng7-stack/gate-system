@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"
echo ========================================
echo   在线拉取最新 VPN Gate feed
echo ========================================
echo.
python vpn_dial.py --dry-run --scene all --refresh
echo.
pause >nul
