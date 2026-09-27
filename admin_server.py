import os
import json
import shutil
import time
from urllib.parse import urlparse
from http.server import HTTPServer, SimpleHTTPRequestHandler

PORT = 8080
PENDING_FILE = "pending.json"
DATA_FILE = "data.json"
IMAGES_DIR = "images"
DELETED_DIR = "deleted"
PENDING_DIR = "pending_images"

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


class AdminHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/pending":
            pending = load_json(PENDING_FILE)
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(json.dumps(pending, ensure_ascii=False).encode("utf-8"))
            return

        if path in ("/", "/admin"):
            self.path = "/admin.html"

        return super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/moderate":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8")
            req = json.loads(body)

            action = req.get("action")
            item_id = req.get("id")
            updated_title = req.get("title")
            updated_caption = req.get("caption")
            updated_tag = req.get("tag")

            pending = load_json(PENDING_FILE)
            item = next((x for x in pending if x.get("id") == item_id), None)

            if not item:
                self.send_response(404)
                self.end_headers()
                return

            src_file = item["temp_file"]

            if action == "approve":
                db = load_json(DATA_FILE)
                filename = os.path.basename(src_file).replace("pending_", "photo_")
                dest_file = os.path.join(IMAGES_DIR, filename)

                if os.path.exists(src_file):
                    shutil.move(src_file, dest_file)

                card = {
                    "file": f"images/{filename}".replace("\\", "/"),
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
                filename = os.path.basename(src_file)
                dest_deleted = os.path.join(DELETED_DIR, filename)
                if os.path.exists(dest_deleted):
                    base, ext = os.path.splitext(filename)
                    dest_deleted = os.path.join(DELETED_DIR, f"{base}_{int(time.time())}{ext}")

                if os.path.exists(src_file):
                    shutil.move(src_file, dest_deleted)

            pending = [x for x in pending if x.get("id") != item_id]
            save_json(PENDING_FILE, pending)

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
            return

        self.send_response(404)
        self.end_headers()


if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", PORT), AdminHandler)
    print(f"Админ-сервер запущен: http://localhost:{PORT}/admin.html")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановка сервера.")