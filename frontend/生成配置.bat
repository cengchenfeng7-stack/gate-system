@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"
echo ========================================
echo   生成 mihomo/Clash 四场景配置
echo ========================================
echo.
python gen_client_config.py
echo.
echo ========================================
echo   配置已生成: client.yaml
echo   用 Clash Verge 导入即可
echo ========================================
echo.
pause >nul
