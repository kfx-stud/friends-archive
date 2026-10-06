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
    curator_file = BASE_DIR / "auto_curator.py"
    with open(curator_file, "r", encoding="utf-8") as f:
        content = f.read()
    assert "gemini-3.8-flash" in content

    example_env = BASE_DIR / ".env.example"
    with open(example_env, "r", encoding="utf-8") as f:
        env_content = f.read()
    assert "GEMINI_MODEL=gemini-3.8-flash" in env_content

if __name__ == "__main__":
    test_data_json()
    test_articles_json()
    test_model_version()
    print("All tests passed successfully!")
