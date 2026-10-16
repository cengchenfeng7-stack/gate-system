@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"
echo ========================================
echo   VPN Gate 拨号器 - 客服稳连场景
echo ========================================
echo.
python vpn_dial.py --scene support --ignore-data
echo.
echo ========================================
echo   按任意键关闭...
pause >nul
