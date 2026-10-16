@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"
echo ========================================
echo   测试模式（不真正拨号）
echo ========================================
echo.
python vpn_dial.py --dry-run --scene support --ignore-data
echo.
pause >nul
