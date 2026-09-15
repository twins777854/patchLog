"""
check_patch.py
---------------
① EA公式のApexパッチノート一覧ページを見に行く
② 前回チェック時と比べて新しい記事があるか確認する
③ 新しければ本文を取得し、Gemini APIに投げて構造化データに変換する
④ apex-data.json の先頭に追記する
"""

import os
import re
import json
import time
import requests
from google import genai

NEWS_LIST_URL = "https://www.ea.com/ja/games/apex-legends/apex-legends/news"
LAST_SEEN_FILE = "last_seen.json"      # 前回確認した記事URLを覚えておくファイル
DATA_FILE = "apex-data.json"           # サイトが表示に使っているデータファイル

# 使うモデル名。Googleのモデル名は更新が早いので、
# もしまた「not found」エラーが出た場合は
# https://aistudio.google.com/ の一覧で使えるモデル名を確認して、
# ここを差し替えてください。
MODEL_NAME = "gemini-flash-latest"


# ---------- ① 一覧ページを取得し、記事リンクを拾う ----------
def fetch_latest_article_url():
    res = requests.get(NEWS_LIST_URL, timeout=20)
    res.raise_for_status()
    html = res.text

    links = re.findall(r'href="(/ja/games/apex-legends/apex-legends/news/[^"]+)"', html)
    if not links:
        return None
    return "https://www.ea.com" + links[0]


# ---------- ② 前回との差分をチェック ----------
def load_last_seen():
    if os.path.exists(LAST_SEEN_FILE):
        with open(LAST_SEEN_FILE, encoding="utf-8") as f:
            return json.load(f).get("url")
    return None


def save_last_seen(url):
    with open(LAST_SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump({"url": url}, f, ensure_ascii=False)


# ---------- 記事本文を取得 ----------
def fetch_article_text(url):
    res = requests.get(url, timeout=20)
    res.raise_for_status()
    text = re.sub(r"<script.*?</script>", "", res.text, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text[:8000]


# ---------- ③ Geminiで構造化データに変換(新SDK: google-genai) ----------
def convert_with_gemini(patch_text, patch_url):
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    prompt = f"""
以下はApex Legendsの新しいパッチノートのページから抜き出した本文です。
レジェンド・武器ごとの変更点を、次のJSON形式の配列だけで出力してください。
説明文やコードブロック記号(```)は不要です。JSON配列だけを返してください。

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


def generate_with_retry(client, prompt, max_tries=4):
    """Geminiが一時的に混雑している(503)ときのために、
    少し待って複数回リトライする。"""
    for attempt in range(1, max_tries + 1):
        try:
            return client.models.generate_content(
                model=MODEL_NAME,
                contents=prompt,
            )
        except Exception as e:
            is_last = attempt == max_tries
            print(f"Gemini呼び出し失敗(試行{attempt}/{max_tries}): {e}")
            if is_last:
                raise
            wait_seconds = 15 * attempt  # 15秒, 30秒, 45秒...と待ち時間を延ばす
            print(f"{wait_seconds}秒待って再試行します")
            time.sleep(wait_seconds)


# ---------- ④ apex-data.json に追記 ----------
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
