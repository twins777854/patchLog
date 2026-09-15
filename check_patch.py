"""
check_patch.py
---------------
① EA公式のApexパッチノート一覧ページを見に行く
② 前回チェック時と比べて新しい記事があるか確認する
③ 新しければ本文を取得し、Gemini APIに投げて構造化データに変換する
④ apex-data.json の先頭に追記する

このファイルは「まず動く形」のスケルトンです。
実際に使う前に、サイトのHTML構造が変わっていないか一度確認してから
調整してください(EA公式サイトは見た目がたまに変わります)。
"""

import os
import re
import json
import requests
import google.generativeai as genai

NEWS_LIST_URL = "https://www.ea.com/ja/games/apex-legends/apex-legends/news"
LAST_SEEN_FILE = "last_seen.json"      # 前回確認した記事URLを覚えておくファイル
DATA_FILE = "apex-data.json"           # サイトが表示に使っているデータファイル

# ---------- ① 一覧ページを取得し、記事リンクを拾う ----------
def fetch_latest_article_url():
    res = requests.get(NEWS_LIST_URL, timeout=20)
    res.raise_for_status()
    html = res.text

    # ページ内の記事リンクを正規表現でざっくり拾う(本来はBeautifulSoupで丁寧にやるのが望ましい)
    links = re.findall(r'href="(/ja/games/apex-legends/apex-legends/news/[^"]+)"', html)
    if not links:
        return None
    # 一覧の一番上にある記事が最新、という前提
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
    # 簡易的にタグを除去して本文だけにする(実運用ではBeautifulSoupの方が安全)
    text = re.sub(r"<script.*?</script>", "", res.text, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text[:8000]  # 長すぎるとAPIコストが増えるので適当な長さで切る


# ---------- ③ Geminiで構造化データに変換 ----------
def convert_with_gemini(patch_text, patch_url):
    genai.configure(api_key=os.environ["GEMINI_API_KEY"])
    model = genai.GenerativeModel("gemini-3-flash")

    prompt = f"""
以下はApex Legendsの新しいパッチノートのページから抜き出した本文です。
レジェンド・武器ごとの変更点を、次のJSON形式の配列だけで出力してください。
説明文やコードブロック記号(```)は不要です。JSON配列だけを返してください。

[
  {{"target": "キャラ名または武器名", "type": "buff", "magnitude": 1, "text": "変更内容を日本語で簡潔に"}},
  ...
]

type は buff / nerf / change / new のいずれか。
magnitude は buff/nerf のときだけ 1〜3 の整数で、変化の大きさの目安。

本文:
{patch_text}
"""
    response = model.generate_content(prompt)
    raw = response.text.strip()
    raw = re.sub(r"^```json|```$", "", raw, flags=re.M).strip()
    return json.loads(raw)


# ---------- ④ apex-data.json に追記 ----------
def append_to_data_file(lines, source_url):
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = {"patches": []}

    new_patch = {
        "ver": "自動検知パッチ",
        "date": "",  # 本来は記事から日付も抽出する
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
