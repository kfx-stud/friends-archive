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

# Задержка между проверками: 5 секунд
REQUEST_PACE_DELAY = 5.0

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
# 3. МЕНЕДЖЕР КЛЮЧЕЙ С КУЛДАУНАМИ И СЧЁТЧИКОМ
# ==========================================
class GeminiKeyManager:
    def __init__(self, raw_keys_str: str, max_uses_per_key: int = 10, timeout_duration: float = 300.0):
        self.keys: List[str] = [k.strip() for k in raw_keys_str.split(",") if k.strip()]
        self.cooldowns: Dict[str, float] = {k: 0.0 for k in self.keys}
        self.usage_counts: Dict[str, int] = {k: 0 for k in self.keys}
        self.dead_keys: set = set()
        self.current_idx = 0
        self.max_uses = max_uses_per_key
        self.timeout_duration = timeout_duration

    @property
    def total_count(self) -> int:
        return len(self.keys)

    def print_status(self):
        now = time.time()
        in_service = sum(1 for k in self.keys if k not in self.dead_keys and self.cooldowns[k] <= now)
        on_pause = sum(1 for k in self.keys if k not in self.dead_keys and self.cooldowns[k] > now)
        dead = len(self.dead_keys)
        print(f"[Ключи] В строю: {in_service} | На паузе: {on_pause} (мертвых/заблокированных: {dead})")

    def get_key(self) -> str:
        """Берёт текущий активный ключ или переключает на следующий при паузе."""
        if not self.keys or len(self.dead_keys) == len(self.keys):
            raise RuntimeError("Все API-ключи исчерпаны, заблокированы или недействительны.")

        while True:
            now = time.time()
            for offset in range(len(self.keys)):
                idx = (self.current_idx + offset) % len(self.keys)
                k = self.keys[idx]
                if k not in self.dead_keys and self.cooldowns[k] <= now:
                    self.current_idx = idx
                    return k

            active_keys = [k for k in self.keys if k not in self.dead_keys]
            earliest_time = min(self.cooldowns[k] for k in active_keys)
            wait_seconds = max(1.0, earliest_time - now)
            print(f"[Ключи] Все ключи на таймауте. Ожидание {int(wait_seconds) + 1} сек...")
            time.sleep(wait_seconds + 0.5)

    def record_success(self, key: str):
        """Считает успешные запросы. При достижении 10 раз отправляет ключ на таймаут 300 сек."""
        self.usage_counts[key] = self.usage_counts.get(key, 0) + 1
        count = self.usage_counts[key]
        print(f"[Ключи] Ключ ...{key[-6:]} отработал {count}/{self.max_uses} раз.")

        if count >= self.max_uses:
            self.usage_counts[key] = 0
            self.cooldowns[key] = time.time() + self.timeout_duration
            print(f"[Ключи] Ключ ...{key[-6:]} завершил серию из {self.max_uses} проверок. Таймаут на {int(self.timeout_duration)} сек.")
            self.current_idx = (self.current_idx + 1) % len(self.keys)
            self.print_status()

    def mark_rate_limited(self, key: str, duration: float = 300.0):
        """Обработка превышения лимитов (429) — уход в таймаут на 300 сек и смена ключа."""
        self.usage_counts[key] = 0
        self.cooldowns[key] = time.time() + duration
        print(f"[Ключи] Превышен лимит (429) для ключа ...{key[-6:]}. Таймаут {int(duration)} сек.")
        self.current_idx = (self.current_idx + 1) % len(self.keys)
        self.print_status()

    def mark_server_busy(self, key: str, duration: float = 30.0):
        """Временный сбой сервера Gemini (503)."""
        self.cooldowns[key] = time.time() + duration
        self.current_idx = (self.current_idx + 1) % len(self.keys)
        print(f"Модель перегружена (503). Смена ключа на {int(duration)} сек.")
        self.print_status()

    def mark_dead(self, key: str):
        """Полная блокировка недействительного ключа (400/403) и переход к следующему."""
        self.dead_keys.add(key)
        self.usage_counts[key] = 0
        print(f"[Ключи] Ключ ...{key[-6:]} заблокирован насовсем (400/403 Invalid).")
        self.current_idx = (self.current_idx + 1) % len(self.keys)
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
    """Легковесное превью (до 750px, quality=70), чтобы не рвать сокеты."""
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
                key_manager.record_success(api_key)
                data = resp.json()
                text_response = data["candidates"][0]["content"]["parts"][0]["text"]
                clean_json = re.sub(r"^```(?:json)?\s*|\s*