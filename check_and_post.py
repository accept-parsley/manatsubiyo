#!/usr/bin/env python3
"""
明治神宮野球場周辺の気温を気象庁アメダス（東京/大手町, 地点番号44132）から取得し、
その日(JST)に初めて閾値(25/30/35/40度)へ到達したタイミングでBlueskyに自動投稿する。

実行は GitHub Actions の cron (10分おき) を想定。
状態(state.json)はリポジトリにコミットして永続化する。

投稿先: Bluesky（AT Protocol）。完全無料・開発者登録不要。
"""

import json
import os
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests

BLUESKY_API_BASE = "https://bsky.social/xrpc"

# ===== 設定 =====
AMEDAS_POINT = "44132"  # 東京(大手町) ※神宮球場最寄りの気象庁公式観測点
STATE_FILE = "state.json"
JST = ZoneInfo("Asia/Tokyo")

# 優先度が高い順（threshold, level, text）
# level: 1=夏日 2=真夏日 3=猛暑日 4=酷暑日
THRESHOLDS = [
    (40.0, 4, "酷暑日よ"),
    (35.0, 3, "猛暑日よ"),
    (30.0, 2, "真夏日よ"),
    (25.0, 1, "夏日よ"),
]


def fetch_current_temp() -> float | None:
    """気象庁アメダスから現在の東京(大手町)の気温を取得する"""
    latest_resp = requests.get(
        "https://www.jma.go.jp/bosai/amedas/data/latest_time.txt", timeout=10
    )
    latest_resp.raise_for_status()
    latest_dt = datetime.fromisoformat(latest_resp.text.strip())
    time_key = latest_dt.strftime("%Y%m%d%H%M") + "00"

    map_url = f"https://www.jma.go.jp/bosai/amedas/data/map/{time_key}.json"
    map_resp = requests.get(map_url, timeout=10)
    map_resp.raise_for_status()
    data = map_resp.json()

    point_data = data.get(AMEDAS_POINT)
    if not point_data or "temp" not in point_data:
        return None

    value, quality = point_data["temp"]
    if value is None:
        return None
    return float(value)


def determine_level(temp: float) -> int:
    """気温から到達している最高レベルを返す（未到達なら0）"""
    for threshold, level, _text in THRESHOLDS:
        if temp >= threshold:
            return level
    return 0


def text_for_level(level: int) -> str:
    for _threshold, lv, text in THRESHOLDS:
        if lv == level:
            return text
    raise ValueError(f"unknown level: {level}")


def load_state() -> dict:
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"date": "", "max_posted_level": 0}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def post_to_bluesky(text: str) -> None:
    """AT Protocol (Bluesky) に1件投稿する"""
    identifier = os.environ["BLUESKY_IDENTIFIER"]
    app_password = os.environ["BLUESKY_APP_PASSWORD"]

    # 1. セッション作成（ログイン）
    session_resp = requests.post(
        f"{BLUESKY_API_BASE}/com.atproto.server.createSession",
        json={"identifier": identifier, "password": app_password},
        timeout=10,
    )
    session_resp.raise_for_status()
    session = session_resp.json()
    access_jwt = session["accessJwt"]
    did = session["did"]

    # 2. 投稿レコード作成
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    record = {
        "$type": "app.bsky.feed.post",
        "text": text,
        "createdAt": now_iso,
    }
    post_resp = requests.post(
        f"{BLUESKY_API_BASE}/com.atproto.repo.createRecord",
        headers={"Authorization": f"Bearer {access_jwt}"},
        json={"repo": did, "collection": "app.bsky.feed.post", "record": record},
        timeout=10,
    )
    post_resp.raise_for_status()


def main() -> None:
    today_str = datetime.now(JST).strftime("%Y-%m-%d")

    state = load_state()
    if state.get("date") != today_str:
        # 日付が変わっていたらリセット
        state = {"date": today_str, "max_posted_level": 0}

    temp = fetch_current_temp()
    if temp is None:
        print("気温データを取得できませんでした（欠測の可能性）。今回はスキップします。")
        sys.exit(0)

    print(f"[{datetime.now(JST).isoformat()}] 現在気温: {temp}°C / 本日の投稿済み最高レベル: {state['max_posted_level']}")

    target_level = determine_level(temp)

    if target_level > state["max_posted_level"]:
        text = text_for_level(target_level)
        print(f"新たにレベル{target_level}（{text}）に到達。投稿します。")
        post_to_bluesky(text)
        state["max_posted_level"] = target_level
        save_state(state)
        print("投稿・状態保存が完了しました。")
    else:
        print("新たな閾値到達はないため、投稿しません。")
        # 日付リセットだけ発生していた場合に備えて常に保存しておく
        save_state(state)


if __name__ == "__main__":
    main()
