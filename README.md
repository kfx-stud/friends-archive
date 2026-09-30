
<div align="center">

# 📸 GasMyas AI Media Pipeline

**Автономный конвейер курирования, оптимизации и публикации медиа-архива на базе Google Gemini API и GitHub Pages.**[cite: 20]

[![Website](https://img.shields.io/badge/Live-site.gasmyas.me-2ea44f?style=flat-square&logo=google-chrome)](https://site.gasmyas.me)[cite: 20]
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue?style=flat-square&logo=python)](https://python.org)[cite: 20]
[![Model](https://img.shields.io/badge/AI-Gemini%203.8%20Flash-orange?style=flat-square&logo=google)](https://ai.google.dev/)[cite: 20]
[![Hosting](https://img.shields.io/badge/Deploy-GitHub%20Pages-black?style=flat-square&logo=github)](https://pages.github.com/)[cite: 20]

[О проекте](#-о-проекте) • [Архитектура](#-архитектура) • [Структура репозитория](#-структура-репозитория) • [Установка и запуск](#-установка-и-запуск) • [Стек](#-стек-технологий)[cite: 20]

</div>

---

## ⚡ О проекте

Проект решает задачу автоматической селекции и публикации локального архива фотографий компании друзей[cite: 20]. Вместо ручной сортировки сотен кадров из Telegram-чатов и верстки веб-страниц, весь процесс автоматизирован локальным скриптом на Python[cite: 20]:

1. **Анализ и оценка:** Нейросеть Gemini оценивает снимок по 10-балльной шкале юмора/вайба и генерирует метаданные (заголовок, остроумную подпись, тег)[cite: 20].
2. **Фильтрация:** Кадры с оценкой ниже заданного порога отсеиваются[cite: 20].
3. **Дедупликация:** Вычисление хэш-сумм SHA-256 предотвращает повторные отправки и дубликаты[cite: 20].
4. **Оптимизация:** Автоматический ресайз и сжатие исходных тяжелых фото в оптимизированный JPEG[cite: 20].
5. **Публикация:** Автоматическое обновление структуры базы данных `data.json`, создание коммита и `git push` на GitHub Pages[cite: 20].

---

## 🛠 Архитектура пайплайна

```text
[ Исходные фото / Telegram Export ]
                 │
                 ▼
          📁 incoming/
                 │
                 ▼
       auto_curator.py
        ├─ SHA-256 Check ─────────► [Дубликат? Пропуск и удаление]
        ├─ Telegram Thumb Cleanup  ► [Удаление мусорных превью]
        ├─ Multi-Key Gemini API ──► [Оценка (Score), Title, Tag, Caption]
        └─ PIL Image Optimizer ───► Сжатие (1200x1200px, JPEG 85%)
                 │
        ┌────────┴────────┐
        ▼                 ▼
   📁 images/        📄 data.json
 (Одобренные фото)  (База карточек)
        │                 │
        └────────┬────────┘
                 ▼
          Git Auto-Push
                 │
                 ▼
     GitHub Pages Hosting
                 │
                 ▼
       🌐 site.gasmyas.me

```

---

## 📂 Структура репозитория

```text
.
├── incoming/             # Буфер для новых фото (исключен из Git)
├── images/               # Оптимизированные медиафайлы для сайта
├── index.html            # Фронтенд (CSS Grid, Lazy loading, модальное окно)
├── data.json             # База данных карточек и метаданных
├── auto_curator.py       # Основной AI-конвейер обработки
├── run.bat               # Запуск анализатора в один клик
├── push.bat              # Скрипт для ручного коммита и пуша
├── requirements.txt      # Зависимости Python
├── .gitignore            # Защита секретов и исключение временных файлов
└── README.md             # Документация проекта

```

---

## 🚀 Установка и запуск

### 1. Клонирование репозитория

```bash
git clone [https://github.com/kfx-stud/kfx-stud.github.io.git](https://github.com/kfx-stud/kfx-stud.github.io.git)
cd kfx-stud.github.io

```

### 2. Установка зависимостей

```bash
pip install -r requirements.txt

```

### 3. Настройка переменных окружения

Создайте локальный файл `.env` в корне проекта (он защищен через `.gitignore` и не попадет в репозиторий):

```ini
# Можно указать один или несколько ключей через запятую для авторотации при лимитах
GEMINI_API_KEYS=AQ.KeyOne...,AQ.KeyTwo...

```

### 4. Добавление и обработка фото

1. Поместите файлы фотографий в папку `incoming/`.


2. Запустите скрипт через терминал или двойным кликом по `run.bat`:


```bash
python auto_curator.py

```


3. Скрипт обработает изображения, обновит `data.json` и автоматически выполнит пуш на GitHub.


4. Изменения появятся на сайте в течение минуты.



---

## 🧰 Стек технологий

* **Language:** Python 3.10+


* **AI Core:** Google Gemini API (`gemini-3.8-flash`) через REST API с пулом ключей


* **Image Processing:** Pillow (PIL)


* **Frontend:** Vanilla HTML5, Modern CSS Grid, JavaScript (Lazy-load, Fetch API)


* **Deployment & Hosting:** GitHub Pages, Custom DNS via Namecheap CNAME
