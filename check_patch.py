"""
check_patch.py
---------------
① EA公式サイト と fpsjp.net(EAA FPS News) の両方を見に行く
② 前回チェック時と比べて、それぞれ新しい記事があるか確認する
③ 新しければ本文を取得し、Gemini APIに投げて構造化データに変換する
   (パッチ内容でない記事だった場合は何もしない)
④ apex-data.json の先頭に追記する
"""

import os
import re
import json
import time
import datetime
import requests
from google import genai

# 情報源を2つ用意。EA公式は一次情報、fpsjp.netはより詳しい解説・
# イベント/コラボ情報も拾える二次情報として使う。
SOURCES = [
    {
        "name": "ea_official",
        "list_url": "https://www.ea.com/ja/games/apex-legends/apex-legends/news",
        "link_pattern": r'href="(/ja/games/apex-legends/apex-legends/news/[^"]+)"',
        "base_url": "https://www.ea.com",
    },
    {
        "name": "fpsjp",
        "list_url": "https://fpsjp.net/archives/category/apex-legends",
        "link_pattern": r'href="(https://fpsjp\.net/archives/\d+)"',
        "base_url": "",  # fpsjpのリンクは元からフルURL
    },
]

LAST_SEEN_FILE = "last_seen.json"      # ソースごとに前回確認した記事URLを覚えておくファイル
DATA_FILE = "apex-data.json"           # サイトが表示に使っているデータファイル

MODEL_NAME = "gemini-flash-latest"
FALLBACK_MODEL = "gemini-2.0-flash"


# ---------- ① 各ソースの一覧ページから最新記事リンクを拾う ----------
def fetch_latest_article_url(source):
    res = requests.get(source["list_url"], timeout=20)
    res.raise_for_status()
    links = re.findall(source["link_pattern"], res.text)
    if not links:
        return None
    return source["base_url"] + links[0]


# ---------- ② ソースごとの前回との差分をチェック ----------
def load_last_seen():
    if os.path.exists(LAST_SEEN_FILE):
        with open(LAST_SEEN_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_last_seen(last_seen):
    with open(LAST_SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(last_seen, f, ensure_ascii=False)


# ---------- 記事本文を取得 ----------
def fetch_article_text(url):
    res = requests.get(url, timeout=20)
    res.raise_for_status()
    text = re.sub(r"<script.*?</script>", "", res.text, flags=re.S)
    text = re.sub(r"<style.*?</style>", "", text, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text[:8000]


# ---------- ③ Geminiで構造化データに変換 ----------
def convert_with_gemini(patch_text, source_name):
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    prompt = f"""
以下はApex Legendsに関するニュースサイトの記事本文です({source_name}より取得)。
この記事から読み取れる情報を、次のJSON形式の**オブジェクト1つ**として出力してください。
説明文やコードブロック記号は不要です。JSONオブジェクトだけを返してください。

{{
  "patches": [
    {{"target": "キャラ名または武器名", "type": "buff", "magnitude": 1, "text": "変更内容を日本語で簡潔に"}}
  ],
  "events": [
    {{"name": "イベント名", "kind": "コラボ", "start": "2026-09-16", "end": "", "desc": "内容を2〜3文で"}}
  ]
}}

ルール:
- "patches" には、レジェンド・武器のバフ/ナーフ/調整など、バランス変更のみを入れる。
  type は buff / nerf / change / new のいずれか。
  magnitude は buff/nerf のときだけ 1〜3 の整数で、変化の大きさの目安。
- "events" には、期間限定イベント・他作品とのコラボ・実店舗連動キャンペーンなど、
  開催期間があるイベント情報を入れる。
  kind は「コラボ」「期間限定イベント」「中間アップデート」「リアルコラボ」「リアル連動」のいずれかに近いものを選ぶ。
  start/end は分かる範囲でYYYY-MM-DD形式(不明ならendは空文字)。
- 記事にどちらの情報も含まれない場合は、該当する配列を空にする（[]）。
- 両方とも空配列になる場合もある(不具合修正のみ・大会結果・グッズ紹介など無関係な記事の場合)。

本文:
{patch_text}
"""
    response = generate_with_retry(client, prompt)
    raw = response.text.strip()
    raw = re.sub(r"^```json|```$", "", raw, flags=re.M).strip()
    parsed = json.loads(raw)
    return parsed.get("patches", []), parsed.get("events", [])


def generate_with_retry(client, prompt, max_tries=3):
    """Geminiが一時的に混雑している(503)ときのために、
    少し待って複数回リトライする。それでもダメなら予備モデルに切り替える。"""
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


# ---------- ④ apex-data.json に追記 ----------
def append_to_data_file(patch_lines, event_list, source_url, source_name):
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = {"patches": [], "events": []}
    data.setdefault("events", [])

    today = datetime.date.today().isoformat()

    if patch_lines:
        data["patches"].insert(0, {
            "ver": "最新アップデート",
            "date": today,
            "source": source_url,
            "source_name": source_name,
            "lines": patch_lines,
        })

    for ev in event_list:
        ev = dict(ev)
        ev.setdefault("start", today)
        ev["source"] = source_url
        data["events"].insert(0, ev)

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def main():
    last_seen = load_last_seen()
    any_update = False

    for source in SOURCES:
        name = source["name"]
        latest_url = fetch_latest_article_url(source)
        if not latest_url:
            print(f"[{name}] 記事が見つかりませんでした")
            continue

        if latest_url == last_seen.get(name):
            print(f"[{name}] 新しい記事はありません")
            continue

        print(f"[{name}] 新しい記事を検知: {latest_url}")
        text = fetch_article_text(latest_url)
        patch_lines, event_list = convert_with_gemini(text, name)

        if patch_lines or event_list:
            append_to_data_file(patch_lines, event_list, latest_url, name)
            print(f"[{name}] apex-data.json を更新しました(パッチ{len(patch_lines)}件 / イベント{len(event_list)}件)")
            any_update = True
        else:
            print(f"[{name}] バランス変更・イベント情報のどちらも含まれていなかったため、反映をスキップしました")

        # 内容の有無にかかわらず、この記事は「確認済み」として記録する
        last_seen[name] = latest_url

    save_last_seen(last_seen)
    if not any_update:
        print("今回はサイトへの反映はありませんでした")


if __name__ == "__main__":
    main()
