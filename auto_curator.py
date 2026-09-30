import os
import sys
import time
import json
import base64
import re
import hashlib
from io import BytesIO
from pathlib import Path
from typing import Optional, Dict, Any, List

import requests
from PIL import Image, ImageOps

# ==========================================
# 1. ПУТИ И КОНФИГУРАЦИЯ
# ==========================================
BASE_DIR = Path(__file__).resolve().parent
INCOMING_DIR = BASE_DIR / "incoming"
PENDING_DIR = BASE_DIR / "pending"       # Промежуточный буфер модерации
IMAGES_DIR = BASE_DIR / "images"         # Финальные фото сайта
DELETED_DIR = BASE_DIR / "deleted"       # Корзина отсеянных фото
DATA_FILE = BASE_DIR / "data.json"
PENDING_FILE = BASE_DIR / "pending.json"
ENV_FILE = BASE_DIR / ".env"

# Порог вайба для попадания в очередь (1-10)
SCORE_THRESHOLD = 5

# Задержка под лимит 5 RPM (1 запрос в 12 секунд)
REQUEST_PACE_DELAY = 12.0

# ==========================================
# 2. ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ И ПРОКСИ
# ==========================================
def load_env() -> Dict[str, str]:
    env_vars = {}
    if ENV_FILE.exists():
        with open(ENV_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env_vars[k.strip()] = v.strip().strip("'\"")
    return env_vars

env = load_env()
GEMINI_MODEL = env.get("GEMINI_MODEL", os.getenv("GEMINI_MODEL", "gemini-3.8-flash"))

HTTP_PROXY = env.get("HTTP_PROXY") or os.getenv("HTTP_PROXY")
HTTPS_PROXY = env.get("HTTPS_PROXY") or os.getenv("HTTPS_PROXY")

session = requests.Session()
if HTTPS_PROXY or HTTP_PROXY:
    proxy_url = HTTPS_PROXY or HTTP_PROXY
    session.proxies = {"http": proxy_url, "https": proxy_url}

# ==========================================
# 3. МЕНЕДЖЕР КЛЮЧЕЙ С КУЛДАУНАМИ
# ==========================================
class GeminiKeyManager:
    def __init__(self, raw_keys_str: str):
        self.keys: List[str] = [k.strip() for k in raw_keys_str.split(",") if k.strip()]
        self.cooldowns: Dict[str, float] = {k: 0.0 for k in self.keys}
        self.dead_keys: set = set()
        self.current_idx = 0

    @property
    def total_count(self) -> int:
        return len(self.keys)

    def print_status(self):
        now = time.time()
        in_service = sum(1 for k in self.keys if k not in self.dead_keys and self.cooldowns[k] <= now)
        on_pause = sum(1 for k in self.keys if k not in self.dead_keys and self.cooldowns[k] > now)
        dead = len(self.dead_keys)
        print(f"[Ключи] В строю: {in_service} | На паузе: {on_pause} (мертвых: {dead})")

    def get_key(self) -> str:
        if not self.keys or len(self.dead_keys) == len(self.keys):
            raise RuntimeError("Все API-ключи исчерпаны или недействительны.")

        while True:
            now = time.time()
            available_keys = [k for k in self.keys if k not in self.dead_keys and self.cooldowns[k] <= now]
            
            if available_keys:
                self.current_idx = (self.current_idx + 1) % len(available_keys)
                return available_keys[self.current_idx]

            active_keys = [k for k in self.keys if k not in self.dead_keys]
            earliest_time = min(self.cooldowns[k] for k in active_keys)
            wait_seconds = max(1.0, earliest_time - now)
            
            print(f"[Ключи] Все ключи на кулдауне. Ожидание {int(wait_seconds) + 1} сек...")
            time.sleep(wait_seconds + 0.5)

    def mark_rate_limited(self, key: str, duration: float = 70.0):
        self.cooldowns[key] = time.time() + duration
        print(f"Превышен лимит (429) для ключа ...{key[-6:]}. Пауза {int(duration)} сек.")
        self.print_status()

    def mark_server_busy(self, key: str, duration: float = 35.0):
        self.cooldowns[key] = time.time() + duration
        print(f"Модель перегружена (503). Смена ключа на {int(duration)} сек.")
        self.print_status()

    def mark_dead(self, key: str):
        self.dead_keys.add(key)
        print(f"[Ключи] Ключ ...{key[-6:]} аннулирован (400/403 Invalid).")
        self.print_status()

# ==========================================
# 4. ОБРАБОТКА ИЗОБРАЖЕНИЙ И УТИЛИТЫ
# ==========================================
def calculate_sha256(filepath: Path) -> str:
    sha = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            sha.update(chunk)
    return sha.hexdigest()

def is_telegram_thumb(filepath: Path) -> bool:
    name = filepath.name.lower()
    if "_thumb" in name or name.startswith("thumb_"):
        return True
    try:
        if filepath.stat().st_size < 15 * 1024:
            with Image.open(filepath) as img:
                w, h = img.size
                if w <= 320 or h <= 320:
                    return True
    except Exception:
        pass
    return False

def parse_date_from_filename(filename: str) -> str:
    match = re.search(r"@(\d{2})-(\d{2})-(\d{4})", filename)
    if match:
        day, month, year = match.groups()
        return f"{year}-{month}-{day}"
    return time.strftime("%Y-%m-%d")

def load_json(filepath: Path) -> list:
    if filepath.exists():
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read().strip()
                return json.loads(content) if content else []
        except Exception:
            return []
    return []

def save_json(filepath: Path, data: list):
    temp_file = filepath.with_suffix(".tmp")
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    temp_file.replace(filepath)

def prepare_image_for_gemini(filepath: Path) -> str:
    """Легковесное превью (до 750px, quality=70), чтобы не рвать прокси-сокеты."""
    with Image.open(filepath) as img:
        img = ImageOps.exif_transpose(img)
        img.thumbnail((750, 750), Image.Resampling.LANCZOS)
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        buffer = BytesIO()
        img.save(buffer, format="JPEG", quality=70)
        return base64.b64encode(buffer.getvalue()).decode("utf-8")

def optimize_pending_image(src_path: Path, dest_path: Path):
    """Сжатие фото в буферную папку pending/ (1200x1200px, JPEG 85%)."""
    with Image.open(src_path) as img:
        img = ImageOps.exif_transpose(img)
        img.thumbnail((1200, 1200), Image.Resampling.LANCZOS)
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        img.save(dest_path, format="JPEG", quality=85, optimize=True)

# ==========================================
# 5. ВЫЗОВ GEMINI API
# ==========================================
PROMPT = (
    "Ты — остроумный куратор и хроникёр медиа-архива тусовки 'ГазМяс'. "
    "Проанализируй фото и оцени его по 10-балльной шкале вайба, угара, душевности или абсурда (score от 1 до 10). "
    "Придумай короткий ироничный заголовок (title), мемную подпись (caption) и один емкий тег (tag: Основа, Дибуны, Sugar Rush, Карл Фридрих, Гараж, Архив и т.д.). "
    "Ответ выдай СТРОГО в формате JSON:\n"
    "{\n"
    '  "score": 8,\n'
    '  "title": "Заголовок",\n'
    '  "caption": "Подпись к фото",\n'
    '  "tag": "Вайб"\n'
    "}"
)

def query_gemini(image_b64: str, key_manager: GeminiKeyManager, max_retries: int = 8) -> Optional[Dict[str, Any]]:
    attempt = 0
    backoff = 5.0

    while attempt < max_retries:
        attempt += 1
        api_key = key_manager.get_key()
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={api_key}"
        
        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": PROMPT},
                        {
                            "inline_data": {
                                "mime_type": "image/jpeg",
                                "data": image_b64
                            }
                        }
                    ]
                }
            ],
            "generationConfig": {
                "temperature": 0.4,
                "response_mime_type": "application/json"
            }
        }

        try:
            resp = session.post(url, json=payload, timeout=30)
            
            if resp.status_code == 200:
                data = resp.json()
                text_response = data["candidates"][0]["content"]["parts"][0]["text"]
                clean_json = re.sub(r"^```(?:json)?\s*|\s*```$", "", text_response.strip(), flags=re.MULTILINE)
                return json.loads(clean_json)

            if resp.status_code == 429:
                key_manager.mark_rate_limited(api_key, duration=70.0)
                time.sleep(3.0)
                continue

            if resp.status_code == 503:
                print(f"Сервер занят (503). Пауза {int(backoff)} сек... (попытка {attempt}/{max_retries})")
                time.sleep(backoff)
                backoff = min(backoff * 1.5, 30.0)
                if attempt >= 2:
                    key_manager.mark_server_busy(api_key, duration=35.0)
                continue

            if resp.status_code in (400, 403):
                key_manager.mark_dead(api_key)
                continue

            resp.raise_for_status()

        except (requests.exceptions.SSLError, requests.exceptions.ConnectionError):
            print(f"Сетевой сбой. Повтор через 5 сек... (попытка {attempt}/{max_retries})")
            time.sleep(5)
            continue
        except requests.exceptions.Timeout:
            print("Таймаут сокета к Gemini. Ожидание 5 сек...")
            time.sleep(5)
            continue
        except Exception as e:
            print(f"Непредвиденная ошибка: {e}. Пауза 4 сек...")
            time.sleep(4)
            continue

    return None

