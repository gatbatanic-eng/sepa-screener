"""한국 투자자별 순매수의 장기 이력을 받을 수 있는 소스가 있는지 점검한다(조회 전용, 키 없음). (python -m accumulation.flow_probe)
후보: pykrx(KRX 종목별 투자자별 거래대금), 네이버 모바일 투자자 동향(페이지 크기 확대). 결과: research/accumulation/flow_probe.json"""
from __future__ import annotations

import datetime as dt
import json
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "research" / "accumulation" / "flow_probe.json"
CODES = ("005930", "000660", "247540")
START = "20240901"


def probe_pykrx() -> dict:
    res: dict = {}
    try:
        from pykrx import stock
        import pykrx
        res["version"] = getattr(pykrx, "__version__", "?")
    except Exception as e:  # noqa: BLE001
        return {"importError": repr(e)[:300]}
    today = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d")
    for code in CODES:
        r: dict = {}
        for name, fn in (("tradingValueByDate", lambda c: stock.get_market_trading_value_by_date(START, today, c)),
                         ("tradingVolumeByDate", lambda c: stock.get_market_trading_volume_by_date(START, today, c))):
            try:
                df = fn(code)
                r[name] = {"rows": int(len(df)), "columns": [str(c) for c in df.columns],
                           "first": str(df.index[0])[:10] if len(df) else None, "last": str(df.index[-1])[:10] if len(df) else None,
                           "tail": json.loads(df.tail(3).to_json(orient="index", force_ascii=False, date_format="iso")) if len(df) else None}
            except Exception as e:  # noqa: BLE001
                r[name] = {"error": repr(e)[:300], "trace": traceback.format_exc()[-400:]}
        res[code] = r
    return res


def probe_naver() -> dict:
    import requests
    res: dict = {}
    for code in CODES[:1]:
        for q in ("", "?pageSize=500", "?page=2"):
            try:
                r = requests.get(f"https://m.stock.naver.com/api/stock/{code}/trend{q}", timeout=20,
                                 headers={"User-Agent": "Mozilla/5.0", "Referer": "https://m.stock.naver.com/"})
                d = r.json() if r.ok else None
                res[f"{code}{q}"] = {"http": r.status_code, "rows": len(d) if isinstance(d, list) else None,
                                     "first": d[0].get("bizdate") if isinstance(d, list) and d else None, "last": d[-1].get("bizdate") if isinstance(d, list) and d else None}
            except Exception as e:  # noqa: BLE001
                res[f"{code}{q}"] = {"error": repr(e)[:200]}
    return res


def main() -> None:
    out = {"ranAt": dt.datetime.now(dt.timezone.utc).isoformat(), "pykrx": probe_pykrx(), "naver": probe_naver()}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1, default=str)[:4000])


if __name__ == "__main__":
    main()
