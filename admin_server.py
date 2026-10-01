import os
import sys
import json
import shutil
import time
import threading
from pathlib import Path
from typing import Any, Dict, List
from dotenv import load_dotenv
from urllib.parse import urlparse, parse_qs
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import requests

PORT = 8080
BASE_DIR = Path(__file__).resolve().parent
os.chdir(BASE_DIR)

DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

DATA_FILE = DATA_DIR / "data.json"
PENDING_FILE = DATA_DIR / "pending.json"
REQUESTS_FILE = DATA_DIR / "requests.json"

PENDING_DIR = BASE_DIR / "pending"
IMAGES_DIR = BASE_DIR / "images"
DELETED_DIR = BASE_DIR / "deleted"

for directory in (PENDING_DIR, IMAGES_DIR, DELETED_DIR):
    directory.mkdir(parents=True, exist_ok=True)

load_dotenv()

JSONBIN_BIN_ID = os.getenv("JSONBIN_BIN_ID")
JSONBIN_API_KEY = os.getenv("JSONBIN_API_KEY")

if not JSONBIN_BIN_ID or not JSONBIN_API_KEY:
    print("[ПРЕДУПРЕЖДЕНИЕ] JSONBIN_BIN_ID или JSONBIN_API_KEY не заданы в .env")


def read_json_file(path: Path) -> List[Dict[str, Any]]:
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = json.load(f)
                if isinstance(content, list):
                    return content
                if isinstance(content, dict):
                    return content.get("items", [])
        except Exception as e:
            print(f"[ОШИБКА] Чтение {path.name}: {e}")
    return []


def write_json_file(path: Path, data: List[Dict[str, Any]]) -> None:
    temp = path.with_suffix(".tmp")
    try:
        with open(temp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        temp.replace(path)
    except Exception as e:
        print(f"[ОШИБКА] Запись {path.name}: {e}")
        if temp.exists():
            temp.unlink()


def sync_cloud_buffer():
    if not JSONBIN_BIN_ID or not JSONBIN_API_KEY:
        return

    url = f"https://api.jsonbin.io/v3/b/{JSONBIN_BIN_ID}"
    headers = {
        "X-Master-Key": JSONBIN_API_KEY,
        "Content-Type": "application/json"
    }

    while True:
        try:
            res = requests.get(f"{url}/latest", headers=headers, timeout=10)
            if res.status_code == 200:
                record = res.json().get("record", {})
                if isinstance(record, dict):
                    cloud_data = record.get("queue", [])
                elif isinstance(record, list):
                    cloud_data = record
                else:
                    cloud_data = []

                new_requests = [x for x in cloud_data if not x.get("init")]

                if new_requests:
                    local_requests = read_json_file(REQUESTS_FILE)
                    existing_ids = {r.get("id") for r in local_requests if "id" in r}

                    added_count = 0
                    for item in new_requests:
                        if item.get("id") not in existing_ids:
                            local_requests.append(item)
                            added_count += 1

                    if added_count > 0:
                        write_json_file(REQUESTS_FILE, local_requests)
                        print(f"[JSONBin] Загружено новых заявок: {added_count}")

                    requests.put(url, headers=headers, json={"queue": []}, timeout=10)
        except Exception as e:
            print(f"[JSONBin Sync] Ошибка опроса: {e}")

        time.sleep(25)


class AdminRequestHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.end_headers()

    def send_json(self, data: Any, status: int = 200):
        response_bytes = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(response_bytes)))
        self.end_headers()
        self.wfile.write(response_bytes)

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == "/api/pending":
            return self.send_json(read_json_file(PENDING_FILE))

        if parsed.path == "/api/data":
            return self.send_json(read_json_file(DATA_FILE))

        if parsed.path == "/api/requests":
            return self.send_json(read_json_file(REQUESTS_FILE))

        return super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        content_length = int(self.headers.get("Content-Length", 0))
        post_data = self.rfile.read(content_length)

        try:
            body = json.loads(post_data.decode("utf-8")) if post_data else {}
        except Exception:
            body = {}

        if parsed.path == "/api/approve":
            filename = body.get("filename")
            crop_x = body.get("crop_x", 50)
            crop_y = body.get("crop_y", 50)

            pending_items = read_json_file(PENDING_FILE)
            item = next((x for x in pending_items if x.get("filename") == filename), None)

            if item:
                pending_items = [x for x in pending_items if x.get("filename") != filename]
                write_json_file(PENDING_FILE, pending_items)

                src = PENDING_DIR / filename
                dst = IMAGES_DIR / filename
                if src.exists():
                    shutil.move(src, dst)

                for key in ("title", "caption", "tag", "score"):
                    if key in body:
                        item[key] = body[key]

                item["crop_x"] = crop_x
                item["crop_y"] = crop_y

                data_items = read_json_file(DATA_FILE)
                data_items.insert(0, item)
                write_json_file(DATA_FILE, data_items)

                return self.send_json({"status": "success", "message": "Одобрено в состав"})

            return self.send_json({"status": "error", "message": "Элемент не найден"}, 404)

        if parsed.path == "/api/reject":
            filename = body.get("filename")
            pending_items = read_json_file(PENDING_FILE)
            item = next((x for x in pending_items if x.get("filename") == filename), None)

            if item:
                pending_items = [x for x in pending_items if x.get("filename") != filename]
                write_json_file(PENDING_FILE, pending_items)

                src = PENDING_DIR / filename
                dst = DELETED_DIR / filename
                if src.exists():
                    shutil.move(src, dst)

                return self.send_json({"status": "success", "message": "Отправлено в брак"})

            return self.send_json({"status": "error", "message": "Элемент не найден"}, 404)

        if parsed.path == "/api/resolve-request":
            req_id = body.get("id")
            requests_items = read_json_file(REQUESTS_FILE)
            requests_items = [x for x in requests_items if x.get("id") != req_id]
            write_json_file(REQUESTS_FILE, requests_items)
            return self.send_json({"status": "success"})

        return self.send_json({"status": "error", "message": "Неизвестный эндпоинт"}, 404)


def run_server():
    sync_thread = threading.Thread(target=sync_cloud_buffer, daemon=True)
    sync_thread.start()

    server = ThreadingHTTPServer(("", PORT), AdminRequestHandler)
    print(f"[ШТАБ МОДЕРАЦИИ] Сервер запущен на http://localhost:{PORT}/admin.html")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[ОСТАНОВКА] Сервер выключен.")
        server.server_close()


if __name__ == "__main__":
    run_server()