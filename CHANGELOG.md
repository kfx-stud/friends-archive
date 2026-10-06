# Changelog

Все важные изменения проекта документируются в этом файле.

## [1.1.0] - 2026-10-06

### Changed
- Обновлен и усилен `.gitignore` для исключения секретов (`.env`, `data/config.json`) и больших архивов (`*.7z`).
- Добавлен безопасный шаблон переменных окружения `.env.example`.
- Добавлены открытые стандарты репозитория: `LICENSE` (MIT), `CONTRIBUTING.md`, `CHANGELOG.md`.
- Добавлен CI-пайплайн GitHub Actions (`.github/workflows/ci.yml`) и автоматические тесты `tests/test_data_integrity.py`.
- Актуализирован `README.md`.
- Синхронизирована конфигурация AI-модели авто-куратора на `Gemini 3.8 Flash` в `README.md`, `.env.example`, bat-файлах и добавлен автотест.
