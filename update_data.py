# -*- coding: utf-8 -*-
"""
彩研所 TWLottery Lab — 開獎資料自動更新腳本
BUILD_VERSION = v1.0.0

資料來源:台灣彩券官方網站 API(api.taiwanlottery.com)
執行方式:由 GitHub Actions 排程呼叫(每日台灣時間 21:35),
        亦可手動執行:python scripts/update_data.py

行為:
  1. 首次執行(data/*.json 不存在或無資料)→ 自動回補 BACKFILL_MONTHS 個月歷史
  2. 日常執行 → 只抓本月與上月,與既有資料以「期別」去重合併
  3. 任一遊戲抓取失敗不影響其他遊戲(各自 try/except)
"""

import json
import os
import sys
import time
import datetime as dt

import requests

BUILD_VERSION = "v1.0.0"
API_BASE = "https://api.taiwanlottery.com/TLCAPIWeB/Lottery/{endpoint}"
BACKFILL_MONTHS = 14  # 首次回補的月數
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) TWLotteryLab/1.0",
    "Accept": "application/json",
}

# picks = 正選球數;has_special:
#   lotto649      → 特別號(同池第 7 球)
#   superlotto638 → 第二區號碼(獨立 1–8)
#   dailycash539  → 無
GAMES = {
    "lotto649": {
        "name": "大樂透",
        "endpoint": "Lotto649Result",
        "picks": 6,
        "has_special": True,
    },
    "superlotto638": {
        "name": "威力彩",
        "endpoint": "SuperLotto638Result",
        "picks": 6,
        "has_special": True,
    },
    "dailycash539": {
        "name": "今彩539",
        "endpoint": "DailyCashResult",
        "picks": 5,
        "has_special": False,
    },
}


def month_list(n_months):
    """回傳最近 n_months 個月(含本月),格式 YYYY-MM,由舊到新。"""
    today = dt.date.today()
    months = []
    y, m = today.year, today.month
    for _ in range(n_months):
        months.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return list(reversed(months))


def fetch_month(endpoint, month):
    """抓取單一遊戲單一月份的開獎資料,回傳 API 原始項目 list。"""
    url = API_BASE.format(endpoint=endpoint)
    params = {"period": "", "month": month, "pageNum": 1, "pageSize": 50}
    r = requests.get(url, params=params, headers=HEADERS, timeout=30)
    r.raise_for_status()
    payload = r.json()
    content = payload.get("content") or {}
    # 官方回應內的清單鍵名依遊戲不同(如 lotto649Res / superLotto638Res / dailyCashRes),
    # 為避免鍵名變動造成中斷,取 content 中第一個「list 型別」的值。
    for value in content.values():
        if isinstance(value, list):
            return value
    return []


def normalize(item, cfg):
    """把官方單筆資料轉成本站統一格式;格式不符時回傳 None。"""
    try:
        period = str(item.get("period") or item.get("Period") or "").strip()
        date_raw = str(item.get("lotteryDate") or item.get("LotteryDate") or "")
        date = date_raw[:10]  # YYYY-MM-DD
        appear = item.get("drawNumberAppear") or item.get("DrawNumberAppear") or []
        size = item.get("drawNumberSize") or item.get("DrawNumberSize") or []
        appear = [int(x) for x in appear]
        size = [int(x) for x in size]
        picks = cfg["picks"]
        need = picks + (1 if cfg["has_special"] else 0)
        if not period or not date or len(appear) < need:
            return None
        base = size[:picks] if len(size) >= picks else sorted(appear[:picks])
        draw = {
            "period": period,
            "date": date,
            "numbers": sorted(base),
            "raw": appear[:need],  # 開出順序(含特別號/第二區)
        }
        if cfg["has_special"]:
            draw["special"] = appear[need - 1]
        return draw
    except (ValueError, TypeError):
        return None


def load_existing(path):
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def update_game(key, cfg):
    path = os.path.join(DATA_DIR, f"{key}.json")
    existing = load_existing(path)
    old_draws = (existing or {}).get("draws") or []
    is_seed = bool((existing or {}).get("seed"))
    first_run = len(old_draws) == 0 or is_seed

    months = month_list(BACKFILL_MONTHS if first_run else 2)
    mode = "回補" if first_run else "增量"
    print(f"[{cfg['name']}] {mode}更新,月份範圍:{months[0]} ~ {months[-1]}")

    merged = {d["period"]: d for d in old_draws if d.get("period")}
    fetched = 0
    for month in months:
        try:
            items = fetch_month(cfg["endpoint"], month)
        except requests.RequestException as e:
            print(f"[{cfg['name']}] {month} 抓取失敗:{e}", file=sys.stderr)
            continue
        for item in items:
            draw = normalize(item, cfg)
            if draw:
                merged[draw["period"]] = draw
                fetched += 1
        time.sleep(0.6)  # 對官方伺服器保持禮貌

    draws = sorted(merged.values(), key=lambda d: (d["date"], d["period"]), reverse=True)
    if not draws:
        print(f"[{cfg['name']}] 無資料可寫入,略過。", file=sys.stderr)
        return False

    out = {
        "game": key,
        "name": cfg["name"],
        "build": BUILD_VERSION,
        "updated": dt.datetime.now(dt.timezone.utc).astimezone(
            dt.timezone(dt.timedelta(hours=8))
        ).strftime("%Y-%m-%d %H:%M:%S +08:00"),
        "count": len(draws),
        "draws": draws,
    }
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"[{cfg['name']}] 完成,累計 {len(draws)} 期(本次處理 {fetched} 筆)。")
    return True


def main():
    print(f"彩研所資料更新腳本 {BUILD_VERSION}")
    ok = 0
    for key, cfg in GAMES.items():
        try:
            if update_game(key, cfg):
                ok += 1
        except Exception as e:  # 單一遊戲失敗不中斷整體
            print(f"[{cfg['name']}] 未預期錯誤:{e}", file=sys.stderr)
    print(f"完成:{ok}/{len(GAMES)} 個遊戲更新成功。")
    # 只要有任一成功即視為成功,避免單一 API 異常讓整個 workflow 標紅
    sys.exit(0 if ok > 0 else 1)


if __name__ == "__main__":
    main()
