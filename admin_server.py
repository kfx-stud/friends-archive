import os
import json
import shutil
from pathlib import Path
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

PORT = 8000
BASE_DIR = Path(__file__).resolve().parent

DATA_FILE = BASE_DIR / "data.json"
PENDING_FILE = BASE_DIR / "pending.json"
REQUESTS_FILE = BASE_DIR / "requests.json"

PENDING_DIR = BASE_DIR / "pending"
IMAGES_DIR = BASE_DIR / "images"
DELETED_DIR = BASE_DIR / "deleted"

def read_json_file(path: Path) -> list:
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []

def write_json_file(path: Path, data: list):
    temp = path.with_suffix(".tmp")
    with open(temp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    temp.replace(path)

class AdminAPIHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        # Отключаем кэш браузера для динамического обновления очереди
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
            items = read_json_file(PENDING_FILE)
            return self.send_json(200, items)

        # Роут опубликованных кадров
        if self.path in ("/api/data", "/data.json"):
            items = read_json_file(DATA_FILE)
            return self.send_json(200, items)

        # Роут пользовательских заявок
        if self.path in ("/api/requests", "/requests.json"):
            items = read_json_file(REQUESTS_FILE)
            return self.send_json(200, items)

        # Перенаправление с корня на админку
        if self.path == "/" or self.path == "/admin":
            self.path = "/admin.html"

        return super().do_GET()

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8")
        
        try:
            payload = json.loads(body) if body else {}
        except Exception:
            return self.send_json(400, {"error": "Невалидный JSON"})

        # ==========================================
        # 1. ОДОБРЕНИЕ КАДРА ИЗ ОЧЕРЕДИ
        # ==========================================
        if self.path == "/api/approve":
            item_id = payload.get("id")
            pending_items = read_json_file(PENDING_FILE)
            data_items = read_json_file(DATA_FILE)

            target_idx = next((i for i, x in enumerate(pending_items) if str(x.get("id")) == str(item_id)), None)
            if target_idx is None:
                return self.send_json(404, {"error": "Кадр не найден в pending.json"})

            item = pending_items.pop(target_idx)
            filename = item.get("filename")

            # Применяем ручные правки из админки (если пользователь отредактировал поля)
            for field in ("title", "caption", "tag", "date"):
                if field in payload and payload[field]:
                    item[field] = payload[field]

            # Перенос файла: pending/ -> images/
            src_file = PENDING_DIR / filename
            dest_file = IMAGES_DIR / filename

            if src_file.exists():
                shutil.move(str(src_file), str(dest_file))
            elif not dest_file.exists():
                return self.send_json(404, {"error": f"Файл {filename} отсутствует на диске"})

            # Обновление пути карточки для сайта
            item["image"] = f"images/{filename}"

            # Добавляем в начало базы сайта
            data_items.insert(0, item)

            write_json_file(DATA_FILE, data_items)
            write_json_file(PENDING_FILE, pending_items)

            print(f"[Админка] Одобрен кадр: {filename} -> сохранен в data.json")
            return self.send_json(200, {"success": True, "item": item})

        # ==========================================
        # 2. ОТКЛОНЕНИЕ / УДАЛЕНИЕ КАДРА
        # ==========================================
        if self.path in ("/api/reject", "/api/delete"):
            item_id = payload.get("id")
            pending_items = read_json_file(PENDING_FILE)

            target_idx = next((i for i, x in enumerate(pending_items) if str(x.get("id")) == str(item_id)), None)
            if target_idx is not None:
                item = pending_items.pop(target_idx)
                filename = item.get("filename")
                src_file = PENDING_DIR / filename
                dest_file = DELETED_DIR / filename

                if src_file.exists():
                    if dest_file.exists():
                        dest_file.unlink()
                    shutil.move(str(src_file), str(dest_file))

                write_json_file(PENDING_FILE, pending_items)
                print(f"[Админка] Отклонен кадр: {filename} -> перемещен в deleted/")
                return self.send_json(200, {"success": True})

            return self.send_json(404, {"error": "Кадр не найден в pending"})

        return self.send_json(404, {"error": "Маршрут не найден"})

def run_server():
    for d in (PENDING_DIR, IMAGES_DIR, DELETED_DIR):
        d.mkdir(parents=True, exist_ok=True)

    server_address = ("", PORT)
    httpd = ThreadingHTTPServer(server_address, AdminAPIHandler)
    print(f"Админ-сервер запущен: http://localhost:{PORT}/admin.html")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановка сервера...")
        httpd.server_close()

if __name__ == "__main__":
    run_server()