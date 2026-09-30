import os
import sys
import json
import shutil
import time
import threading
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import urlparse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import requests

# ==========================================
# КОНФИГУРАЦИЯ И ПУТИ
# ==========================================
PORT = 8080
BASE_DIR = Path(__file__).resolve().parent
os.chdir(BASE_DIR)

DATA_FILE = BASE_DIR / "data.json"
PENDING_FILE = BASE_DIR / "pending.json"
REQUESTS_FILE = BASE_DIR / "requests.json"

PENDING_DIR = BASE_DIR / "pending"
IMAGES_DIR = BASE_DIR / "images"
DELETED_DIR = BASE_DIR / "deleted"

# Ключи JSONBin (как в index.html)
JSONBIN_BIN_ID = "6abbe438ffd5d160533c11f1"
JSONBIN_MASTER_KEY = "$2a$10$VRoPiN8zdepg2AgC69BLZudokIxDgyL3LmDVPcv5HlHKRAalpZ5Vq"

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
# ФОНОВЫЙ ОПРОС ОБЛАКА JSONBIN (раз в 25 сек)
# ==========================================
def sync_cloud_buffer():
    if not JSONBIN_BIN_ID:
        return

    url = f"https://api.jsonbin.io/v3/b/{JSONBIN_BIN_ID}"
    headers = {"X-Master-Key": JSONBIN_MASTER_KEY}

    while True:
        try:
            res = requests.get(f"{url}/latest", headers=headers, timeout=10)
            if res.status_code == 200:
                record = res.json().get("record", {})
                if isinstance(record, dict):
                    data = record.get("queue", [])
                elif isinstance(record, list):
                    data = record
                else:
                    data = []

                data = [x for x in data if not x.get("init")]

                if len(data) > 0:
                    print(f"[*] Получено новых заявок из облака: {len(data)}")
                    pending = read_json_file(PENDING_FILE)
                    reqs = read_json_file(REQUESTS_FILE)

                    for item in data:
                        if item.get("type") == "idea":
                            pending_item = {
                                "id": item.get("id"),
                                "filename": "",
                                "image": item.get("image_url") or "images/placeholder.jpg",
                                "title": item.get("title"),
                                "caption": item.get("caption"),
                                "tag": item.get("tag", "Предложка"),
                                "score": 10,
                                "date": time.strftime("%Y-%m-%d"),
                                "is_user_idea": True
                            }
                            if not any(x.get("id") == pending_item["id"] for x in pending):
                                pending.insert(0, pending_item)
                        else:
                            if not any(x.get("id") == item.get("id") for x in reqs):
                                reqs.insert(0, item)

                    write_json_file(PENDING_FILE, pending)
                    write_json_file(REQUESTS_FILE, reqs)

                    requests.put(url, headers={**headers, "Content-Type": "application/json"}, json={"queue": []}, timeout=10)
                    print("[✓] Облачный буфер перенесён в requests.json / pending.json")
        except Exception:
            pass

        time.sleep(25)

cloud_thread = threading.Thread(target=sync_cloud_buffer, daemon=True)
cloud_thread.start()

