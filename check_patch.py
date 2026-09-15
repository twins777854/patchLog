import os
import re
import json
import time
import requests
from google import genai

NEWS_LIST_URL = "https://www.ea.com/ja/games/apex-legends/apex-legends/news"
LAST_SEEN_FILE = "last_seen.json"
DATA_FILE = "apex-data.json"
MODEL_NAME = "gemini-flash-latest"
FALLBACK_MODEL = "gemini-2.0-flash"


def fetch_latest_article_url():
    res = requests.get(NEWS_LIST_URL, timeout=20)
    res.raise_for_status()
    html = res.text
    links = re.findall(r'href="(/ja/games/apex-legends/apex-legends/news/[^"]+)"', html)
    if not links:
        return None
    return "https://www.ea.com" + links[0]


def load_last_seen():
    if os.path.exists(LAST_SEEN_FILE):
        with open(LAST_SEEN_FILE, encoding="utf-8") as f:
            return json.load(f).get("url")
    return None


def save_last_seen(url):
    with open(LAST_SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump({"url": url}, f, ensure_ascii=False)


def fetch_article_text(url):
    res = requests.get(url, timeout=20)
    res.raise_for_status()
    text = re.sub(r"<script.*?</script>", "", res.text, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text[:8000]


def generate_with_retry(client, prompt, max_tries=3):
    for model_name in [MODEL_NAME, FALLBACK_MODEL]:
        for attempt in range(1, max_tries + 1):
            try:
                return client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                )
            except Exception as e:
                print(f"{model_name} 呼び出し失敗(試行{attempt}/{max_tries}): {e}")
                if attempt != max_tries:
                    wait_seconds = 15 * attempt
                    print(f"{wait_seconds}秒待って再試行します")
                    time.sleep(wait_seconds)
        print(f"{model_name} は諦めて次のモデルを試します")
    raise RuntimeError("すべてのモデルで生成に失敗しました")


def convert_with_gemini(patch_text, patch_url):
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    prompt = f"""以下はApex Legendsの新しいパッチノートのページから抜き出した本文です。
レジェンド・武器ごとの変更点を、次のJSON形式の配列だけで出力してください。
説明文やコードブロック記号は不要です。JSON配列だけを返してください。

[
  {{"target": "キャラ名または武器名", "type": "buff", "magnitude": 1, "text": "変更内容を日本語で簡潔に"}}
]

type は buff / nerf / change / new のいずれか。
magnitude は buff/nerf のときだけ 1〜3 の整数で、変化の大きさの目安。

本文:
{patch_text}
"""
    response = generate_with_retry(client, prompt)
    raw = response.text.strip()
    raw = re.sub(r"^```json|```$", "", raw, flags=re.M).strip()
    return json.loads(raw)


def append_to_data_file(lines, source_url):
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = {"patches": []}

    new_patch = {
        "ver": "自動検知パッチ",
        "date": "",
        "source": source_url,
        "lines": lines,
    }
    data["patches"].insert(0, new_patch)

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def main():
    latest_url = fetch_latest_article_url()
    if not latest_url:
        print("記事が見つかりませんでした")
        return

    if latest_url == load_last_seen():
        print("新しいパッチはありません")
        return

    print(f"新しい記事を検知: {latest_url}")
    text = fetch_article_text(latest_url)
    lines = convert_with_gemini(text, latest_url)
    append_to_data_file(lines, latest_url)
    save_last_seen(latest_url)
    print("apex-data.json を更新しました")


if __name__ == "__main__":
    main()
