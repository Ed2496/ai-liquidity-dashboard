# -*- coding: utf-8 -*-
"""Auto-update TW foreign-investor data in the AI liquidity dashboard.

Source: TWSE official daily 三大法人買賣超 (T86), 外資 = 外陸資(不含外資自營商) + 外資自營商.
Units: dashboard uses 張 (1 張 = 1000 股).
Patches the `const TW = [...]` block and the 資料截至 note in the dashboard HTML.
Idempotent; keeps a one-time .bak of the original.
"""
import os, re, json, sys, time, shutil
from pathlib import Path
from datetime import date, timedelta
import requests

# 用法：python update_tw.py [dashboard.html] [output.json]
#   無參數 = 本機模式（更新 Downloads 下的儀表板 + E:\liquidity-tool\tw_foreign_data.json）
#   有參數 = CI/雲端模式（更新指定 HTML，JSON 寫在指定路徑）
DASHBOARD = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(r"C:\Users\ed249\Downloads\美國 AI 資本流動性儀表板 v2.html")
DATA_JSON = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(r"E:\liquidity-tool\tw_foreign_data.json")
STOCKS = {"2330": "台積電", "2454": "聯發科", "2308": "台達電"}
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

def fetch_day(d: str):
    """Return {stock: net_shares_foreign} for date str YYYYMMDD, or None if no data."""
    try:
        r = requests.get(f"https://www.twse.com.tw/rwd/zh/fund/T86?date={d}&selectType=ALL",
                         headers=UA, timeout=25)
        j = r.json()
    except Exception as e:
        print(f"  {d} fetch error: {e}")
        return None
    rows = j.get("data")
    if not rows:
        return None
    out = {}
    for row in rows:
        code = row[0].strip()
        if code in STOCKS:
            try:
                f1 = int(row[4].replace(",", ""))  # 外陸資買賣超(不含外資自營商)
                f2 = int(row[7].replace(",", ""))  # 外資自營商買賣超
                out[code] = f1 + f2
            except Exception:
                pass
    return out if len(out) == len(STOCKS) else None

def main():
    # collect trading days backwards until we have 60 with data (max 95 calendar days)
    daily = {}  # date -> {code: shares}
    d = date.today()
    tried = 0
    while len(daily) < 62 and tried < 95:
        d -= timedelta(days=1)
        tried += 1
        if d.weekday() >= 5:
            continue
        ds = d.strftime("%Y%m%d")
        got = fetch_day(ds)
        if got:
            daily[ds] = got
            print(f"  {ds} OK (have {len(daily)} days)")
        else:
            print(f"  {ds} 無資料（假日或尚未公佈）")
        time.sleep(0.6)

    if len(daily) < 60:
        print(f"WARNING: only {len(daily)} trading days collected (<60)")

    days_sorted = sorted(daily.keys(), reverse=True)
    def sum_window(n):
        window = days_sorted[:n]
        missing = n - len(window)
        return ({c: round(sum(daily[ds][c] for ds in window) / 1000) for c in STOCKS},
                window, missing)

    d10, w10, _ = sum_window(10)
    d30, w30, _ = sum_window(30)
    d60, w60, m60 = sum_window(60)

    asof = days_sorted[0]
    asof_fmt = f"{asof[:4]}/{asof[4:6]}/{asof[6:]}"

    result = {
        "asOf": asof_fmt,
        "source": "TWSE 官方 三大法人買賣超日報（外陸資不含自營 + 外資自營商）",
        "unit": "張",
        "windows": {
            "d10": {"range": f"{w10[-1]}~{w10[0]}", "values": d10},
            "d30": {"range": f"{w30[-1]}~{w30[0]}", "values": d30},
            "d60": {"range": f"{w60[-1]}~{w60[0]}", "values": d60, "missing_days": m60},
        },
        "trading_days_collected": len(daily),
    }
    DATA_JSON.parent.mkdir(parents=True, exist_ok=True)
    DATA_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(result["windows"], ensure_ascii=False, indent=1))

    # patch dashboard
    html = DASHBOARD.read_text(encoding="utf-8")
    if not os.environ.get("CI"):  # 雲端模式不留下 .bak
        bak = DASHBOARD.with_suffix(DASHBOARD.suffix + ".bak")
        if not bak.exists():
            shutil.copy2(DASHBOARD, bak)
            print("backup ->", bak)

    tw_block = "const TW = [\n" + ",\n".join(
        f" {{n:'{STOCKS[c]} {c}', d10:{d10[c]}, d30:{d30[c]}, d60:{d60[c]}, warn:'官方證交所資料 {asof_fmt} 更新，60日窗完整'}}"
        for c in STOCKS
    ) + "\n];"
    new_html, n = re.subn(r"const TW = \[.*?\];", tw_block, html, count=1, flags=re.S)
    assert n == 1, "TW block not found"
    new_html, n2 = re.subn(
        r"資料截至 [0-9/]+（[^）]*）",
        f"資料截至 {asof_fmt}（證交所官方三大法人日報自動更新；10/30/60日外資買賣超，單位：張）",
        new_html, count=1)
    assert n2 == 1, "date note not found"
    DASHBOARD.write_text(new_html, encoding="utf-8")
    print(f"\nPatched {DASHBOARD.name}: TW data as of {asof_fmt}")

if __name__ == "__main__":
    main()
