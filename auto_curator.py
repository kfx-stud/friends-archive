import os
import sys
import json
import time
import re
import io
import shutil
import base64
from difflib import SequenceMatcher
import requests
from PIL import Image

INCOMING_DIR = "incoming"
PENDING_DIR = "pending_images"
DELETED_DIR = "deleted"
DATA_FILE = "data.json"
PENDING_FILE = "pending.json"
KEYS_FILE = "keys.txt"
ENV_FILE = ".env"

MIN_SCORE = 7

TITLE_SIMILARITY_THRESHOLD = 0.70
CAPTION_SIMILARITY_THRESHOLD = 0.72

os.makedirs(INCOMING_DIR, exist_ok=True)
os.makedirs(PENDING_DIR, exist_ok=True)
os.makedirs(DELETED_DIR, exist_ok=True)


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
    keys = []
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    k, v = line.split("=", 1)
                    k = k.strip().upper()
                    v = v.strip().strip('"').strip("'")
                    if any(sub in k for sub in ("GEMINI", "API_KEY", "KEY")):
                        for sub_key in v.split(","):
                            sub_key = sub_key.strip()
                            if sub_key and sub_key not in keys:
                                keys.append(sub_key)

    if not keys and os.path.exists(KEYS_FILE):
        with open(KEYS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and line not in keys:
                    keys.append(line)

    if not keys:
        print("Ошибка: API-ключи не найдены ни в .env, ни в keys.txt.")
        sys.exit(1)

    return keys


def move_to_deleted(src_path):
    filename = os.path.basename(src_path)
    target_path = os.path.join(DELETED_DIR, filename)

    if os.path.exists(target_path):
        base, ext = os.path.splitext(filename)
        target_path = os.path.join(DELETED_DIR, f"{base}_{int(time.time())}{ext}")

    try:
        shutil.move(src_path, target_path)
    except Exception as e:
        print(f"[!] Ошибка перемещения в {DELETED_DIR}: {e}")
        if os.path.exists(src_path):
            os.remove(src_path)


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


def clean_text_for_compare(text):
    if not text:
        return ""
    text = text.lower()
    return re.sub(r"[^\w\sа-яёa-z0-9]", "", text).strip()


def is_duplicate_text(new_title, new_caption, existing_items):
    c_new_title = clean_text_for_compare(new_title)
    c_new_caption = clean_text_for_compare(new_caption)

    for item in existing_items:
        c_item_title = clean_text_for_compare(item.get("title", ""))
        c_item_caption = clean_text_for_compare(item.get("caption", ""))

        if c_new_title and c_item_title:
            ratio_title = SequenceMatcher(None, c_new_title, c_item_title).ratio()
            if ratio_title >= TITLE_SIMILARITY_THRESHOLD:
                return True, f"Схожее название ({int(ratio_title * 100)}%): '{item.get('title')}'"

        if c_new_caption and c_item_caption:
            ratio_caption = SequenceMatcher(None, c_new_caption, c_item_caption).ratio()
            if ratio_caption >= CAPTION_SIMILARITY_THRESHOLD:
                return True, f"Схожее описание ({int(ratio_caption * 100)}%): '{item.get('caption')}'"

    return False, ""


def extract_json_payload(text):
    text = text.strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return json.loads(match.group(0))
    return json.loads(text)


def analyze_image_with_gemini(image_path, key_manager):
    system_prompt = (
        "Ты беспощадный арт-директор и архивариус дворового футбольного клуба 'ФК ГазМяс'. "
        "Твоя задача - объективно оценить историческую и мемную ценность кадра и написать едкое, живое описание.\n\n"
        "ШКАЛА ОЦЕНКИ SCORE (СТРОГО 1-10, НЕ ЗАВЫШАЙ БАЛЛЫ ИЗ ВЕЖЛИВОСТИ!):\n"
        "- 1-4 балла: смазанные пальцы, пустые стены, скучные бытовые фото без действия, рандомные скриншоты переписок, размытые лица.\n"
        "- 5-6 баллов: обычное селфи, стандартная посиделка, ничего легендарного или смешного.\n"
        "- 7-8 баллов: клубная атмосфера, яркие эмоции, алкоголь, забавные позы, узнаваемые лица ГазМяса.\n"
        "- 9-10 баллов: эпический завоз, исторический момент, угар, идеальный мем.\n\n"
        "КЛЮЧЕВЫЕ ТЕМАТИЧЕСКИЕ КОРЗИНЫ (СТРОГО ЧЕРЕДУЙ ИХ, НЕЛЬЗЯ ПОВТОРЯТЬ ОДНО И ТО ЖЕ!):\n"
        "Категорически запрещено вставлять Базанова, Свит Бонанзу или цитату про солнышко в каждый кадр. Выбирай только ОДНУ наиболее подходящую тему под конкретный визуал:\n\n"
        "1. КОРЗИНА 'ЛУДКА И ШУГАР РАШ' (ГЛАВНЫЙ ПРИОРИТЕТ В АЗАРТЕ):\n"
        "- Шугар Раш (Sugar Rush), кластеры 7х7, споты х128, покупка бонуски, розовая бурмалда, мармеладные мишки.\n"
        "- Теорема лудки: 99% лудоманов останавливаются ровно за шаг до мега-заноса.\n"
        "- 'Гоша, мы не пойдем в 666' (отказ от гиблой суеты, выбор надежного слота или пути домой).\n\n"
        "2. КОРЗИНА 'БЫТ, ПРИРОДА И ВИТАМИНЫ':\n"
        "- Дибуны ('нихуя Дибуны отстроили', дачные хроники, станция, лес).\n"
        "- 'Прикормить собачек' (забота о фауне, дворовые шашлыки, делёж сосисок).\n"
        "- 'Яблоки зеленые сорвал да поел', ворованный кислый крыжовник, виноград, витаминный заряд перед вторым таймом.\n\n"
        "3. КОРЗИНА 'РЕАЛЬНЫЕ ПАЦАНЫ':\n"
        "- Районный вайб, цитаты и повадки: Базанов (дал джазу), Колян, Вован, Эдик.\n"
        "- Использовать ТОЛЬКО если на фото видна конкретная районная нелепость или характерная поза.\n\n"
        "4. КОРЗИНА 'ЛОКАЦИИ И РУКОВОДСТВО':\n"
        "- Гараж на Гороховой, Франк на Сенной, Кресты (Карл Фридрих), Студос.\n"
        "- Директор Платон Нодь (Первый и Единственный), Менеджер Артем Визиров, Тренер Иван Плыгун, саппорт kfx.\n\n"
        "5. КОРЗИНА 'АРМЕЙСКАЯ СТРОЕВАЯ':\n"
        "- Дисциплина, строевой шаг, клубный гимн ('Солнышко светит, курочка клюет...'). Использовать редко, только для строгих групповых фото.\n\n"
        "Верни ответ СТРОГО в формате валидного JSON:\n"
        '{"score": 7, "title": "Заголовок (3-5 слов)", "caption": "Едкий панчлайн (1-2 предложения)", "tag": "Тег (Шугар Раш, Лудка, Дибуны, Витамины, Собачки, Основа, Тренер, Дирекция, Легенда)"}'
    )

    try:
        with Image.open(image_path) as img:
            rgb_img = img.convert("RGB")
            rgb_img.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            rgb_img.save(buffer, format="JPEG", quality=85)
            b64_image = base64.b64encode(buffer.getvalue()).decode("utf-8")
    except Exception as e:
        print(f"[!] Файл поврежден ({image_path}): {e}")
        return {"error_corrupt": True}

    payload = {
        "contents": [{
            "parts": [
                {"text": system_prompt},
                {"inline_data": {"mime_type": "image/jpeg", "data": b64_image}}
            ]
        }],
        "generationConfig": {"temperature": 0.3, "response_mime_type": "application/json"}
    }

    headers = {"Content-Type": "application/json"}
    errors_503_count = 0

    while True:
        api_key = key_manager.get_current_key()
        if not api_key:
            print("[X] Все доступные API-ключи исчерпаны.")
            return None

        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent?key={api_key}"

        try:
            res = requests.post(url, headers=headers, json=payload, timeout=30)

            if res.status_code == 200:
                errors_503_count = 0
                data = res.json()
                text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
                return extract_json_payload(text)

            elif res.status_code == 503:
                errors_503_count += 1
                if errors_503_count < 10:
                    print(f"Сервер занят (503). Пауза 8 сек... (попытка {errors_503_count}/10)")
                    time.sleep(8)
                    continue
                else:
                    print("10 ошибок 503 подряд. Смена ключа (пауза на 60 сек)...")
                    errors_503_count = 0
                    key_manager.mark_cooldown(seconds=60)
                    continue

            elif res.status_code == 429:
                errors_503_count = 0
                print("Превышен лимит запросов (429). Пауза для текущего ключа на 60 сек...")
                key_manager.mark_cooldown(seconds=60)
                continue

            elif res.status_code == 400:
                print(f"[!] Ошибка запроса 400 (Bad Request): {res.text}")
                return {"error_bad_request": True}

            elif res.status_code in (401, 403):
                errors_503_count = 0
                print(f"Ошибка авторизации ({res.status_code}): {res.text}. Исключаем ключ...")
                key_manager.mark_dead()
                continue

            else:
                errors_503_count = 0
                print(f"Неизвестный статус: {res.status_code}. Смена ключа...")
                key_manager.switch_to_next()
                time.sleep(2)
                continue

        except requests.exceptions.RequestException as e:
            print(f"Сетевой сбой: {e}. Пауза 5 сек...")
            time.sleep(5)
            continue
        except Exception as e:
            print(f"Ошибка обработки: {e}. Повтор...")
            time.sleep(2)
            continue


def load_json(filepath):
    if os.path.exists(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    return json.loads(content)
        except Exception:
            return []
    return []


def save_json(filepath, data):
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())


def main():
    keys = load_keys()
    key_manager = KeyManager(keys)

    print(f"Загружено ключей: {len(keys)}")
    key_manager.print_status()

    supported_exts = (".jpg", ".jpeg", ".png", ".webp")
    incoming_files = [f for f in os.listdir(INCOMING_DIR) if f.lower().endswith(supported_exts)]
    print(f"Кадров на просмотр: {len(incoming_files)}")

    db = load_json(DATA_FILE)
    pending = load_json(PENDING_FILE)

    print(f"Уже на сайте ({DATA_FILE}): {len(db)} | В очереди админки ({PENDING_FILE}): {len(pending)}\n")

    processed_hashes = {x["hash"] for x in (db + pending) if x.get("hash")}

    if not incoming_files:
        print(f"Папка {INCOMING_DIR}/ пуста. Все кадры обработаны.")
        return

    total = len(incoming_files)
    for idx, filename in enumerate(incoming_files, start=1):
        incoming_path = os.path.join(INCOMING_DIR, filename)
        print(f"[{idx}/{total}] Проверка кадра: {filename}")

        img_hash = calculate_dhash(incoming_path)
        if img_hash is not None and img_hash in processed_hashes:
            print(f"-> Дубликат кадра (по хэшу). Перемещение в {DELETED_DIR}/")
            move_to_deleted(incoming_path)
            continue

        result = analyze_image_with_gemini(incoming_path, key_manager)
        if not result:
            print("-> Не удалось получить ответ от API. Остановка очереди.")
            break

        if result.get("error_corrupt") or result.get("error_bad_request"):
            print(f"-> Кадр поврежден или не принят API. Перемещение в {DELETED_DIR}/\n")
            move_to_deleted(incoming_path)
            continue

        score = result.get("score", 0)
        print(f"-> Оценка куратора: {score}/10")

        if score < MIN_SCORE:
            print(f"-> Не дотягивает (< {MIN_SCORE}). Перемещение в {DELETED_DIR}/\n")
            move_to_deleted(incoming_path)
            continue

        title = result.get("title", "ФК ГазМяс")
        caption = result.get("caption", "Момент матча")

        is_dup, dup_reason = is_duplicate_text(title, caption, db + pending)
        if is_dup:
            print(f"-> Текстовый повтор: {dup_reason}. В {DELETED_DIR}/\n")
            move_to_deleted(incoming_path)
            continue

        # Сохраняем кандидат во временную папку pending_images
        new_filename = f"pending_{int(time.time())}_{filename}"
        temp_dest = os.path.join(PENDING_DIR, new_filename)

        with Image.open(incoming_path) as img:
            rgb_img = img.convert("RGB")
            rgb_img.save(temp_dest, "JPEG", quality=85)

        os.remove(incoming_path)

        pending_item = {
            "id": f"item_{int(time.time() * 1000)}",
            "temp_file": temp_dest.replace("\\", "/"),
            "original_name": filename,
            "title": title,
            "caption": caption,
            "tag": result.get("tag", "Основа"),
            "score": score,
            "hash": img_hash
        }

        pending.append(pending_item)
        save_json(PENDING_FILE, pending)

        if img_hash is not None:
            processed_hashes.add(img_hash)

        print(f"-> [КАНДИДАТ ОДОБРЕН] Отправлен в админку: \"{title}\" (в очереди: {len(pending)})\n")
        time.sleep(1)

    print(f"Анализ завершен. Кандидатов в админке: {len(pending)}")


if __name__ == "__main__":
    main()