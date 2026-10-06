"""한국 종목 보조 데이터 수집(조회 전용: nhplug.probe의 허용 목록을 그대로 쓴다). (python -m nhplug.kr_extra --mode auto|full|flow)
full: 전종목 업종·시가총액·PER/PBR → research/nhplug/kr_extra.json (주 1회, 약 10분)
flow: 후보·보유 종목의 외국인·기관·개인 순매수 5·20거래일 합계 → research/nhplug/kr_flow.json (매일, 수 분)
수급 필드 대조(2026-10-06, 네이버 동향과 5종목×10일): 기관(gigwan)·개인(person)은 완전 일치, 외국인은 invest가 근접(소폭 차이), frgn_ntby_qty는 다른 값.
그래서 외국인은 invest를 방향·대략의 크기로만 쓴다."""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import requests

from .probe import call, kr_period, rows, token

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "research" / "nhplug" / "kr_extra.json"
FLOW_OUT = ROOT / "research" / "nhplug" / "kr_flow.json"
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


def summarize_flow(inv: list[dict], kst_today: str | None = None) -> dict:
    """최근순 정렬 가정(첫 행이 최신). 오늘(KST) 날짜 행은 장 마감 직후 집계 전일 수 있어 뺀다.
    frgn=invest(외국인, 근사), inst=gigwan(기관), indiv=person(개인). 5·20거래일 순매수 수량 합계."""
    if kst_today:
        inv = [x for x in inv if str(x.get("bsop_date1")) != kst_today]
    out = {"days": len(inv), "asOf": str(inv[0].get("bsop_date1")) if inv else None}
    for key, name in (("invest", "frgn"), ("gigwan", "inst"), ("person", "indiv")):
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


def candidates() -> list[str]:
    """매일 수급을 받을 종목: SEPA 조건 6개 이상 충족 또는 READY/BREAKOUT_ZONE, 에이전트 권고 포트폴리오의 한국 종목, 계좌복구 상위 종목."""
    codes: set[str] = set()
    for r in json.loads((ROOT / "docs" / "data" / "latest_kr.json").read_text(encoding="utf-8")):
        if r.get("status") == "OK" and r.get("code") and ((r.get("metCount") or 0) >= 6 or r.get("zone") in ("READY", "BREAKOUT_ZONE")):
            codes.add(str(r["code"]).zfill(6))
    try:
        for ln in json.loads((ROOT / "docs" / "research" / "agents.json").read_text(encoding="utf-8"))["portfolio"]["lines"]:
            if ln.get("market") == "kr":
                codes.add(str(ln["code"]).zfill(6))
    except (OSError, ValueError, KeyError):
        pass
    try:
        for r in json.loads((ROOT / "docs" / "recovery" / "data" / "latest.json").read_text(encoding="utf-8")).get("strongest") or []:
            if str(r.get("market")).lower() == "kr" and r.get("code"):
                codes.add(str(r["code"]).zfill(6))
    except (OSError, ValueError):
        pass
    return sorted(codes)


def run_full(tok: str) -> int:
    stocks, errors = {}, 0
    uni = universe()
    for i, (code, mk) in enumerate(uni):
        try:
            p = kr_period(tok, code, mk, dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d"), 1)
            o0 = ((p["data"] or {}).get("Output_0")) or {}
            if isinstance(o0, list):
                o0 = o0[0] if o0 else {}
            stocks[code] = {"name": str(o0.get("iem_nm") or "").lstrip("*#"), "sector": o0.get("bstp_kor_isnm"), "sectorCode": o0.get("bstp_cls_code"),
                            "marcapEok": _i(o0.get("hts_avls")), "per": _f(o0.get("per")), "pbr": _f(o0.get("pbr")), "asOf": str(o0.get("bsop_date") or "")}
        except Exception as e:  # noqa: BLE001 - 종목 하나 실패가 전체를 막지 않게
            errors += 1
            stocks[code] = {"error": repr(e)[:120]}
        if i % 100 == 0:
            print(f"full {i}/{len(uni)} 오류 {errors}", flush=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"schemaVersion": 2, "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(), "stocks": stocks}, ensure_ascii=False), encoding="utf-8")
    print("저장:", OUT, "종목", len(stocks), "오류", errors)
    return 0 if errors < len(stocks) * 0.2 else 1


def run_flow(tok: str) -> int:
    kst_today = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=9)).strftime("%Y%m%d")
    codes = candidates()
    stocks, errors = {}, 0
    for code in codes:
        try:
            inv = investor(tok, code)
            stocks[code] = {**summarize_flow(inv, kst_today), "forRate": _f(inv[0].get("for_rate")) if inv else None}
        except Exception as e:  # noqa: BLE001
            errors += 1
    kr_bars: list[str] = []  # 한국 장이 열린 최근 날짜들(대표 종목 일봉 20개). 휴장일 판정에 쓴다(agents/freshness.py)
    try:
        bars = rows(kr_period(tok, "005930", "1", dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d"), 20))
        kr_bars = [str(b.get("bsop_date")) for b in bars if b.get("bsop_date")]
    except Exception as e:  # noqa: BLE001
        print("마지막 봉 조회 실패:", repr(e)[:120])
    FLOW_OUT.parent.mkdir(parents=True, exist_ok=True)
    FLOW_OUT.write_text(json.dumps({"schemaVersion": 1, "krBars": kr_bars, "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(), "kstToday": kst_today,
                                    "note": "frgn=invest(외국인, 네이버와 근접·소폭 차이), inst=기관, indiv=개인. 순매수 수량 합계. 오늘(KST) 행 제외",
                                    "stocks": stocks}, ensure_ascii=False), encoding="utf-8")
    print("저장:", FLOW_OUT, "종목", len(stocks), "/", len(codes), "오류", errors)
    return 0 if errors < max(1, len(codes)) * 0.2 else 1


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["auto", "full", "flow"], default="auto")
    mode = ap.parse_args().mode
    if mode == "auto":  # 토요일(UTC)이거나 업종 파일이 없거나 8일 넘게 묵었으면 전종목, 아니면 수급만
        stale = True
        if OUT.exists():
            try:
                g = dt.datetime.fromisoformat(json.loads(OUT.read_text(encoding="utf-8"))["generatedAt"])
                stale = (dt.datetime.now(dt.timezone.utc) - g).days >= 8
            except (OSError, ValueError, KeyError):
                stale = True
        mode = "full" if stale or dt.datetime.now(dt.timezone.utc).weekday() == 5 else "flow"
    print("mode:", mode)
    tok = token()
    rc = run_full(tok) if mode == "full" else 0
    rc = max(rc, run_flow(tok))  # 전종목 갱신 날에도 수급은 같이 받는다
    sys.exit(rc)


if __name__ == "__main__":
    main()
