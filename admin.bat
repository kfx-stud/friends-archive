@echo off
chcp 65001 > nul
title ФК ГазМяс - Сервер Админки

echo ========================================================
echo   Запуск сервера модерации...
echo ========================================================

start http://localhost:8080/admin.html

py admin_server.py

pause