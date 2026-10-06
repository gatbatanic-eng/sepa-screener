"""NHPLUG(NH투자증권 Open API) 조회 전용 점검. (python -m nhplug.probe)
시세 조회(/krstock/quote, /gbstock/quote)와 토큰 발급만 부른다. 주문·계좌 경로는 코드에 없다(허용 목록 밖은 호출 거부).
토큰·키는 로그와 결과 파일에 남기지 않는다. 결과: research/nhplug/probe/result.json"""
from __future__ import annotations

import datetime as dt
import json
import os
import time
from pathlib import Path

import requests

HOST = "https://api.nhplug.com:8443"
ALLOWED = ("/krstock/quote/", "/gbstock/quote/")
OUT = Path(__file__).resolve().parent.parent / "research" / "nhplug" / "probe" / "result.json"
GAP = 0.3  # 초당 4건 이하


def token() -> str:
    r = requests.post(f"{HOST}/oauth2/token", params={"appkey": os.environ["NHPLUG_APPKEY"], "appsecretkey": os.environ["NHPLUG_APPSECRET"],
                                                      "grant_type": "client_credentials", "scope": "oob"},
                      headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=30)
    r.raise_for_status()
    return r.json()["access_token"]


def call(tok: str, path: str, body: dict) -> dict:
    if not path.startswith(ALLOWED):
        raise ValueError(f"조회 전용 점검입니다: {path}")
    time.sleep(GAP)
    r = requests.post(f"{HOST}{path}", json={"Input_0": body}, headers={"Authorization": f"Bearer {tok}"}, timeout=30)
    try:
        data = r.json()
    except ValueError:
        data = {"raw": r.text[:300]}
    return {"http": r.status_code, "rsp_cd": data.get("rsp_cd"), "rsp_msg": data.get("rsp_msg"), "data": data}


def rows(res: dict, key="Output_1") -> list:
    v = (res.get("data") or {}).get(key)
    return v if isinstance(v, list) else ([v] if isinstance(v, dict) else [])


def brief(res: dict, keys=("Output_0", "Output_1")) -> dict:
    out = {"http": res["http"], "rsp_cd": res["rsp_cd"], "rsp_msg": res["rsp_msg"], "message": (res["data"] or {}).get("message")}
    for k in keys:
        v = (res["data"] or {}).get(k)
        if isinstance(v, list):
            out[k] = {"n": len(v), "first": v[:3], "last": v[-3:]}
        elif isinstance(v, dict):
            out[k] = {kk: vv for kk, vv in v.items() if kk in ("qry_date", "iem_cd", "iem_nm", "stck_prpr", "bstp_kor_isnm", "bstp_cls_code", "hts_avls",
                                                                   "per", "pbr", "date", "trdprc", "kor_name", "trading_cls", "list_amt", "bsop_date")}
    return out


def kr_period(tok, code, mkt, edate, n):
    return call(tok, "/krstock/quote/v1/period", {"market_cd": "KRX", "iem_cd": code, "mrkt_div_cls_code": mkt, "edate": edate, "array_cnt": f"{n:04d}",
                                                  "maxavg": "000", "gubun": "1", "xtick": "000", "today_cls_code": "0", "fake_tick": "1", "sur_flag": "0",
                                                  "sur_gb_day_cnt": "00", "sur_bf_end_time": "", "out1_scale_change": "0", "out2_scale_change": "0",
                                                  "view_main_yn": "Y"})


def us_period(tok, sym, edate, n):
    return call(tok, "/gbstock/quote/v1/period", {"iem_cd": sym, "end_dt": edate, "count": f"{n:04d}", "maxavg": "000", "gubun": "3", "xtick": "0001",
                                                   "today_cls": "0", "market_cls": "1"})


def yahoo_close(market: str, code: str, start: str):
    try:
        from ledger import prices
        if market == "kr":
            got, _ = prices.fetch_closes("kr", [code], start, {code: "KOSPI"})
        else:
            got, _ = prices.fetch_closes("us", [code], start)
        s = got.get(code)
        return None if s is None else {str(k)[:10].replace("-", ""): float(v) for k, v in s.items()}
    except Exception as e:  # noqa: BLE001 - 점검용: 실패 사유만 남긴다
        return {"error": repr(e)[:200]}


def main() -> None:
    tok = token()
    today = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d")
    res: dict = {"ranAt": dt.datetime.now(dt.timezone.utc).isoformat(), "calls": {}, "compare": {}}
    c = res["calls"]
    # 한국: 최근 일봉(깊이 확인), 액면분할 전후(삼성전자 2018-05-04 50:1), 코스닥 종목, 수급
    r = kr_period(tok, "005930", "1", today, 400); c["kr_recent_005930"] = brief(r); recent = rows(r)
    r = kr_period(tok, "005930", "1", "20180510", 10); c["kr_split_005930_20180510"] = brief(r)
    r = kr_period(tok, "000660", "1", today, 5); c["kr_recent_000660"] = brief(r)
    r = kr_period(tok, "247540", "4", today, 5); c["kr_kosdaq_247540"] = brief(r)
    r = call(tok, "/krstock/quote/v1/currentInvestor", {"market_cd": "KRX", "iem_cd": "005930", "array_cnt": "030"}); c["kr_investor_005930"] = brief(r, ("Output_0",))
    # 미국: 최근 일봉(깊이), 분할 전후(NVDA 2024-06-10 10:1), 다른 종목
    r = us_period(tok, "AAPL", today, 400); c["us_recent_AAPL"] = brief(r); us_recent = rows(r)
    r = us_period(tok, "NVDA", "20240614", 10); c["us_split_NVDA_20240614"] = brief(r)
    r = us_period(tok, "TSLA", today, 5); c["us_recent_TSLA"] = brief(r)
    r = call(tok, "/gbstock/quote/v1/symbolIndexFxPeriod", {"iem_cd": "SPX", "end_dt": today, "array_cnt": "0005", "maxavg": "000", "gubun": "1", "xtick": "001",
                                                            "today_cls": "0", "scale_change": "0"}); c["us_index_SPX"] = brief(r)
    # Yahoo 종가와 비교: 같은 날짜의 차이율
    kr = {x.get("bsop_date"): x.get("stck_prpr") for x in recent}
    us = {x.get("trade_date"): x.get("close_prc") for x in us_recent}
    for m, code, nh in (("kr", "005930", kr), ("us", "AAPL", us)):
        if not nh:
            res["compare"][code] = {"error": "NHPLUG 일봉 없음"}
            continue
        start = f"{min(nh)[:4]}-{min(nh)[4:6]}-{min(nh)[6:]}"
        y = yahoo_close(m, code, start)
        if not y or "error" in y:
            res["compare"][code] = {"yahoo": y}
            continue
        diffs = []
        for d, v in nh.items():
            try:
                if d in y:
                    diffs.append(round((float(v) / y[d] - 1) * 100, 3))
            except (TypeError, ValueError):
                pass
        res["compare"][code] = {"n": len(diffs), "maxAbsDiffPct": max((abs(x) for x in diffs), default=None),
                                "sample": diffs[-5:], "nhDates": [min(nh), max(nh)]}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print("저장:", OUT)
    for k, v in c.items():
        print(k, v["http"], v["rsp_cd"], v["rsp_msg"])


if __name__ == "__main__":
    main()
