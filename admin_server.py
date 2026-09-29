import os
import sys
import json
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

# ==========================================
# КОНФИГУРАЦИЯ И ПУТИ
# ==========================================
PORT = 8080
BASE_DIR = Path(__file__).resolve().parent

# Принудительно устанавливаем рабочую директорию в корень скрипта
os.chdir(BASE_DIR)

DATA_FILE = BASE_DIR / "data.json"
PENDING_FILE = BASE_DIR / "pending.json"
REQUESTS_FILE = BASE_DIR / "requests.json"

PENDING_DIR = BASE_DIR / "pending"
IMAGES_DIR = BASE_DIR / "images"
DELETED_DIR = BASE_DIR / "deleted"

# ==========================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ДЛЯ JSON
# ==========================================
def read_json_file(path: Path) -> List[Dict[str, Any]]:
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read().strip()
                return json.loads(content) if content else []
        except Exception as e:
            print(f"[Ошибка чтения {path.name}]: {e}")
            return []
    return []

def write_json_file(path: Path, data: List[Dict[str, Any]]):
    temp = path.with_suffix(".tmp")
    with open(temp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    temp.replace(path)

# ==========================================
# ОБРАБОТЧИК ЗАПРОСОВ API И СТАТИКИ
# ==========================================
class AdminAPIHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(BASE_DIR), **kwargs)

    def end_headers(self):
        # Запрет кэширования для обновления очереди в реальном времени
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        super().end_headers()

    def send_json(self, status_code: int, data: Any):
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def do_GET(self):
        # Роут очереди на модерацию
        if self.path in ("/api/pending", "/pending.json"):
            return self.send_json(200, read_json_file(PENDING_FILE))

        # Роут опубликованных карточек сайта
        if self.path in ("/api/data", "/data.json"):
            return self.send_json(200, read_json_file(DATA_FILE))

        # Роут пользовательских заявок
        if self.path in ("/api/requests", "/requests.json"):
            return self.send_json(200, read_json_file(REQUESTS_FILE))

        # Перенаправление с корня на страницу панели модератора
        if self.path in ("/", "/admin", "/admin/"):
            self.path = "/admin.html"

        return super().do_GET()

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
        
        try:
            payload = json.loads(body)
        except Exception:
            return self.send_json(400, {"error": "Невалидный JSON"})

        # ------------------------------------------------------
        # 1. ОДОБРЕНИЕ КАДРА ИЗ ОЧЕРЕДИ МОДЕРАЦИИ
        # ------------------------------------------------------
        if self.path == "/api/approve":
            item_id = str(payload.get("id"))
            pending_items = read_json_file(PENDING_FILE)
            data_items = read_json_file(DATA_FILE)

            target_idx = next((i for i, x in enumerate(pending_items) if str(x.get("id")) == item_id), None)
            if target_idx is None:
                return self.send_json(404, {"error": "Кадр не найден в pending.json"})

            item = pending_items.pop(target_idx)
            filename = item.get("filename")

            # Применение ручных правок из формы админки
            for field in ("title", "caption", "tag", "date"):
                if field in payload and payload[field] is not None:
                    item[field] = payload[field]

            # Перемещение файла из pending/ в images/
            src_file = PENDING_DIR / filename
            dest_file = IMAGES_DIR / filename

            if src_file.exists():
                shutil.move(str(src_file), str(dest_file))
            elif not dest_file.exists():
                return self.send_json(404, {"error": f"Файл {filename} не найден на диске"})

            # Обновление пути внутри объекта для сайта
            item["image"] = f"images/{filename}"

            # Добавляем в начало ленты сайта
            data_items.insert(0, item)

            write_json_file(DATA_FILE, data_items)
            write_json_file(PENDING_FILE, pending_items)

            print(f"[Админка] Одобрено: {filename} -> сохранено в data.json")
            return self.send_json(200, {"success": True, "item": item})

        # ------------------------------------------------------
        # 2. ОТКЛОНЕНИЕ / УДАЛЕНИЕ КАДРА
        # ------------------------------------------------------
        if self.path in ("/api/reject", "/api/delete"):
            item_id = str(payload.get("id"))
            pending_items = read_json_file(PENDING_FILE)
            data_items = read_json_file(DATA_FILE)

            # Проверяем очередь pending.json
            p_idx = next((i for i, x in enumerate(pending_items) if str(x.get("id")) == item_id), None)
            if p_idx is not None:
                item = pending_items.pop(p_idx)
                filename = item.get("filename")
                src = PENDING_DIR / filename
                dst = DELETED_DIR / filename
                if src.exists():
                    if dst.exists():
                        dst.unlink()
                    shutil.move(str(src), str(dst))
                write_json_file(PENDING_FILE, pending_items)
                print(f"[Админка] Отклонен из очереди: {filename} -> перенесен в deleted/")
                return self.send_json(200, {"success": True})

            # Проверяем уже опубликованные карточки data.json
            d_idx = next((i for i, x in enumerate(data_items) if str(x.get("id")) == item_id), None)
            if d_idx is not None:
                item = data_items.pop(d_idx)
                filename = item.get("filename")
                src = IMAGES_DIR / filename
                dst = DELETED_DIR / filename
                if src.exists():
                    if dst.exists():
                        dst.unlink()
                    shutil.move(str(src), str(dst))
                write_json_file(DATA_FILE, data_items)
                print(f"[Админка] Удален с сайта: {filename} -> перенесен в deleted/")
                return self.send_json(200, {"success": True})

            return self.send_json(404, {"error": "Кадр с таким ID не найден"})

        return self.send_json(404, {"error": "Маршрут API не найден"})

# ==========================================
# ТОЧКА ВХОДА
# ==========================================
def run_server():
    # Создание необходимых папок при старте
    for folder in (PENDING_DIR, IMAGES_DIR, DELETED_DIR):
        folder.mkdir(parents=True, exist_ok=True)

    ThreadingHTTPServer.allow_reuse_address = True
    server_address = ("", PORT)

    try:
        httpd = ThreadingHTTPServer(server_address, AdminAPIHandler)
    except OSError as e:
        if "10048" in str(e) or "Address already in use" in str(e):
            print(f"[Ошибка] Порт {PORT} уже занят другим процессом.")
            print("Выполните в консоли: taskkill /F /IM python.exe")
            sys.exit(1)
        raise e

    print("========================================================")
    print(f" Локальный сервер админ-панели запущен!")
    print(f" URL: http://localhost:{PORT}/admin.html")
    print(" Для остановки нажмите Ctrl + C")
    print("========================================================")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановка сервера...")
        httpd.server_close()

if __name__ == "__main__":
    run_server()