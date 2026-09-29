import os
import json
import shutil
import time
import threading
from urllib.parse import urlparse
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
import requests

PORT = 8080
PENDING_FILE = "pending.json"
REQUESTS_FILE = "requests.json"
DATA_FILE = "data.json"
IMAGES_DIR = "images"
DELETED_DIR = "deleted"
PENDING_DIR = "pending_images"

# ВСТАВЬ СВОИ ДАННЫЕ ОТ JSONBIN.IO
JSONBIN_BIN_ID = "ВСТАВЬ_СВОЙ_BIN_ID"
JSONBIN_MASTER_KEY = "ВСТАВЬ_СВОЙ_MASTER_KEY"

os.makedirs(IMAGES_DIR, exist_ok=True)
os.makedirs(DELETED_DIR, exist_ok=True)
os.makedirs(PENDING_DIR, exist_ok=True)


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


def sync_cloud_buffer():
    if not JSONBIN_BIN_ID or "ВСТАВЬ" in JSONBIN_BIN_ID:
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

                # Игнорируем тестовые маркеры
                data = [x for x in data if not x.get("init")]

                if len(data) > 0:
                    print(f"[*] Получено новых заявок из облака: {len(data)}")
                    pending = load_json(PENDING_FILE)
                    reqs = load_json(REQUESTS_FILE)

                    for item in data:
                        if item.get("type") == "idea":
                            pending_item = {
                                "id": item.get("id"),
                                "temp_file": item.get("image_url"),
                                "original_name": "Идея от " + item.get("author", "Болельщика"),
                                "title": item.get("title"),
                                "caption": item.get("caption"),
                                "tag": item.get("tag", "Предложка"),
                                "score": 10,
                                "is_user_idea": True
                            }
                            if not any(x.get("id") == pending_item["id"] for x in pending):
                                pending.insert(0, pending_item)
                        else:
                            if not any(x.get("id") == item.get("id") for x in reqs):
                                reqs.insert(0, item)

                    save_json(PENDING_FILE, pending)
                    save_json(REQUESTS_FILE, reqs)

                    # Очищаем очередь в облаке, оставляя валидный контейнер
                    requests.put(url, headers={**headers, "Content-Type": "application/json"}, json={"queue": []}, timeout=10)
                    print("[✓] Облачная очередь перенесена на ПК и очищена.")
        except Exception:
            pass

        time.sleep(5)


# Фоновый опрос облака
cloud_thread = threading.Thread(target=sync_cloud_buffer, daemon=True)
cloud_thread.start()


class AdminHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Allow", "GET, POST, OPTIONS")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200, "OK")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/api/pending":
            pending = load_json(PENDING_FILE)
            cleaned = []
            changed = False
            for item in pending:
                t_file = item.get("temp_file", "").replace("/", os.sep).replace("\\", os.sep)
                if not t_file or os.path.exists(t_file) or item.get("is_user_idea"):
                    cleaned.append(item)
                else:
                    changed = True

            if changed:
                save_json(PENDING_FILE, cleaned)
                pending = cleaned

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(json.dumps(pending, ensure_ascii=False).encode("utf-8"))
            return

        if path == "/api/requests":
            reqs = load_json(REQUESTS_FILE)
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(json.dumps(reqs, ensure_ascii=False).encode("utf-8"))
            return

        if path in ("", "/", "/admin"):
            self.path = "/admin.html"

        return super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"

        try:
            req = json.loads(body)
        except Exception:
            req = {}

        if path == "/api/handle_request":
            action = req.get("action")
            req_id = req.get("id")

            reqs = load_json(REQUESTS_FILE)
            target = next((x for x in reqs if x.get("id") == req_id), None)

            if not target:
                self.send_response(404)
                self.end_headers()
                return

            if action == "apply":
                db = load_json(DATA_FILE)
                file_target = target.get("file")

                if target.get("type") == "delete":
                    db = [item for item in db if item.get("file") != file_target]
                    save_json(DATA_FILE, db)
                    if os.path.exists(file_target):
                        filename = os.path.basename(file_target)
                        shutil.move(file_target, os.path.join(DELETED_DIR, filename))

                elif target.get("type") == "edit":
                    for item in db:
                        if item.get("file") == file_target:
                            if target.get("new_title"):
                                item["title"] = target["new_title"]
                            if target.get("new_caption"):
                                item["caption"] = target["new_caption"]
                            break
                    save_json(DATA_FILE, db)

            reqs = [x for x in reqs if x.get("id") != req_id]
            save_json(REQUESTS_FILE, reqs)

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
            return

        if path == "/api/moderate":
            action = req.get("action")
            item_id = str(req.get("id"))
            updated_title = req.get("title")
            updated_caption = req.get("caption")
            updated_tag = req.get("tag")

            pending = load_json(PENDING_FILE)
            item = next((x for x in pending if str(x.get("id")) == item_id), None)

            if not item:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"status":"already_removed"}')
                return

            raw_path = item.get("temp_file", "")
            src_file = raw_path.replace("/", os.sep).replace("\\", os.sep)
            filename = os.path.basename(src_file)

            if action == "approve":
                db = load_json(DATA_FILE)
                if item.get("is_user_idea"):
                    final_path = item.get("temp_file")
                else:
                    dest_filename = filename.replace("pending_", "photo_")
                    dest_file = os.path.join(IMAGES_DIR, dest_filename)
                    if os.path.exists(src_file):
                        shutil.move(src_file, dest_file)
                    final_path = f"images/{dest_filename}".replace("\\", "/")

                card = {
                    "file": final_path,
                    "title": updated_title or item.get("title", "ФК ГазМяс"),
                    "caption": updated_caption or item.get("caption", ""),
                    "tag": updated_tag or item.get("tag", "Основа"),
                    "tag_class": "alt" if len(db) % 2 == 0 else "",
                    "score": item.get("score", 7),
                    "hash": item.get("hash")
                }
                db.append(card)
                save_json(DATA_FILE, db)

            elif action == "reject":
                if not item.get("is_user_idea") and os.path.exists(src_file):
                    shutil.move(src_file, os.path.join(DELETED_DIR, filename))

            pending = [x for x in pending if str(x.get("id")) != item_id]
            save_json(PENDING_FILE, pending)

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
            return

        self.send_response(404)
        self.end_headers()


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", PORT), AdminHandler)
    print(f"Админ-сервер запущен: http://localhost:{PORT}/admin.html")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановка сервера.")