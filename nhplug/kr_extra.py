"""한국 종목 보조 데이터(업종·시가총액·PER/PBR·외국인/기관 수급) 수집. (python -m nhplug.kr_extra)
조회 전용(nhplug.probe의 허용 목록을 그대로 쓴다). SEPA 규칙·에이전트 계좌에는 연결하지 않고 research/nhplug/kr_extra.json에 쌓기만 한다.
수급 필드 뜻(frgn_ntby_qty vs invest)은 명세 설명이 모호해, 네이버 투자자 동향과 대조한 결과를 research/nhplug/probe/investor_check.json에 남긴다."""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import requests

from .probe import call, kr_period, rows, token

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "research" / "nhplug" / "kr_extra.json"
CHECK = ROOT / "research" / "nhplug" / "probe" / "investor_check.json"
CHECK_CODES = ("005930", "000660", "247540", "035420", "005380")


def _i(v):
    try:
        return int(float(str(v).replace(",", "").replace("+", "")))
    except (TypeError, ValueError):
        return None


def _f(v):
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def universe() -> list[tuple[str, str]]:
    rows_ = json.loads((ROOT / "docs" / "data" / "latest_kr.json").read_text(encoding="utf-8"))
    return [(r["code"], "4" if r.get("market") == "KOSDAQ" else "1") for r in rows_ if r.get("code")]


def investor(tok: str, code: str) -> list[dict]:
    r = call(tok, "/krstock/quote/v1/currentInvestor", {"market_cd": "KRX", "iem_cd": code, "array_cnt": "030"})
    return rows(r, "Output_0")


def summarize_flow(inv: list[dict]) -> dict:
    """최근순 정렬 가정(첫 행이 최신). 5·20거래일 합계를 두 후보 필드 모두 남긴다."""
    out = {"days": len(inv)}
    for key, name in (("frgn_ntby_qty", "frgnA"), ("invest", "frgnB"), ("gigwan", "inst"), ("person", "indiv")):
        vals = [_i(x.get(key)) for x in inv]
        for n in (5, 20):
            part = [v for v in vals[:n] if v is not None]
            out[f"{name}{n}"] = sum(part) if len(part) == n else None
    return out


def naver_trend(code: str) -> dict[str, dict]:
    """네이버 모바일 투자자 동향(일자→외국인/기관/개인 순매매량). 대조용이라 실패하면 빈 값."""
    try:
        r = requests.get(f"https://m.stock.naver.com/api/stock/{code}/trend", timeout=20,
                         headers={"User-Agent": "Mozilla/5.0", "Referer": "https://m.stock.naver.com/"})
        r.raise_for_status()
        return {x["bizdate"]: {"foreign": _i(x.get("foreignerPureBuyQuant")), "organ": _i(x.get("organPureBuyQuant")),
                               "person": _i(x.get("individualPureBuyQuant"))} for x in r.json()}
    except Exception as e:  # noqa: BLE001 - 대조용
        return {"error": repr(e)[:200]}


def check_fields(tok: str) -> dict:
    res = {}
    for code in CHECK_CODES:
        inv, nv = investor(tok, code), None
        nv = naver_trend(code)
        if "error" in nv:
            res[code] = nv
            continue
        stat = {"days": 0, "frgnA==naver": 0, "frgnB==naver": 0, "gigwan==naverOrgan": 0, "person==naverPerson": 0}
        samples = []
        for x in inv:
            n = nv.get(str(x.get("bsop_date1")))
            if not n:
                continue
            stat["days"] += 1
            stat["frgnA==naver"] += _i(x.get("frgn_ntby_qty")) == n["foreign"]
            stat["frgnB==naver"] += _i(x.get("invest")) == n["foreign"]
            stat["gigwan==naverOrgan"] += _i(x.get("gigwan")) == n["organ"]
            stat["person==naverPerson"] += _i(x.get("person")) == n["person"]
            if len(samples) < 3:
                samples.append({"date": x.get("bsop_date1"), "nh": {k: x.get(k) for k in ("frgn_ntby_qty", "invest", "gigwan", "person")}, "naver": n})
        res[code] = {**stat, "samples": samples}
    return res


def main() -> None:
    tok = token()
    CHECK.parent.mkdir(parents=True, exist_ok=True)
    CHECK.write_text(json.dumps({"ranAt": dt.datetime.now(dt.timezone.utc).isoformat(), "result": check_fields(tok)}, ensure_ascii=False, indent=1), encoding="utf-8")
    stocks, errors, last = {}, 0, ""
    for i, (code, mk) in enumerate(universe()):
        try:
            p = kr_period(tok, code, mk, dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d"), 1)
            o0 = ((p["data"] or {}).get("Output_0")) or {}
            if isinstance(o0, list):
                o0 = o0[0] if o0 else {}
            inv = investor(tok, code)
            d = str(o0.get("bsop_date") or "")
            last = max(last, d)
            stocks[code] = {"name": str(o0.get("iem_nm") or "").lstrip("*#"), "sector": o0.get("bstp_kor_isnm"), "sectorCode": o0.get("bstp_cls_code"),
                            "marcapEok": _i(o0.get("hts_avls")), "per": _f(o0.get("per")), "pbr": _f(o0.get("pbr")), "asOf": d,
                            "forRate": _f(inv[0].get("for_rate")) if inv else None, "flow": summarize_flow(inv)}
        except Exception as e:  # noqa: BLE001 - 종목 하나 실패가 전체를 막지 않게
            errors += 1
            stocks[code] = {"error": repr(e)[:120]}
        if i % 100 == 0:
            print(f"{i}/{len(universe())} 오류 {errors}", flush=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"schemaVersion": 1, "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(), "asOf": last,
                               "note": "frgnA=frgn_ntby_qty, frgnB=invest: 어느 쪽이 외국인인지 investor_check.json으로 확정 전까지 사용 금지",
                               "stocks": stocks}, ensure_ascii=False), encoding="utf-8")
    print("저장:", OUT, "종목", len(stocks), "오류", errors)
    sys.exit(0 if errors < len(stocks) * 0.2 else 1)


if __name__ == "__main__":
    main()
