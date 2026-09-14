# -*- coding: utf-8 -*-
"""Auto-update US indicators in the AI liquidity dashboard from FRED (no API key needed).

Automatable from FRED:
  liq    = Fed 淨流動性 4 週變動% = (WALCL/1000 − WTREGEN − RRPONTSYD) 的 28 日變動率
  spread = ICE BofA AA 美國公司債 OAS（科技巨頭均為 AA/A 評級，以 AA 指數作代理）

其餘 10 項（VC 融資、模型公司募資、CapEx 指引、財報數據、GPU 報價等）
無官方公開 API，維持手動更新，不勉強用不對口的資料源。
"""
import re, sys
from pathlib import Path
from datetime import date, timedelta
import requests

DASHBOARD = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(r"C:\Users\ed249\Downloads\美國 AI 資本流動性儀表板 v2.html")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

def fred_series(sid, days=120):
    """Fetch FRED series as {date: float} over the past `days` days（統一換算為十億美元）。"""
    end = date.today()
    start = end - timedelta(days=days)
    url = (f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}"
           f"&cosd={start.isoformat()}&coed={end.isoformat()}")
    r = None
    for attempt in range(4):  # 雲端主機偶發逾時，重試 3 次
        try:
            r = requests.get(url, headers=UA, timeout=60)
            r.raise_for_status()
            break
        except Exception as e:
            print(f"  {sid} 第 {attempt + 1} 次抓取失敗: {e}")
            r = None
    if r is None:
        raise RuntimeError(f"FRED {sid} 多次重試仍失敗")
    out = {}
    for line in r.text.strip().splitlines()[1:]:
        ds, _, val = line.partition(",")
        val = val.strip().strip('"')
        if val and val != ".":
            try:
                out[date.fromisoformat(ds.strip())] = float(val)
            except ValueError:
                pass
    # 單位自動校正：FRED 部分序列實際以百萬美元遞送（如 WTREGEN 出現 883,335）
    if out and sorted(out.values())[len(out) // 2] > 100000:
        out = {d: v / 1000 for d, v in out.items()}
    return out

def net_liquidity():
    """NL = WALCL(百萬→十億) − TGA − ON RRP；回傳 (最新日期, 4週變動%)"""
    walcl = fred_series("WALCL")      # 百萬美元
    tga = fred_series("WTREGEN")      # 十億美元
    rrp = fred_series("RRPONTSYD")    # 十億美元
    if not (walcl and tga and rrp):
        raise RuntimeError("FRED 資料抓取不完整")
    all_dates = sorted(set(walcl) | set(tga) | set(rrp))
    series = []
    for d in all_dates:
        # 各自取「該日或之前最新值」往前填充
        w = max((k for k in walcl if k <= d), default=None)
        t = max((k for k in tga if k <= d), default=None)
        rr = max((k for k in rrp if k <= d), default=None)
        if w and t and rr:
            # fred_series 已統一換算為十億美元，直接用
            series.append((d, walcl[w] - tga[t] - rrp[rr]))
    latest_d, latest_v = series[-1]
    ref_d = latest_d - timedelta(days=28)
    ref = [v for d, v in series if d <= ref_d]
    if not ref:
        raise RuntimeError("28 日前參考值不足")
    chg = (latest_v - ref[-1]) / abs(ref[-1]) * 100
    return latest_d, chg

def aa_spread():
    """ICE BofA AA US Corporate OAS 最新值；FRED 單位為百分比，換算為 bp"""
    s = fred_series("BAMLC0A2CAA", days=30)
    if not s:
        raise RuntimeError("AA spread 抓取失敗")
    d = max(s)
    return d, s[d] * 100  # 0.6% → 60bp

def patch(html, iid, new_v, new_note):
    """替換 IND 內指定 id 那一列的 v: 值與整段 note（整段置換，避免重複疊加）"""
    pat = re.compile(r"(\{id:'" + iid + r"'[^\n]*?v:)-?[\d.]+(,\s*red)", re.S)
    m = pat.search(html)
    assert m, f"indicator {iid} not found"
    v_txt = f"{new_v:.1f}" if abs(new_v) < 100 else f"{new_v:.0f}"
    html = pat.sub(lambda m: m.group(1) + v_txt + m.group(2), html, count=1)
    npat = re.compile(r"(id:'" + iid + r"'[^\n]*?note:)'[^']*('\})", re.S)
    html = npat.sub(lambda m: m.group(1) + "'" + new_note + m.group(2), html, count=1)
    return html

def main():
    liq_d, liq_chg = net_liquidity()
    sp_d, sp_v = aa_spread()
    asof = max(liq_d, sp_d)
    asof_fmt = f"{asof.year}/{asof.month:02d}/{asof.day:02d}"

    html = DASHBOARD.read_text(encoding="utf-8")
    html = patch(html, "liq", liq_chg, f"紅：連續收縮 < −2%（FRED 自動更新 {asof_fmt}）")
    html = patch(html, "spread", sp_v,
                 f"紅：> 130bp 且 CDS 同步走升（ICE BofA AA 指數代理，FRED 自動更新 {asof_fmt}）")
    DASHBOARD.write_text(html, encoding="utf-8")
    print(f"liq 4週變動 = {liq_chg:.2f}%（{liq_d}）")
    print(f"AA spread   = {sp_v:.0f}bp（{sp_d}）")
    print(f"Patched {DASHBOARD.name}: US FRED data as of {asof_fmt}")

if __name__ == "__main__":
    main()
