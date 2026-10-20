# Changelog

Все важные изменения проекта документируются в этом файле.

## [1.3.0] - 2026-10-20

### Added
- Системный промпт AI-куратора вынесен в отдельный локальный файл `prompt.txt` (включен в `.gitignore`).
- Добавлен публичный шаблон `prompt.example.txt` для репозитория на GitHub без персональных замечаний и локальных шуток.
- В `auto_curator.py` реализована функция `load_prompt()` с поддержкой переменной `CURATOR_PROMPT_FILE` и автоматическим фолбэком на шаблон.
- Добавлен автотест `test_prompt_configuration` в `tests/test_data_integrity.py` для контроля валидности шаблона промпта и отсутствия приватных замечаний.

### Changed
- В `.gitignore` добавлены правила исключения персональных промптов `prompt.txt` при сохранении отслеживания `prompt.example.txt`.
- Актуализированы `.env.example` и `README.md` с описанием настройки и кастомизации промпта.

## [1.2.0] - 2026-10-20

### Added
- Добавлена поддержка SOCKS5/SOCKS5h проксирования в авто-куратор (`auto_curator.py`) через зависимость `PySocks`.
- Документирована маршрутизация доменов Google Gemini API и настройка прокси для клиентов Happ, Sing-box и Clash в `README.md` и `.env.example`.

### Changed
- Обновлен `requirements.txt` с добавлением `PySocks>=1.7.1`.
- Скорректирован фокус кадрирования карточки в `data/data.json`.

## [1.1.0] - 2026-10-06

### Changed
- Обновлен и усилен `.gitignore` для исключения секретов (`.env`, `data/config.json`) и больших архивов (`*.7z`).
- Добавлен безопасный шаблон переменных окружения `.env.example`.
- Добавлены открытые стандарты репозитория: `LICENSE` (MIT), `CONTRIBUTING.md`, `CHANGELOG.md`.
- Добавлен CI-пайплайн GitHub Actions (`.github/workflows/ci.yml`) и автоматические тесты `tests/test_data_integrity.py`.
- Актуализирован `README.md`.
- Синхронизирована конфигурация AI-модели авто-куратора на `Gemini 3.8 Flash` в `README.md`, `.env.example`, bat-файлах и добавлен автотест.
