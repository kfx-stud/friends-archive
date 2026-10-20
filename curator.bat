@echo off
chcp 65001 > nul
title ФК ГазМяс - Куратор
echo.
echo ========================================================
echo   [3/3] Запуск авто-куратора Gemini 3.8 Flash...
echo ========================================================
py scripts\auto_curator.py

pause