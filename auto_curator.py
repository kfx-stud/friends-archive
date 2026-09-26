import os
import sys
import json
import time
import base64
import requests
from PIL import Image

INCOMING_DIR = "incoming"
IMAGES_DIR = "images"
DATA_FILE = "data.json"
KEYS_FILE = "keys.txt"

# Порог прохождения кадра в архив (от 1 до 10)
MIN_SCORE = 7

os.makedirs(INCOMING_DIR, exist_ok=True)
os.makedirs(IMAGES_DIR, exist_ok=True)


class KeyManager:
    def __init__(self, api_keys):
        self.api_keys = [k.strip() for k in api_keys if k.strip()]
        self.current_index = 0
        self.cooldowns = {k: 0.0 for k in self.api_keys}
        self.dead_keys = set()

    def get_stats(self):
        now = time.time()
        dead_cnt = len(self.dead_keys)
        cooling_cnt = sum(1 for k in self.api_keys if k not in self.dead_keys and self.cooldowns[k] > now)
        active_cnt = len(self.api_keys) - dead_cnt - cooling_cnt
        inactive_cnt = dead_cnt + cooling_cnt
        return active_cnt, inactive_cnt, dead_cnt

    def print_status(self):
        active, inactive, dead = self.get_stats()
        print(f"[Ключи] В строю: {active} | На паузе/отключено: {inactive} (мертвых: {dead})")

    def get_current_key(self):
        if not self.api_keys or len(self.dead_keys) == len(self.api_keys):
            return None
        return self.api_keys[self.current_index]

    def mark_cooldown(self, seconds=60):
        key = self.get_current_key()
        if key:
            self.cooldowns[key] = time.time() + seconds
        self.switch_to_next()

    def mark_dead(self):
        key = self.get_current_key()
        if key:
            self.dead_keys.add(key)
            print(f"[!] Ключ ...{key[-6:]} исключен навсегда.")
        self.switch_to_next()

    def switch_to_next(self):
        if len(self.dead_keys) == len(self.api_keys):
            return None

        now = time.time()
        for _ in range(len(self.api_keys)):
            self.current_index = (self.current_index + 1) % len(self.api_keys)
            cand = self.api_keys[self.current_index]
            if cand not in self.dead_keys and self.cooldowns[cand] <= now:
                self.print_status()
                return cand

        alive_keys = [k for k in self.api_keys if k not in self.dead_keys]
        if not alive_keys:
            return None

        min_wait_key = min(alive_keys, key=lambda k: self.cooldowns[k])
        wait_sec = max(1.0, self.cooldowns[min_wait_key] - now)
        self.current_index = self.api_keys.index(min_wait_key)

        self.print_status()
        print(f"Все доступные ключи исчерпали лимит. Ожидание {int(wait_sec)} сек...")
        time.sleep(wait_sec)
        return self.api_keys[self.current_index]


def load_keys():
    if not os.path.exists(KEYS_FILE):
        print(f"Файл {KEYS_FILE} не найден.")
        sys.exit(1)
    with open(KEYS_FILE, "r", encoding="utf-8") as f:
        keys = [line.strip() for line in f if line.strip() and not line.startswith("#")]
    if not keys:
        print(f"Файл {KEYS_FILE} пуст.")
        sys.exit(1)
    return keys


def calculate_dhash(image_path, hash_size=8):
    try:
        with Image.open(image_path) as img:
            img_gray = img.convert("L").resize((hash_size + 1, hash_size), Image.Resampling.LANCZOS)
            pixels = list(img_gray.get_flattened_data())
            diff = []
            for row in range(hash_size):
                for col in range(hash_size):
                    pixel_left = pixels[row * (hash_size + 1) + col]
                    pixel_right = pixels[row * (hash_size + 1) + col + 1]
                    diff.append(pixel_left > pixel_right)
            return sum([2 ** i for (i, v) in enumerate(diff) if v])
    except Exception:
        return None


def analyze_image_with_gemini(image_path, key_manager):
    system_prompt = (
        "Ты беспощадный арт-директор и архивариус дворового футбольного клуба 'ФК ГазМяс'. "
        "Твоя задача — объективно оценить историческую и мемную ценность кадра и написать едкое описание.\n\n"
        "ШКАЛА ОЦЕНКИ SCORE (СТРОГО 1-10, НЕ ЗАВЫШАЙ БАЛЛЫ ИЗ ВЕЖЛИВОСТИ!):\n"
        "- 1-4 балла (мусор/проходняк): смазанные пальцы, пустые стены, скучные бытовые фото без действия, рандомные скриншоты переписок, некрасивые размытые лица.\n"
        "- 5-6 баллов (посредственно): обычное селфи, стандартная посиделка, ничего легендарного или смешного.\n"
        "- 7-8 баллов (хороший контент): клубная атмосфера, яркие эмоции, алкоголь, забавные позы, узнаваемые лица ГазМяса.\n"
        "- 9-10 баллов (золотой фонд/шедевр): эпический завоз, исторический момент, угар, идеальный мем.\n\n"
        "Главные темы клуба для панчлайнов и тегов:\n"
        "1. Дибуны ('нихуя Дибуны отстроили', дача, станция, природа).\n"
        "2. Лудка, бонуски, бурмалда, Sugar Rush, Sweet Bonanza, '99% лудоманов останавливаются за шаг до победы'.\n"
        "3. Реальные пацаны (Базанов, Колян, Вован, Эдик, 'Базанов дал джазу').\n"
        "4. Гимн/строевая: 'Солнышко светит, курочка клюет по зернышку по зернышку, а служба все идет...'.\n"
        "5. Локации и лица: Гараж на Гороховой, Франк на Сенной, Кресты (Карл Фридрих), Студос; Директор Платон (Первый и Единственный), Менеджер Артем Визиров, Тренер Иван Плыгун, kfx.\n\n"
        "Верни ответ СТРОГО в формате JSON без markdown (без ```json):\n"
        '{"score": 7, "title": "Заголовок (3-5 слов)", "caption": "Панчлайн и описание (1-2 предложения)", "tag": "Тег (Дибуны, Лудка, Бонуска, Основа, Тренер, Дирекция, Легенда)"}'
    )

    with open(image_path, "rb") as f:
        image_bytes = f.read()
    b64_image = base64.b64encode(image_bytes).decode("utf-8")

    payload = {
        "contents": [{
            "parts": [
                {"text": system_prompt},
                {
                    "inline_data": {
                        "mime_type": "image/jpeg",
                        "data": b64_image
                    }
                }
            ]
        }],
        "generationConfig": {
            "temperature": 0.3,
            "response_mime_type": "application/json"
        }
    }

    headers = {"Content-Type": "application/json"}
    errors_503_count = 0

    while True:
        api_key = key_manager.get_current_key()
        if not api_key:
            print("[X] Все доступные API-ключи исчерпаны.")
            return None

        url = f"[https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key=](https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key=){api_key}"

        try:
            res = requests.post(url, headers=headers, json=payload, timeout=30)

            if res.status_code == 200:
                errors_503_count = 0
                data = res.json()
                text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
                if text.startswith("