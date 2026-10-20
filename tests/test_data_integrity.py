import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

def test_data_json():
    data_file = DATA_DIR / "data.json"
    assert data_file.exists()
    with open(data_file, "r", encoding="utf-8") as f:
        items = json.load(f)
    assert isinstance(items, list)
    for item in items:
        assert "file" in item
        assert "title" in item

def test_articles_json():
    articles_file = DATA_DIR / "articles.json"
    assert articles_file.exists()
    with open(articles_file, "r", encoding="utf-8") as f:
        articles = json.load(f)
    assert isinstance(articles, list)

def test_model_version():
    curator_file = BASE_DIR / "scripts" / "auto_curator.py"
    if not curator_file.exists():
        curator_file = BASE_DIR / "auto_curator.py"
    with open(curator_file, "r", encoding="utf-8") as f:
        content = f.read()
    assert "gemini-3.8-flash" in content

    example_env = BASE_DIR / ".env.example"
    with open(example_env, "r", encoding="utf-8") as f:
        env_content = f.read()
    assert "GEMINI_MODEL=gemini-3.8-flash" in env_content

def test_prompt_configuration():
    example_prompt = BASE_DIR / "prompt.example.txt"
    assert example_prompt.exists(), "prompt.example.txt должен существовать для репозитория на GitHub"
    with open(example_prompt, "r", encoding="utf-8") as f:
        content = f.read()

    # Проверка структуры JSON ответа
    assert '"score":' in content
    assert '"title":' in content
    assert '"caption":' in content
    assert '"tag":' in content

    # Проверка отсутствия приватных замечаний и локальных шуток в публичном шаблоне
    for private_term in ["ГазМяс", "Базанов", "Дибуны", "Шугар Раш", "Дирик Платон"]:
        assert private_term not in content, f"Публичный prompt.example.txt не должен содержать локальное замечание '{private_term}'"

def test_html_pages_in_root():
    index_file = BASE_DIR / "index.html"
    admin_file = BASE_DIR / "admin.html"
    assert index_file.exists(), "index.html должен находиться в корне проекта"
    assert admin_file.exists(), "admin.html должен находиться в корне проекта"

    with open(index_file, "r", encoding="utf-8") as f:
        index_content = f.read()
    assert "../images/symbol.png" not in index_content, "index.html не должен ссылаться на ../images/symbol.png"
    assert "../data/data.json" not in index_content, "index.html не должен ссылаться на ../data/data.json"
    assert "images/symbol.png" in index_content
    assert "data/data.json" in index_content

    with open(admin_file, "r", encoding="utf-8") as f:
        admin_content = f.read()
    assert "../images/symbol.png" not in admin_content, "admin.html не должен ссылаться на ../images/symbol.png"
    assert "images/symbol.png" in admin_content

    pages_dir = BASE_DIR / "pages"
    assert not pages_dir.exists() or not any(pages_dir.glob("*.html")), "В каталоге pages не должно оставаться HTML-файлов"

if __name__ == "__main__":
    test_data_json()
    test_articles_json()
    test_model_version()
    test_prompt_configuration()
    test_html_pages_in_root()
    print("All tests passed successfully!")
