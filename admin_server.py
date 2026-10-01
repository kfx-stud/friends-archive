import os
import sys
import json
import shutil
import time
import threading
from pathlib import Path
from typing import Any, Dict, List
from dotenv import load_dotenv
from urllib.parse import urlparse
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
ARTICLES_FILE = DATA_DIR / "articles.json"

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
                    return content.get("items", content.get("queue", []))
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
                    existing_ids = {str(r.get("id")) for r in local_requests if "id" in r}

                    added_count = 0
                    for item in new_requests:
                        if str(item.get("id")) not in existing_ids:
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

        if parsed.path == "/api/articles":
            return self.send_json(read_json_file(ARTICLES_FILE))

        if parsed.path in ("", "/", "/admin"):
            self.path = "/admin.html"

        return super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        content_length = int(self.headers.get("Content-Length", 0))
        post_data = self.rfile.read(content_length)

        try:
            body = json.loads(post_data.decode("utf-8")) if post_data else {}
        except Exception:
            body = {}

        # 1. Одобрение карточки из очереди
        if parsed.path == "/api/approve":
            filename = body.get("filename")
            crop_x = body.get("crop_x", 50)
            crop_y = body.get("crop_y", 50)

            pending_items = read_json_file(PENDING_FILE)
            item = next((x for x in pending_items if (x.get("filename") == filename or x.get("file") == filename or Path(x.get("file", "")).name == filename)), None)

            if item:
                pending_items = [x for x in pending_items if x != item]
                write_json_file(PENDING_FILE, pending_items)

                actual_fn = item.get("filename") or Path(item.get("file", "")).name
                src = PENDING_DIR / actual_fn
                dst = IMAGES_DIR / actual_fn
                if src.exists():
                    shutil.move(src, dst)

                for key in ("title", "caption", "tag", "score"):
                    if key in body:
                        item[key] = body[key]

                item["file"] = f"images/{actual_fn}"
                item["filename"] = actual_fn
                item["crop_x"] = crop_x
                item["crop_y"] = crop_y

                data_items = read_json_file(DATA_FILE)
                data_items.insert(0, item)
                write_json_file(DATA_FILE, data_items)

                return self.send_json({"status": "success", "message": "Одобрено в состав"})

            return self.send_json({"status": "error", "message": "Элемент не найден"}, 404)

        # 2. Отклонение карточки в брак
        if parsed.path == "/api/reject":
            filename = body.get("filename")
            pending_items = read_json_file(PENDING_FILE)
            item = next((x for x in pending_items if (x.get("filename") == filename or x.get("file") == filename or Path(x.get("file", "")).name == filename)), None)

            if item:
                pending_items = [x for x in pending_items if x != item]
                write_json_file(PENDING_FILE, pending_items)

                actual_fn = item.get("filename") or Path(item.get("file", "")).name
                src = PENDING_DIR / actual_fn
                dst = DELETED_DIR / actual_fn
                if src.exists():
                    shutil.move(src, dst)

                return self.send_json({"status": "success", "message": "Отправлено в брак"})

            return self.send_json({"status": "error", "message": "Элемент не найден"}, 404)

        # 3. Обновление карточки (текст и кадрирование)
        if parsed.path == "/api/update-card":
            filename = body.get("filename")
            data_items = read_json_file(DATA_FILE)
            updated = False

            for item in data_items:
                cur_fn = item.get("filename") or Path(item.get("file", "")).name
                if cur_fn == filename or item.get("file") == filename:
                    if "title" in body:
                        item["title"] = body["title"]
                    if "tag" in body:
                        item["tag"] = body["tag"]
                    if "caption" in body:
                        item["caption"] = body["caption"]
                    if "crop_x" in body:
                        item["crop_x"] = body["crop_x"]
                    if "crop_y" in body:
                        item["crop_y"] = body["crop_y"]
                    updated = True
                    break

            if updated:
                write_json_file(DATA_FILE, data_items)

                req_id = body.get("request_id")
                if req_id:
                    reqs = read_json_file(REQUESTS_FILE)
                    reqs = [r for r in reqs if str(r.get("id")) != str(req_id)]
                    write_json_file(REQUESTS_FILE, reqs)

                return self.send_json({"status": "success", "message": "Карточка обновлена"})

            return self.send_json({"status": "error", "message": "Карточка не найдена в data.json"}, 404)

        # 4. Удаление карточки
        if parsed.path == "/api/delete-card":
            filename = body.get("filename")
            data_items = read_json_file(DATA_FILE)
            item = next((x for x in data_items if (x.get("filename") == filename or x.get("file") == filename or Path(x.get("file", "")).name == filename)), None)

            if item:
                data_items = [x for x in data_items if x != item]
                write_json_file(DATA_FILE, data_items)

                actual_fn = item.get("filename") or Path(item.get("file", "")).name
                src = IMAGES_DIR / actual_fn
                dst = DELETED_DIR / actual_fn
                if src.exists():
                    shutil.move(src, dst)

                req_id = body.get("request_id")
                if req_id:
                    reqs = read_json_file(REQUESTS_FILE)
                    reqs = [r for r in reqs if str(r.get("id")) != str(req_id)]
                    write_json_file(REQUESTS_FILE, reqs)

                return self.send_json({"status": "success", "message": "Карточка удалена с сайта"})

            return self.send_json({"status": "error", "message": "Карточка не найдена"}, 404)

        # 5. Снятие заявки с очереди
        if parsed.path == "/api/resolve-request":
            req_id = body.get("id")
            requests_items = read_json_file(REQUESTS_FILE)
            requests_items = [x for x in requests_items if str(x.get("id")) != str(req_id)]
            write_json_file(REQUESTS_FILE, requests_items)
            return self.send_json({"status": "success"})

        # 6. Сохранение и редактирование статей (включая флаг featured)
        if parsed.path == "/api/articles/save":
            art_id = body.get("id")
            title = body.get("title", "").strip()
            content = body.get("content", "").strip()
            tag = body.get("tag", "ВЕСТНИК").strip()
            author = body.get("author", "Редакция").strip()
            featured = bool(body.get("featured", False))
            date_str = body.get("date", time.strftime("%d.%m.%Y"))

            if not title or not content:
                return self.send_json({"status": "error", "message": "Заголовок и текст обязательны"}, 400)

            articles = read_json_file(ARTICLES_FILE)

            if art_id:
                for art in articles:
                    if str(art.get("id")) == str(art_id):
                        art["title"] = title
                        art["content"] = content
                        if "tag" in body and body["tag"]:
                            art["tag"] = tag
                        if "author" in body and body["author"]:
                            art["author"] = author
                        if "featured" in body:
                            art["featured"] = featured
                        break
            else:
                new_art = {
                    "id": f"art_{int(time.time())}",
                    "title": title,
                    "date": date_str,
                    "tag": tag,
                    "content": content,
                    "author": author,
                    "featured": featured
                }
                articles.insert(0, new_art)

            write_json_file(ARTICLES_FILE, articles)

            req_id = body.get("request_id")
            if req_id:
                reqs = read_json_file(REQUESTS_FILE)
                reqs = [r for r in reqs if str(r.get("id")) != str(req_id)]
                write_json_file(REQUESTS_FILE, reqs)

            return self.send_json({"status": "success", "message": "Статья сохранена"})

        # 7. Изменение порядка статей (вверх/вниз)
        if parsed.path == "/api/articles/reorder":
            if isinstance(body, list):
                write_json_file(ARTICLES_FILE, body)
                return self.send_json({"status": "success", "message": "Порядок статей сохранен"})
            return self.send_json({"status": "error", "message": "Ожидается список статей"}, 400)

        # 8. Удаление статьи
        if parsed.path == "/api/articles/delete":
            art_id = body.get("id")
            articles = read_json_file(ARTICLES_FILE)
            articles = [a for a in articles if str(a.get("id")) != str(art_id)]
            write_json_file(ARTICLES_FILE, articles)
            return self.send_json({"status": "success", "message": "Статья удалена"})

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