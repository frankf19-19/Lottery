# -*- coding: utf-8 -*-
"""
彩研所 TWLottery Lab — 開獎資料自動更新腳本
BUILD_VERSION = v3.6.0

資料來源:台灣彩券官方網站 API(api.taiwanlottery.com)
執行方式:由 GitHub Actions 排程呼叫(每日台灣時間 21:35),
        亦可手動執行:python scripts/update_data.py

v3.6.0(依 Actions log 確認之官方格式接通):
  - 今彩539 端點修正為 Daily539Result(原 DailyCashResult 為 404)
  - 獎金分配改由月份 API 內嵌的 *Assign 欄位解析(jackpotAssign / super638JackpotAssign 等)
  - 既有資料缺獎金時自動全量回補升級

行為:
  1. 首次執行(data/*.json 不存在或為種子)→ 自動回補 BACKFILL_MONTHS 個月歷史
  2. 日常執行 → 只抓本月與上月,與既有資料以「期別」去重合併
  3. 對最近 PRIZE_LOOKBACK 期中尚無獎金資料者,逐期查詢獎金分配
  4. 任一遊戲抓取失敗不影響其他遊戲(各自 try/except)
"""

import json
import os
import sys
import time
import datetime as dt

import requests

BUILD_VERSION = "v3.6.0"
API_BASE = "https://api.taiwanlottery.com/TLCAPIWeB/Lottery/{endpoint}"
BACKFILL_MONTHS = 14   # 首次回補的月數
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) TWLotteryLab/1.1",
    "Accept": "application/json",
}

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
        "endpoint": "Daily539Result",
        "picks": 5,
        "has_special": False,
    },
}

# ---------- 通用工具 ----------

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


def api_get(endpoint, params):
    url = API_BASE.format(endpoint=endpoint)
    r = requests.get(url, params=params, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()


def fetch_month(endpoint, month):
    """抓取單一遊戲單一月份的開獎資料,回傳 API 原始項目 list。"""
    payload = api_get(endpoint, {"period": "", "month": month, "pageNum": 1, "pageSize": 50})
    content = payload.get("content") or {}
    # 官方回應內的清單鍵名依遊戲不同,取 content 中第一個「list 型別」的值。
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
        prizes = extract_assign_prizes(item)
        if prizes:
            draw["prizes"] = prizes
        # 銷售/總額等純數值金額欄位(排除 *Assign 巢狀欄位)
        money = {}
        for k, v in item.items():
            lk = str(k).lower()
            if lk.endswith("assign"):
                continue
            if any(h in lk for h in ("amount", "money", "sales", "jackpot", "bonus")):
                try:
                    money[str(k)] = int(float(v))
                except (ValueError, TypeError):
                    continue
        if money:
            draw["money"] = money
        return draw
    except (ValueError, TypeError):
        return None


# 官方月份 API 內嵌的獎金分配欄位(鍵名結尾),依獎項順序排列
TIER_SUFFIXES = [
    ("jackpotassign", "頭獎"), ("secondassign", "貳獎"), ("thirdassign", "參獎"),
    ("fourthassign", "肆獎"), ("fifthassign", "伍獎"), ("sixthassign", "陸獎"),
    ("seventhassign", "柒獎"), ("eighthassign", "捌獎"), ("ninthassign", "玖獎"),
    ("normalassign", "普獎"),
]


def _to_int(v):
    try:
        return int(float(v))
    except (ValueError, TypeError):
        return None


def extract_assign_prizes(item):
    """解析官方回應內嵌的 *Assign 獎金分配欄位(依 2026-07 Actions log 確認之格式)。
    每個 Assign 為 {"prize": 本期分配額, "lastPrize": 前期累積, "winnerCount": 注數, "perPrize": 每注獎金}
    大樂透為 jackpotAssign...normalAssign;威力彩為 super638JackpotAssign...;539 同型。"""
    rows = []
    lower_map = {str(k).lower(): k for k in item}
    for suffix, name in TIER_SUFFIXES:
        for lk, orig in lower_map.items():
            if lk.endswith(suffix):
                a = item.get(orig)
                if isinstance(a, dict):
                    al = {str(k).lower(): v for k, v in a.items()}
                    rows.append({
                        "name": name,
                        "winners": _to_int(al.get("winnercount")),
                        "amount": _to_int(al.get("perprize")),
                        "pool": _to_int(al.get("prize")),
                        "carry": _to_int(al.get("lastprize")),
                    })
                break
    return rows if rows else None


# ---------- 主流程 ----------

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
    lacks_prizes = not any(d.get("prizes") for d in old_draws[:10])
    first_run = len(old_draws) == 0 or is_seed or lacks_prizes

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
        if items and month == months[-1]:
            print(f"[{cfg['name']}] 官方欄位範例:{sorted(items[0].keys())}")
            raw0 = json.dumps(items[0], ensure_ascii=False)
            print(f"[{cfg['name']}] 最新項目原始內容:{raw0[:800]}")
        for item in items:
            draw = normalize(item, cfg)
            if draw:
                prev = merged.get(draw["period"])
                if prev and prev.get("prizes") and not draw.get("prizes"):
                    draw["prizes"] = prev["prizes"]  # 新抓不到時保留既有獎金資料
                merged[draw["period"]] = draw
                fetched += 1
        time.sleep(0.6)  # 對官方伺服器保持禮貌

    draws = sorted(merged.values(), key=lambda d: (d["date"], d["period"]), reverse=True)
    if not draws:
        print(f"[{cfg['name']}] 無資料可寫入,略過。", file=sys.stderr)
        return False

    # 資料內容無變動就不改寫檔案:密集排程下避免只因時間戳而產生無意義 commit
    if not first_run and json.dumps(draws, sort_keys=True, ensure_ascii=False) == json.dumps(
        old_draws, sort_keys=True, ensure_ascii=False
    ):
        print(f"[{cfg['name']}] 資料無變動(累計 {len(draws)} 期),略過寫檔。")
        return True

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
    with_prize = sum(1 for d in draws if d.get("prizes"))
    print(f"[{cfg['name']}] 完成,累計 {len(draws)} 期(本次處理 {fetched} 筆;{with_prize} 期含獎金資料)。")
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

    sys.exit(0 if ok > 0 else 1)


if __name__ == "__main__":
    main()