# ==========================================
# ОБРАБОТЧИК ЗАПРОСОВ
# ==========================================
class AdminAPIHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(BASE_DIR), **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200, "OK")
        self.end_headers()

    def send_json(self, status_code: int, data: Any):
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path in ("/api/pending", "/pending.json"):
            return self.send_json(200, read_json_file(PENDING_FILE))

        if path in ("/api/requests", "/requests.json"):
            return self.send_json(200, read_json_file(REQUESTS_FILE))

        if path in ("", "/", "/admin"):
            self.path = "/admin.html"

        return super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
        
        try:
            payload = json.loads(body)
        except Exception:
            return self.send_json(400, {"error": "Невалидный JSON"})

        # Одобрение / Отклонение кандидатов
        if path == "/api/moderate":
            action = payload.get("action")
            item_id = str(payload.get("id"))
            pending_items = read_json_file(PENDING_FILE)
            data_items = read_json_file(DATA_FILE)

            target = next((x for x in pending_items if str(x.get("id")) == item_id), None)
            if not target:
                return self.send_json(200, {"status": "already_handled"})

            filename = target.get("filename")
            is_idea = target.get("is_user_idea", False)

            if action == "approve":
                if not is_idea and filename:
                    src_file = PENDING_DIR / filename
                    dest_file = IMAGES_DIR / filename
                    if src_file.exists():
                        shutil.move(str(src_file), str(dest_file))
                    image_path = f"images/{filename}"
                else:
                    image_path = target.get("image") or target.get("file")

                # Формируем структуру карточки строго под index.html
                card = {
                    "id": target.get("id"),
                    "file": image_path,
                    "title": payload.get("title") or target.get("title", "ФК ГазМяс"),
                    "caption": payload.get("caption") or target.get("caption", ""),
                    "tag": payload.get("tag") or target.get("tag", "Основа"),
                    "tag_class": "alt" if len(data_items) % 2 == 0 else "",
                    "score": target.get("score", 7),
                    "date": target.get("date", time.strftime("%Y-%m-%d")),
                    "sha256": target.get("sha256", "")
                }
                data_items.insert(0, card)
                write_json_file(DATA_FILE, data_items)

            elif action == "reject":
                if not is_idea and filename:
                    src_file = PENDING_DIR / filename
                    dest_file = DELETED_DIR / filename
                    if src_file.exists():
                        shutil.move(str(src_file), str(dest_file))

            pending_items = [x for x in pending_items if str(x.get("id")) != item_id]
            write_json_file(PENDING_FILE, pending_items)
            return self.send_json(200, {"status": "ok"})

        # Обработка пользовательских правок и удалений
        if path == "/api/handle_request":
            action = payload.get("action")
            req_id = payload.get("id")
            custom_title = payload.get("custom_title")
            custom_caption = payload.get("custom_caption")

            reqs = read_json_file(REQUESTS_FILE)
            target = next((x for x in reqs if x.get("id") == req_id), None)

            if not target:
                return self.send_json(404, {"error": "Заявка не найдена"})

            if action == "apply":
                db = read_json_file(DATA_FILE)
                file_target = target.get("file")

                if target.get("type") == "delete":
                    db = [item for item in db if item.get("file") != file_target]
                    write_json_file(DATA_FILE, db)
                    if file_target:
                        disk_path = BASE_DIR / file_target.replace("/", os.sep)
                        if disk_path.exists():
                            shutil.move(str(disk_path), str(DELETED_DIR / disk_path.name))

                elif target.get("type") == "edit":
                    for item in db:
                        if item.get("file") == file_target:
                            item["title"] = custom_title if custom_title is not None else target.get("new_title", item.get("title"))
                            item["caption"] = custom_caption if custom_caption is not None else target.get("new_caption", item.get("caption"))
                            break
                    write_json_file(DATA_FILE, db)

            reqs = [x for x in reqs if x.get("id") != req_id]
            write_json_file(REQUESTS_FILE, reqs)
            return self.send_json(200, {"status": "ok"})

        return self.send_json(404, {"error": "Неизвестный роут"})

def run_server():
    for folder in (PENDING_DIR, IMAGES_DIR, DELETED_DIR):
        folder.mkdir(parents=True, exist_ok=True)

    ThreadingHTTPServer.allow_reuse_address = True
    server_address = ("", PORT)

    try:
        httpd = ThreadingHTTPServer(server_address, AdminAPIHandler)
    except OSError as e:
        if "10048" in str(e) or "Address already in use" in str(e):
            print(f"[Ошибка] Порт {PORT} занят. Выполните: taskkill /F /IM python.exe")
            sys.exit(1)
        raise e

    print(f"Админка доступна: http://localhost:{PORT}/admin.html")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановка сервера...")
        httpd.server_close()

if __name__ == "__main__":
    run_server()