# ==========================================
# 6. ГЛАВНЫЙ ЦИКЛ
# ==========================================
def main():
    print("========================================================")
    print("  [3/3] Запуск авто-куратора Gemini...")
    print("========================================================")
    
    raw_keys = (
        env.get("GEMINI_API_KEYS") 
        or env.get("GEMINI_API_KEY") 
        or os.getenv("GEMINI_API_KEYS") 
        or os.getenv("GEMINI_API_KEY") 
        or ""
    )
    if not raw_keys:
        print("Ошибка: в .env не найдены GEMINI_API_KEYS!")
        sys.exit(1)

    key_manager = GeminiKeyManager(raw_keys)
    print(f"Используемая модель: {GEMINI_MODEL}")
    print(f"Загружено ключей: {key_manager.total_count}")
    key_manager.print_status()

    for d in (INCOMING_DIR, PENDING_DIR, IMAGES_DIR, DELETED_DIR):
        d.mkdir(parents=True, exist_ok=True)

    data_items = load_json(DATA_FILE)
    pending_items = load_json(PENDING_FILE)

    known_hashes = {item.get("sha256") for item in data_items if "sha256" in item}
    known_hashes.update({item.get("sha256") for item in pending_items if "sha256" in item})

    all_incoming = sorted(
        [f for f in INCOMING_DIR.iterdir() if f.is_file() and f.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")],
        key=lambda x: x.name
    )

    print(f"Кадров на просмотр: {len(all_incoming)}")
    print(f"Уже на сайте (data.json): {len(data_items)} | В очереди админки (pending.json): {len(pending_items)}\n")

    for idx, filepath in enumerate(all_incoming, start=1):
        filename = filepath.name
        print(f"[{idx}/{len(all_incoming)}] Проверка кадра: {filename}")

        if is_telegram_thumb(filepath):
            print("-> Превью Telegram. Удаление...")
            filepath.unlink(missing_ok=True)
            continue

        file_hash = calculate_sha256(filepath)
        if file_hash in known_hashes:
            print("-> Дубликат (уже есть в базе/очереди). Удаление из incoming...")
            filepath.unlink(missing_ok=True)
            continue

        try:
            image_b64 = prepare_image_for_gemini(filepath)
        except Exception as e:
            print(f"-> Ошибка чтения фото: {e}. Перенос в deleted...")
            filepath.rename(DELETED_DIR / filename)
            continue

        ai_result = query_gemini(image_b64, key_manager)
        
        if not ai_result:
            print("-> Не удалось получить ответ Gemini. Кадр оставлен в incoming.")
            continue

        score = ai_result.get("score", 0)
        title = ai_result.get("title", "Без названия")
        caption = ai_result.get("caption", "")
        tag = ai_result.get("tag", "Архив")

        if score < SCORE_THRESHOLD:
            print(f"✗ Отклонено AI (Score: {score}/{10}) -> перенос в deleted/")
            dest_del = DELETED_DIR / filename
            if dest_del.exists():
                dest_del.unlink()
            filepath.rename(dest_del)
        else:
            print(f"✓ Одобрено AI (Score: {score}/{10}): «{title}» [{tag}] -> отправка в pending/")
            
            dest_pending = PENDING_DIR / filename
            optimize_pending_image(filepath, dest_pending)
            filepath.unlink(missing_ok=True)

            # Формируем карточку, полностью совместимую и с admin.html, и с index.html
            card = {
                "id": file_hash[:12],
                "filename": filename,
                "image": f"pending/{filename}",
                "file": f"pending/{filename}",
                "title": title,
                "caption": caption,
                "tag": tag,
                "tag_class": "alt" if len(pending_items) % 2 == 0 else "",
                "score": score,
                "date": parse_date_from_filename(filename),
                "sha256": file_hash,
                "timestamp": int(time.time())
            }

            pending_items.append(card)
            save_json(PENDING_FILE, pending_items)
            known_hashes.add(file_hash)

        time.sleep(REQUEST_PACE_DELAY)

    print("\nОбработка входящих файлов завершена.")

if __name__ == "__main__":
    main()