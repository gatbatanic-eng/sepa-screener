"""한국 장 마감 정리 보고서. (python -m nhplug.close_report)
평일 16:10 KST(07:10 UTC)에 NHPLUG(조회 전용)로 한국 약 700종목의 당일 일봉·업종·투자자별 순매수(잠정)를 받아
시장 흐름, 강한 섹터, 수급이 쏠린 섹터, 오늘의 이슈(거래대금 급증·뉴스 건수·큰 등락), 우리 종목 점검을 정리한다.
LLM을 쓰지 않는다. 모든 문장은 수치에서 나온다. 수급은 장 마감 직후 잠정치라 확정치와 다를 수 있다.
저장: docs/market/kr_close/YYYY-MM-DD.{json,md} (같은 날은 한 번만 쓴다). 휴장일(대표 종목 당일 봉 없음)은 쓰지 않는다."""
from __future__ import annotations

import datetime as dt
import html
import json
import re
import statistics as st
import sys
from pathlib import Path

import requests

from .kr_extra import investor, universe
from .probe import kr_period, rows, token

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "docs" / "market" / "kr_close"
MIN_SECTOR = 5          # 종목이 5개 미만인 업종은 섹터 순위에서 뺀다
ISSUE_MIN_MOVE = 5.0    # 이슈 후보: 등락 ±5% 이상
ISSUE_MIN_SURGE = 2.0   # 그리고 거래대금이 20일 평균의 2배 이상


def _f(v):
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def sector_group(name):
    s = str(name or "").strip()
    for p in ("코스피 ", "코스닥 "):
        if s.startswith(p):
            s = s[len(p):]
    return s or "기타"


def stock_day(bars: list[dict], o0: dict, today: str) -> dict | None:
    """당일 봉(최신순 bars[0]) + 직전 20일 평균 거래대금 → 당일 지표. 당일 봉이 아니면 None."""
    if not bars or str(bars[0].get("bsop_date")) != today:
        return None
    b = bars[0]
    close, prev = _f(b.get("stck_prpr")), _f(bars[1].get("stck_prpr")) if len(bars) > 1 else None
    turn = _f(b.get("tr_pbmn")) or 0.0
    past = [_f(x.get("tr_pbmn")) or 0.0 for x in bars[1:21]]
    avg = sum(past) / len(past) if past else None
    hi20 = max((_f(x.get("stck_hgpr")) or 0.0) for x in bars[1:21]) if len(bars) > 1 else None
    return {"close": close, "chg": (close / prev - 1) * 100 if close and prev else None, "turn": turn,
            "surge": turn / avg if avg else None, "news": int(_f(b.get("news_cnt")) or 0), "newHigh20": bool(hi20 and close and close > hi20),
            "sector": sector_group(o0.get("bstp_kor_isnm")), "name": str(o0.get("iem_nm") or "").lstrip("*#"), "marcapEok": _f(o0.get("hts_avls"))}


def flow_today(inv: list[dict], today: str) -> dict | None:
    """당일(잠정) 외국인·기관·개인·프로그램 순매수 수량."""
    for x in inv:
        if str(x.get("bsop_date1")) == today:
            g = lambda k: _f(x.get(k)) or 0.0  # noqa: E731
            return {"frgn": g("invest"), "inst": g("gigwan"), "indiv": g("person"), "program": g("program")}
    return None


