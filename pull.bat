@echo off
chcp 65001 > nul
cd /d "%~dp0"

echo Подтягивание изменений с GitHub...
git pull --rebase origin main

echo.
if %ERRORLEVEL% equ 0 (
    echo [OK] Локальный репозиторий успешно обновлен!
) else (
    echo [!] Произошла ошибка при обновлении. Проверь статус командой git status.
)

echo.
pause