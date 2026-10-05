import os
import sys
import json
import time
import shutil
import threading
import urllib.request
import urllib.error
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
IMAGES_DIR = os.path.join(BASE_DIR, "images")
PENDING_DIR = os.path.join(BASE_DIR, "pending")
DELETED_DIR = os.path.join(BASE_DIR, "deleted")

DATA_FILE = os.path.join(DATA_DIR, "data.json")
PENDING_FILE = os.path.join(DATA_DIR, "pending.json")
REQUESTS_FILE = os.path.join(DATA_DIR, "requests.json")
ARTICLES_FILE = os.path.join(DATA_DIR, "articles.json")

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"

for d in [DATA_DIR, IMAGES_DIR, PENDING_DIR, DELETED_DIR]:
    os.makedirs(d, exist_ok=True)

def load_env_file():
    env_path = os.path.join(BASE_DIR, ".env")
    if os.path.exists(env_path):
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        os.environ[k.strip()] = v.strip().strip("'\"")
        except Exception as e:
            print(f"[ВНИМАНИЕ] Ошибка парсинга .env: {e}")

load_env_file()

JSONBIN_BIN_ID = os.environ.get("JSONBIN_BIN_ID", "")
JSONBIN_API_KEY = os.environ.get("JSONBIN_API_KEY", "")

if not JSONBIN_BIN_ID or not JSONBIN_API_KEY:
    print("[ПРЕДУПРЕЖДЕНИЕ] Переменные JSONBIN_BIN_ID или JSONBIN_API_KEY не найдены в .env!")

JSON_LOCK = threading.Lock()

def read_json_file(path, default=None):
    if default is None:
        default = []
    with JSON_LOCK:
        if not os.path.exists(path):
            return default
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default

def write_json_file(path, data):
    temp_path = path + ".tmp"
    with JSON_LOCK:
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(temp_path, path)

def sync_with_jsonbin():
    bin_id = os.environ.get("JSONBIN_BIN_ID", "")
    api_key = os.environ.get("JSONBIN_API_KEY", "")

    if not bin_id or not api_key:
        return 0

    url_latest = f"https://api.jsonbin.io/v3/b/{bin_id}/latest"
    headers = {
        "X-Master-Key": api_key,
        "X-Access-Key": api_key,
        "User-Agent": USER_AGENT,
        "Accept": "application/json"
    }
    
    req = urllib.request.Request(url_latest, headers=headers)

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status != 200:
                print(f"[ОБЛАКО] Статус ответа JSONBin: HTTP {resp.status}")
                return 0
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="ignore")
        print(f"[ОБЛАКО] Ошибка доступа к JSONBin: HTTP {e.code} – {err_body}")
        return 0
    except Exception as e:
        print(f"[ОБЛАКО] Сетевая ошибка при обращении к JSONBin: {e}")
        return 0

    record = body.get("record", {})
    queue = record.get("queue", []) if isinstance(record, dict) else (record if isinstance(record, list) else [])

    if not queue:
        return 0

    local_reqs = read_json_file(REQUESTS_FILE, [])
    existing_ids = {r.get("id") for r in local_reqs if isinstance(r, dict)}

    new_count = 0
    for item in queue:
        if isinstance(item, dict) and item.get("id") not in existing_ids:
            local_reqs.insert(0, item)
            existing_ids.add(item.get("id"))
            new_count += 1

    if new_count > 0:
        write_json_file(REQUESTS_FILE, local_reqs)
        print(f"[ОБЛАКО] Добавлено заявок: {new_count}. Очистка удаленной очереди...")

        url_put = f"https://api.jsonbin.io/v3/b/{bin_id}"
        put_data = json.dumps({"queue": []}).encode("utf-8")
        put_req = urllib.request.Request(
            url_put,
            data=put_data,
            headers={
                "Content-Type": "application/json",
                "X-Master-Key": api_key,
                "X-Access-Key": api_key,
                "User-Agent": USER_AGENT,
                "Accept": "application/json"
            },
            method="PUT"
        )
        try:
            with urllib.request.urlopen(put_req, timeout=10) as put_resp:
                if put_resp.status == 200:
                    print("[ОБЛАКО] Очередь в JSONBin сброшена.")
        except Exception as e:
            print(f"[ОБЛАКО] Сбой сброса очереди в JSONBin: {e}")

    return new_count

def background_sync_worker():
    print("[ОБЛАКО] Запущен фоновый опрос с интервалом 25 секунд")
    while True:
        try:
            sync_with_jsonbin()
        except Exception as e:
            print(f"[ОБЛАКО] Ошибка потока синхронизации: {e}")
        time.sleep(25)