def build(today: str, data: dict[str, dict], market_of: dict[str, str], watch: dict[str, list[str]], headlines: dict[str, list[str]] | None = None) -> dict:
    """data: {코드: stock_day 결과 + 'flow'}. 수치만으로 보고서 구조를 만든다(테스트 가능하도록 네트워크와 분리)."""
    rep = {"schemaVersion": 1, "date": today, "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(), "stocks": len(data)}
    # 1) 시장 흐름
    mk = {}
    for m in ("KOSPI", "KOSDAQ"):
        xs = [d for c, d in data.items() if market_of.get(c) == m and d["chg"] is not None]
        if xs:
            mk[m] = {"n": len(xs), "up": sum(d["chg"] > 0 for d in xs), "down": sum(d["chg"] < 0 for d in xs),
                     "medianChg": round(st.median(d["chg"] for d in xs), 2),
                     "turnoverEok": round(sum(d["turn"] for d in xs) / 1e8), "newHigh20": sum(d["newHigh20"] for d in xs),
                     "turnoverVsAvg": round(sum(d["turn"] for d in xs) / max(1.0, sum(d["turn"] / d["surge"] for d in xs if d["surge"])), 2)}
    rep["market"] = mk
    # 2) 강한 섹터(동일가중 평균 등락, 상승 비율) 3) 수급이 쏠린 섹터(외국인+기관 순매수 금액 / 섹터 거래대금, 잠정)
    sec: dict[str, list] = {}
    for c, d in data.items():
        if d["chg"] is not None:
            sec.setdefault(d["sector"], []).append((c, d))
    srows = []
    for name, xs in sec.items():
        if len(xs) < MIN_SECTOR:
            continue
        turn = sum(d["turn"] for _, d in xs)
        net = sum(((d.get("flow") or {}).get("frgn", 0) + (d.get("flow") or {}).get("inst", 0)) * (d["close"] or 0) for _, d in xs)
        has_flow = sum(1 for _, d in xs if d.get("flow"))
        lead = max(xs, key=lambda t: t[1]["chg"])
        srows.append({"sector": name, "n": len(xs), "avgChg": round(st.mean(d["chg"] for _, d in xs), 2),
                      "upPct": round(100 * sum(d["chg"] > 0 for _, d in xs) / len(xs)), "turnoverEok": round(turn / 1e8),
                      "netBuyEok": round(net / 1e8) if has_flow else None, "netShare": round(net / turn * 100, 1) if has_flow and turn else None,
                      "leader": f"{lead[1]['name']} {lead[1]['chg']:+.1f}%"})
    rep["strongSectors"] = sorted(srows, key=lambda r: -r["avgChg"])[:5]
    rep["weakSectors"] = sorted(srows, key=lambda r: r["avgChg"])[:3]
    fl = [r for r in srows if r["netShare"] is not None]
    rep["flowInSectors"] = sorted(fl, key=lambda r: -r["netShare"])[:5]
    rep["flowOutSectors"] = sorted(fl, key=lambda r: r["netShare"])[:3]
    def netval(d):
        f = d.get("flow") or {}
        return (f.get("frgn", 0) + f.get("inst", 0)) * (d["close"] or 0)
    top_buy = sorted((c for c in data if data[c].get("flow")), key=lambda c: -netval(data[c]))[:8]
    rep["flowTopStocks"] = [{"code": c, "name": data[c]["name"], "sector": data[c]["sector"], "chg": round(data[c]["chg"] or 0, 2),
                             "netBuyEok": round(netval(data[c]) / 1e8), "frgnEok": round(data[c]["flow"]["frgn"] * (data[c]["close"] or 0) / 1e8),
                             "instEok": round(data[c]["flow"]["inst"] * (data[c]["close"] or 0) / 1e8)} for c in top_buy]
    # 4) 오늘의 이슈: 큰 등락 + 거래대금 급증, 뉴스 건수로 가중. 같은 섹터에 몰리면 '오늘의 테마'
    cand = [(c, d) for c, d in data.items() if d["chg"] is not None and d["surge"] and abs(d["chg"]) >= ISSUE_MIN_MOVE and d["surge"] >= ISSUE_MIN_SURGE]
    cand.sort(key=lambda t: -(abs(t[1]["chg"]) * min(t[1]["surge"], 10) * (1 + min(t[1]["news"], 50) / 25)))
    rep["issues"] = [{"code": c, "name": d["name"], "sector": d["sector"], "chg": round(d["chg"], 2), "surge": round(d["surge"], 1), "news": d["news"],
                      "turnoverEok": round(d["turn"] / 1e8), "headlines": (headlines or {}).get(c, [])} for c, d in cand[:10]]
    themes: dict[str, list] = {}
    for c, d in cand:
        themes.setdefault((d["sector"], "up" if d["chg"] > 0 else "down"), []).append(d["name"])
    rep["themes"] = [{"sector": s, "direction": dirn, "count": len(v), "names": v[:6]} for (s, dirn), v in sorted(themes.items(), key=lambda kv: -len(kv[1])) if len(v) >= 3][:3]
    rep["issueCount"] = len(cand)
    # 5) 우리 종목 점검
    w = []
    for label, codes in watch.items():
        for c in codes:
            d = data.get(c)
            if d:
                w.append({"group": label, "code": c, "name": d["name"], "chg": round(d["chg"] or 0, 2), "close": d["close"],
                          "netBuyEok": round(netval(d) / 1e8) if d.get("flow") else None})
    rep["watch"] = w
    return rep


def stop_alerts(data: dict[str, dict], lines: list[dict]) -> list[dict]:
    """권고 포트폴리오의 한국 보유 종목 중 손절가(-8%, 종가 기준)까지 3% 이내."""
    out = []
    for ln in lines:
        d, stop = data.get(str(ln.get("code"))), ln.get("stopPrice")
        if d and stop and d["close"]:
            gap = (d["close"] / stop - 1) * 100
            if gap <= 3:
                out.append({"code": ln["code"], "name": d["name"], "close": d["close"], "stop": stop, "gapPct": round(gap, 2)})
    return out


def to_markdown(rep: dict) -> str:
    d = rep["date"]
    o = [f"# 한국 장 마감 정리 {d[:4]}-{d[4:6]}-{d[6:]}", "> 규칙 기반 자동 정리(LLM 미사용) · 투자 권유 아님 · 수급은 장 마감 직후 **잠정치** · 데이터: NH투자증권 Open API", ""]
    o.append("## 시장 흐름")
    for m, x in rep["market"].items():
        o.append(f"- {m}: 상승 {x['up']} · 하락 {x['down']} (분석 {x['n']}) · 중앙 등락 {x['medianChg']:+.2f}% · 거래대금 {x['turnoverEok']:,}억(20일 평균 대비 {x['turnoverVsAvg']}배) · 20일 고가 돌파 {x['newHigh20']}종목")
    o += ["", "## 강한 섹터 (동일가중 평균 등락)", "| 업종 | 종목 | 평균 등락 | 상승 비율 | 거래대금(억) | 대표 |", "|---|---|---|---|---|---|"]
    o += [f"| {r['sector']} | {r['n']} | {r['avgChg']:+.2f}% | {r['upPct']}% | {r['turnoverEok']:,} | {r['leader']} |" for r in rep["strongSectors"]]
    o.append("약한 섹터: " + ", ".join(f"{r['sector']} {r['avgChg']:+.2f}%" for r in rep["weakSectors"]))
    o += ["", "## 수급이 쏠린 섹터 (외국인+기관 순매수, 잠정)", "| 업종 | 순매수(억) | 거래대금 대비 | 평균 등락 |", "|---|---|---|---|"]
    o += [f"| {r['sector']} | {r['netBuyEok']:+,} | {r['netShare']:+.1f}% | {r['avgChg']:+.2f}% |" for r in rep["flowInSectors"]]
    o.append("순매도 쏠림: " + ", ".join(f"{r['sector']} {r['netBuyEok']:+,}억" for r in rep["flowOutSectors"]))
    if rep["flowTopStocks"]:
        o.append("순매수 상위 종목: " + ", ".join(f"{x['name']} {x['netBuyEok']:+,}억({x['chg']:+.1f}%)" for x in rep["flowTopStocks"][:6]))
    o += ["", f"## 오늘의 이슈 (등락 ±{ISSUE_MIN_MOVE:.0f}% 이상 + 거래대금 20일 평균의 {ISSUE_MIN_SURGE:.0f}배 이상, {rep['issueCount']}종목)"]
    if rep["themes"]:
        o += [f"- **테마: {t['sector']} {'강세' if t['direction'] == 'up' else '약세'}** — {t['count']}종목 ({', '.join(t['names'])})" for t in rep["themes"]]
    o += ["| 종목 | 업종 | 등락 | 거래대금 배수 | 뉴스 | 헤드라인 |", "|---|---|---|---|---|---|"]
    o += [f"| {x['name']} | {x['sector']} | {x['chg']:+.1f}% | {x['surge']}배 | {x['news']} | {' / '.join(x['headlines'][:2]) or '–'} |" for x in rep["issues"]]
    if rep.get("stopAlerts"):
        o += ["", "## 주의: 손절가 근접 (권고 포트폴리오, 종가 기준 -8%)"] + [f"- {x['name']} 종가 {x['close']:,.0f} / 손절 {x['stop']:,.0f} ({x['gapPct']:+.1f}%)" for x in rep["stopAlerts"]]
    if rep["watch"]:
        o += ["", "## 우리 종목 점검", "| 구분 | 종목 | 등락 | 순매수(억, 잠정) |", "|---|---|---|---|"]
        o += [f"| {x['group']} | {x['name']} | {x['chg']:+.2f}% | {x['netBuyEok'] if x['netBuyEok'] is not None else '–'} |" for x in rep["watch"]]
    return "\n".join(o)


def filter_titles(titles: list[str], name: str, limit: int = 2) -> list[str]:
    """종목명이 들어간 제목만 남긴다(공백 무시). 시황·다른 종목 기사가 섞이는 것을 막는다."""
    key = re.sub(r"\s+", "", str(name or ""))
    if len(key) < 2:
        return []
    out = []
    for t in titles:
        if key in re.sub(r"\s+", "", t) and t not in out:
            out.append(t)
        if len(out) >= limit:
            break
    return out


def headlines_for(items: list[tuple[str, str]]) -> dict[str, list[str]]:
    """네이버 모바일 종목 뉴스 제목 중 종목명이 들어간 것만(참고). items = [(코드, 종목명)]. 실패하면 빈 값."""
    out = {}
    for c, name in items:
        try:
            r = requests.get(f"https://m.stock.naver.com/api/news/stock/{c}?pageSize=10&page=1", timeout=10,
                             headers={"User-Agent": "Mozilla/5.0", "Referer": "https://m.stock.naver.com/"})
            titles: list[str] = []

            def walk(x):
                if isinstance(x, dict):
                    t = x.get("title") or x.get("titleFull")
                    if isinstance(t, str):
                        titles.append(html.unescape(re.sub(r"<[^>]+>", "", t)).strip())
                    for v in x.values():
                        walk(v)
                elif isinstance(x, list):
                    for v in x:
                        walk(v)
            if r.ok:
                walk(r.json())
            out[c] = filter_titles(titles, name)
        except Exception:  # noqa: BLE001
            out[c] = []
    return out


def _read(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def main() -> None:
    today = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=9)).strftime("%Y%m%d")
    path = OUT_DIR / f"{today[:4]}-{today[4:6]}-{today[6:]}.json"
    if path.exists():
        print("이미 있음:", path)
        return
    tok = token()
    probe = rows(kr_period(tok, "005930", "1", today, 1))
    if not probe or str(probe[0].get("bsop_date")) != today:
        print("휴장일 또는 당일 봉 없음 — 보고서를 쓰지 않는다")
        return
    names = {str(r["code"]).zfill(6): r.get("market") for r in _read(ROOT / "docs" / "data" / "latest_kr.json", []) if r.get("code")}
    data, errors = {}, 0
    for i, (code, mk) in enumerate(universe()):
        try:
            res = kr_period(tok, code, mk, today, 21)
            o0 = (res["data"] or {}).get("Output_0") or {}
            o0 = o0[0] if isinstance(o0, list) and o0 else o0
            d = stock_day(rows(res), o0 if isinstance(o0, dict) else {}, today)
            if d:
                d["flow"] = flow_today(investor(tok, code), today)
                data[code] = d
        except Exception:  # noqa: BLE001 - 종목 하나 실패가 보고서를 막지 않게
            errors += 1
        if i % 100 == 0:
            print(f"{i} 오류 {errors}", flush=True)
    agents = _read(ROOT / "docs" / "research" / "agents.json", {})
    lines = [ln for ln in (agents.get("portfolio") or {}).get("lines", []) if ln.get("market") == "kr"]
    memo = _read(ROOT / "docs" / "advisory" / "data" / "latest.json", {})
    acc = _read(ROOT / "docs" / "research" / "accumulation.json", {})
    latest = ((acc.get("forward") or {}).get("latest") or {}).get("groups") or {}
    watch = {"권고 포트폴리오": [str(ln["code"]) for ln in lines],
             "공격 진입 추천": [str(p["code"]).zfill(6) for p in ((memo.get("picks") or {}).get("picks") or []) if str(p.get("market")).upper() != "US"],
             "매집 v1 FLOW": [p["code"] for p in latest.get("FLOW", [])][:10], "매집 v2": [p["code"] for p in latest.get("V2", [])][:10]}
    pre = build(today, data, names, watch)
    rep = build(today, data, names, watch, headlines_for([(x["code"], x["name"]) for x in pre["issues"][:5]]))
    rep["stopAlerts"] = stop_alerts(data, lines)
    rep["errors"] = errors
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rep, ensure_ascii=False), encoding="utf-8")
    md = path.with_suffix(".md")
    md.write_text(to_markdown(rep), encoding="utf-8")
    print("저장:", md, "종목", len(data), "오류", errors)
    if len(data) < 300:
        sys.exit(1)


if __name__ == "__main__":
    main()
