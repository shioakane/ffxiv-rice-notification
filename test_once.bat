@echo off
chcp 65001 >nul
cd /d "D:\GAME\rice"
echo ========================================
echo   FF14 绝欧米茄招募 - 单次查询测试
echo ========================================
echo.
python test_api.py
pause