def get_clean_pending():
    pending = read_json_file(PENDING_FILE, [])
    valid_pending = []
    dirty = False
    for item in pending:
        fn = os.path.basename(item.get("filename", "") or item.get("file", ""))
        if fn and os.path.exists(os.path.join(PENDING_DIR, fn)):
            valid_pending.append(item)
        else:
            dirty = True
    if dirty:
        write_json_file(PENDING_FILE, valid_pending)
    return valid_pending

class AdminHandler(SimpleHTTPRequestHandler):
    def send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == "/api/config":
            return self.send_json({
                "JSONBIN_BIN_ID": os.environ.get("JSONBIN_BIN_ID", ""),
                "JSONBIN_API_KEY": os.environ.get("JSONBIN_API_KEY", "")
            })

        if parsed.path == "/api/sync-cloud":
            added = sync_with_jsonbin()
            reqs = read_json_file(REQUESTS_FILE, [])
            return self.send_json({"status": "success", "added": added, "requests": reqs})

        if parsed.path == "/api/pending":
            return self.send_json(get_clean_pending())

        if parsed.path == "/api/requests":
            return self.send_json(read_json_file(REQUESTS_FILE, []))

        if parsed.path == "/api/articles":
            return self.send_json(read_json_file(ARTICLES_FILE, []))

        if parsed.path == "/api/data":
            return self.send_json(read_json_file(DATA_FILE, []))

        super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        content_length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"

        try:
            body = json.loads(raw_body)
        except Exception:
            return self.send_json({"status": "error", "message": "Некорректный JSON"}, 400)

        if parsed.path == "/api/approve":
            filename = os.path.basename(body.get("filename", "") or body.get("file", ""))
            if not filename:
                return self.send_json({"status": "error", "message": "Имя файла не указано"}, 400)

            src = os.path.join(PENDING_DIR, filename)
            dst = os.path.join(IMAGES_DIR, filename)

            if os.path.exists(src):
                shutil.move(src, dst)

            pending = read_json_file(PENDING_FILE, [])
            item = next((x for x in pending if x.get("filename") == filename or os.path.basename(x.get("file", "")) == filename), None)
            pending = [x for x in pending if x.get("filename") != filename and os.path.basename(x.get("file", "")) != filename]
            pending = [x for x in pending if os.path.exists(os.path.join(PENDING_DIR, os.path.basename(x.get("filename", "") or x.get("file", ""))))]
            write_json_file(PENDING_FILE, pending)

            data = read_json_file(DATA_FILE, [])
            data = [c for c in data if os.path.basename(c.get("file", "") or c.get("filename", "")) != filename]
            card = {
                "id": (item.get("id") if item else None) or filename.split("@")[0].replace("photo_", ""),
                "file": f"images/{filename}",
                "filename": filename,
                "title": body.get("title", item.get("title", "") if item else ""),
                "caption": body.get("caption", item.get("caption", "") if item else ""),
                "tag": body.get("tag", item.get("tag", "ОСНОВА") if item else "ОСНОВА"),
                "score": item.get("score", 8) if item else 8,
                "date": item.get("date", time.strftime("%Y-%m-%d")) if item else time.strftime("%Y-%m-%d"),
                "crop_x": body.get("crop_x", 50),
                "crop_y": body.get("crop_y", 50)
            }
            if item and item.get("sha256"):
                card["sha256"] = item["sha256"]
            data.insert(0, card)
            write_json_file(DATA_FILE, data)
            return self.send_json({"status": "success", "card": card})

        if parsed.path == "/api/reject":
            filename = os.path.basename(body.get("filename", "") or body.get("file", ""))
            if not filename:
                return self.send_json({"status": "error", "message": "Имя файла не указано"}, 400)

            src = os.path.join(PENDING_DIR, filename)
            dst = os.path.join(DELETED_DIR, filename)

            if os.path.exists(src):
                shutil.move(src, dst)

            pending = [x for x in read_json_file(PENDING_FILE, []) if x.get("filename") != filename and os.path.basename(x.get("file", "")) != filename]
            pending = [x for x in pending if os.path.exists(os.path.join(PENDING_DIR, os.path.basename(x.get("filename", "") or x.get("file", ""))))]
            write_json_file(PENDING_FILE, pending)
            return self.send_json({"status": "success"})

        if parsed.path == "/api/update-card":
            raw_fn = body.get("filename") or body.get("file") or ""
            filename = os.path.basename(raw_fn)

            data = read_json_file(DATA_FILE, [])
            found = False
            for c in data:
                c_fn = os.path.basename(c.get("file", "") or c.get("filename", ""))
                if c_fn == filename:
                    c["title"] = body.get("title", c.get("title"))
                    c["caption"] = body.get("caption", c.get("caption"))
                    c["tag"] = body.get("tag", c.get("tag"))
                    if "crop_x" in body:
                        c["crop_x"] = body["crop_x"]
                    if "crop_y" in body:
                        c["crop_y"] = body["crop_y"]
                    found = True
                    break

            if found:
                write_json_file(DATA_FILE, data)

            req_id = body.get("request_id")
            if req_id:
                reqs = [r for r in read_json_file(REQUESTS_FILE, []) if r.get("id") != req_id]
                write_json_file(REQUESTS_FILE, reqs)

            return self.send_json({"status": "success"})

        if parsed.path == "/api/delete-card":
            raw_fn = body.get("filename") or body.get("file") or ""
            filename = os.path.basename(raw_fn)

            data = [c for c in read_json_file(DATA_FILE, []) if os.path.basename(c.get("file", "") or c.get("filename", "")) != filename]
            write_json_file(DATA_FILE, data)

            src = os.path.join(IMAGES_DIR, filename)
            dst = os.path.join(DELETED_DIR, filename)
            if os.path.exists(src):
                shutil.move(src, dst)

            req_id = body.get("request_id")
            if req_id:
                reqs = [r for r in read_json_file(REQUESTS_FILE, []) if r.get("id") != req_id]
                write_json_file(REQUESTS_FILE, reqs)

            return self.send_json({"status": "success"})

        if parsed.path == "/api/articles/save":
            art_id = body.get("id")
            title = body.get("title", "").strip()
            content = body.get("content", "").strip()
            tag = body.get("tag", "ВЕСТНИК").strip()
            author = body.get("author", "Редакция").strip()
            featured = bool(body.get("featured", False))
            highlight_color = body.get("highlight_color", "#facc15")
            date_str = body.get("date", time.strftime("%d.%m.%Y"))

            if not title or not content:
                return self.send_json({"status": "error", "message": "Заголовок и текст обязательны"}, 400)

            articles = read_json_file(ARTICLES_FILE, [])
            if art_id:
                for a in articles:
                    if a.get("id") == art_id:
                        a["title"] = title
                        a["content"] = content
                        if "tag" in body and body["tag"]: a["tag"] = tag
                        if "author" in body and body["author"]: a["author"] = author
                        if "featured" in body: a["featured"] = featured
                        if "highlight_color" in body: a["highlight_color"] = highlight_color
                        break
            else:
                new_art = {
                    "id": f"art_{int(time.time())}",
                    "title": title,
                    "date": date_str,
                    "tag": tag,
                    "author": author,
                    "content": content,
                    "featured": featured,
                    "highlight_color": highlight_color
                }
                articles.insert(0, new_art)

            write_json_file(ARTICLES_FILE, articles)

            req_id = body.get("request_id")
            if req_id:
                reqs = [r for r in read_json_file(REQUESTS_FILE, []) if r.get("id") != req_id]
                write_json_file(REQUESTS_FILE, reqs)

            return self.send_json({"status": "success"})

        if parsed.path == "/api/articles/delete":
            art_id = body.get("id")
            articles = [a for a in read_json_file(ARTICLES_FILE, []) if a.get("id") != art_id]
            write_json_file(ARTICLES_FILE, articles)
            return self.send_json({"status": "success"})

        if parsed.path == "/api/articles/reorder":
            if isinstance(body, list):
                write_json_file(ARTICLES_FILE, body)
                return self.send_json({"status": "success"})
            return self.send_json({"status": "error", "message": "Ожидается список"}, 400)

        if parsed.path == "/api/resolve-request":
            req_id = body.get("id")
            reqs = [r for r in read_json_file(REQUESTS_FILE, []) if r.get("id") != req_id]
            write_json_file(REQUESTS_FILE, reqs)
            return self.send_json({"status": "success"})

        self.send_response(404)
        self.end_headers()

if __name__ == "__main__":
    port = 8080
    threading.Thread(target=background_sync_worker, daemon=True).start()
    
    server = HTTPServer(("127.0.0.1", port), AdminHandler)
    print("=" * 56)
    print(f"[ШТАБ МОДЕРАЦИИ] Сервер запущен на http://127.0.0.1:{port}/admin.html")
    print("=" * 56)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановка сервера...")
        server.server_close()