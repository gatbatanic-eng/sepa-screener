"""골든코드 순위 경로 점검(조회 전용). python -m goldencode.probe_rankings --output DIR
네이버 금융 순위 후보 엔드포인트를 호출해 상태, 응답 구조, 표본 행, 응답 시각을 기록한다.
어떤 경로가 실제로 되는지 확인하는 용도이며, 매매 판단에 쓰지 않는다."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import time
from pathlib import Path

import requests

KST = dt.timezone(dt.timedelta(hours=9))
M = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1",
     "Referer": "https://m.stock.naver.com/"}
D = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
     "Referer": "https://finance.naver.com/"}
MOBILE = ["marketValue", "up", "down", "quantHigh", "quant", "tradingValue", "tradingValueHigh", "amount",
          "priceTop", "popular", "searchTop", "volumeSurge", "highPrice", "high52week"]
DESKTOP = ["sise_quant.naver", "sise_rise.naver", "sise_fall.naver", "sise_quant_high.naver",
           "lastsearch2.naver", "sise_high_up.naver", "sise_market_sum.naver"]


def rows_shape(obj, depth=0):
    """코드가 든 가장 큰 dict 배열의 행 수, 키, 앞 3행."""
    best = []

    def walk(o):
        nonlocal best
        if isinstance(o, list):
            rs = [x for x in o if isinstance(x, dict)]
            if rs and len(rs) > len(best):
                best = rs
            for x in o:
                walk(x)
        elif isinstance(o, dict):
            for v in o.values():
                walk(v)
    walk(obj)
    return {"rows": len(best), "keys": sorted(best[0].keys()) if best else [], "sample": best[:3]}


def probe(url, headers, desktop=False):
    t = time.time()
    out = {"url": url}
    try:
        r = requests.get(url, headers=headers, timeout=20)
        out.update(http=r.status_code, seconds=round(time.time() - t, 2), serverDate=r.headers.get("Date"),
                   contentType=r.headers.get("Content-Type"), bytes=len(r.content))
        if desktop:
            html = r.content.decode("euc-kr", "replace")
            codes = re.findall(r"code=(\d{6})", html)
            out.update(codeCount=len(set(codes)), firstCodes=list(dict.fromkeys(codes))[:10],
                       tableHeader=re.findall(r"<th[^>]*>(.*?)</th>", html, re.S)[:16],
                       asOf=re.findall(r"(\d{4}\.\d{2}\.\d{2}\s*\d{2}:\d{2}|\d{2}/\d{2}/\d{2}\s*\d{2}:\d{2})", html)[:3])
        elif r.status_code == 200:
            try:
                out["shape"] = rows_shape(r.json())
            except ValueError:
                out["head"] = r.text[:300]
        else:
            out["head"] = r.text[:200]
    except requests.RequestException as e:
        out["error"] = type(e).__name__
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", type=Path, required=True)
    a = ap.parse_args()
    results = []
    for market in ("KOSPI", "KOSDAQ"):
        for name in MOBILE:
            results.append(probe(f"https://m.stock.naver.com/api/stocks/{name}/{market}?page=1&pageSize=30", M))
            time.sleep(0.3)
    for sosok in (0, 1):
        for page in DESKTOP:
            results.append(probe(f"https://finance.naver.com/sise/{page}?sosok={sosok}", D, desktop=True))
            time.sleep(0.3)
    snap = {"generatedAt": dt.datetime.now(KST).isoformat(), "results": results}
    folder = a.output / "research" / "goldencode" / "probe"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "rankings.json").write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
    for r in results:
        s = r.get("shape", {})
        print(r.get("http"), r.get("error", ""), s.get("rows", r.get("codeCount", "")), r["url"][:90])


if __name__ == "__main__":
    main()
