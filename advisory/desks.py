"""데스크 6개. 각 함수는 {id, name, headline, bullets, table?, gaps}를 돌려준다. 모든 문장은 입력 수치에서만 만든다."""
from __future__ import annotations

import datetime as dt
import statistics as st

from . import config

EXIT_WARN = ("WATCH_EXIT", "PROFIT_ALERT", "TREND_BREAK", "EXIT")


def _desk(did, name, headline, bullets=None, table=None, gaps=None):
    d = {"id": did, "name": name, "headline": headline, "bullets": bullets or [], "gaps": gaps or []}
    if table:
        d["table"] = table
    return d


def _num(x):
    return x if isinstance(x, (int, float)) and x == x else None


def _f(v, nd=2):
    return "–" if v is None else f"{v:,.{nd}f}"


# ---------------------------------------------------------------- 매크로
KEY_INDICATORS = ("US10Y", "HY_OAS", "DXY", "VIX", "USDKRW", "WTI", "SOX")


def macro_desk(inp: dict, now: dt.datetime) -> tuple[dict, dict]:
    m = inp.get("macro")
    if not m:
        return _desk("macro", "매크로 데스크", "매크로 데이터 없음", gaps=["docs/macro.json이 없습니다"]), {"regime": None}
    ind = m.get("indicators", {})
    bullets = [f"{s['group']}: {s['message']} ({s['score']:+d})" for s in m.get("signals", [])] or ["발동한 규칙 없음"]
    flips = []
    for s in m.get("signals", []):
        rule = inp["rules"].get(s["id"], {})
        if s.get("score", 0) < 0 and rule.get("condition"):
            flips.append({"id": s["id"], "gain": -s["score"], "condition": rule["condition"], "message": s["message"]})
    rows = []
    for k in KEY_INDICATORS:
        v = ind.get(k)
        if not v:
            continue
        unit = "bp" if v.get("kind") == "rate" or k == "HY_OAS" else "%"
        rows.append([v["name"], _f(v["value"]), f"{_f(v.get('chg_1w'), 1)}{unit}", f"{_f(v.get('chg_1m'), 1)}{unit}", _f(v.get("pct_1y"), 0)])
    table = {"columns": ["지표", "현재", "1주", "1개월", "1년 백분위"], "rows": rows}
    gaps = []
    try:  # 'YYYY-MM-DD HH:MM KST'
        gen = dt.datetime.strptime(m["generated_at"][:16], "%Y-%m-%d %H:%M")
        age = (now.astimezone(dt.timezone(dt.timedelta(hours=9))).replace(tzinfo=None) - gen).days
        if age > config.STALE_MACRO_DAYS:
            gaps.append(f"매크로 갱신이 {age}일 전입니다")
    except (KeyError, ValueError):
        gaps.append("매크로 갱신 시각을 읽지 못했습니다")
    bullets.append(f"규칙 결론: {m.get('sepa_note')}")
    d = _desk("macro", "매크로 데스크", f"{m['regime_label']} (점수 {m['score']:+d})", bullets, table, gaps)
    return d, {"regime": m["regime"], "score": m["score"], "flips": flips, "generated_at": m["generated_at"],
               "note": m.get("sepa_note"), "levels": {k: ind[k]["value"] for k in KEY_INDICATORS if k in ind}}


# ---------------------------------------------------------------- 시장
def _seg(market: str, r: dict) -> str:
    """스크리너는 한국을 코스피·코스닥으로 나눠 국면·breadth를 따로 계산한다. 미국은 하나."""
    if market == "us":
        return "US"
    return "KOSPI" if str(r.get("market")).upper().startswith("KOSPI") else "KOSDAQ"


def _market_stats(rows: list[dict]) -> dict:
    ok = [r for r in rows if r.get("status") == "OK"]
    reg: dict = {}
    for r in ok:
        if r.get("regime"):
            reg[r["regime"]] = reg.get(r["regime"], 0) + 1
    passed = [r for r in ok if r.get("passAll")]
    br = [b for b in (_num(r.get("breadth")) for r in ok) if b is not None]
    sf = [b for b in (_num(r.get("sizeFactor")) for r in ok) if b is not None]
    zones: dict = {}
    for r in ok:
        if r.get("zone"):
            zones[r["zone"]] = zones.get(r["zone"], 0) + 1
    gates = {r.get("marketGate") for r in ok if r.get("marketGate")}
    return {"n": len(rows), "ok": len(ok), "bad": len(rows) - len(ok), "regime": max(reg, key=reg.get) if reg else None,
            "breadth": round(st.median(br), 3) if br else None, "sizeFactor": round(st.median(sf), 2) if sf else None,
            "passed": len(passed), "watch": sum(1 for r in ok if r.get("entryVerdict") == "WATCH"),
            "go": sum(1 for r in ok if r.get("entryVerdict") == "GO"),
            "exitWarn": sum(1 for r in passed if r.get("exitState") in EXIT_WARN), "zones": zones,
            "gate": max(gates, key=lambda g: sum(1 for r in ok if r.get("marketGate") == g)) if gates else None}


def market_desk(inp: dict) -> tuple[dict, dict]:
    by_seg: dict[str, list[dict]] = {"KOSPI": [], "KOSDAQ": [], "US": []}
    bad_reason: dict[str, dict] = {s: {} for s in by_seg}
    for m in ("kr", "us"):
        for r in inp["rows"][m]:
            seg = _seg(m, r)
            by_seg[seg].append(r)
            if r.get("status") != "OK":
                k = (r.get("reason") or r.get("status") or "")[:18]
                bad_reason[seg][k] = bad_reason[seg].get(k, 0) + 1
    stats, bullets, gaps, rows = {}, [], [], []
    for seg, rs_ in by_seg.items():
        s = _market_stats(rs_)
        stats[seg] = s
        reason = max(bad_reason[seg], key=bad_reason[seg].get) if bad_reason[seg] else None
        if not s["ok"]:
            if s["n"]:
                gaps.append(f"{seg}: 분석 가능 종목이 없습니다" + (f" ({reason})" if reason else ""))
            rows.append([seg, "분석 불가", "–", "–", "–", "–", "–"])
            continue
        if s["bad"] > s["n"] * 0.2:
            gaps.append(f"{seg}: 분석 불가 {s['bad']}종목 ({reason} 등)")
        rows.append([seg, s["regime"] or "–", f"{s['breadth']:.0%}" if s["breadth"] is not None else "–",
                     f"{s['passed']} ({s['passed'] / s['ok']:.1%})", s["watch"] + s["go"], s["exitWarn"], s["gate"] or "–"])
    table = {"columns": ["구간", "다수 국면", "breadth(50일선 위 비중)", "추세 통과", "진입 WATCH·GO", "통과 중 청산 경고", "시장 게이트"], "rows": rows}
    regs = {k: v["regime"] for k, v in stats.items() if v["regime"]}
    if len(set(regs.values())) > 1:
        bullets.append("국면 불일치: " + ", ".join(f"{k} {r}" for k, r in regs.items()) + " — 구간별로 따로 판단(한국은 코스피·코스닥이 다르다)")
    for k, s in stats.items():
        if s["ok"] and s["zones"]:
            bullets.append(f"{k} 구간 분포: " + ", ".join(f"{z} {n}" for z, n in sorted(s["zones"].items(), key=lambda kv: -kv[1])[:4]))
    head = " · ".join(f"{k} {r}" for k, r in regs.items()) or "시장 데이터 없음"
    return _desk("market", "시장 데스크", head, bullets, table, gaps), stats


# ---------------------------------------------------------------- 섹터·테마
def _grp(rows: list[dict]) -> dict:
    rs = [x for x in (_num(r.get("rsRank")) for r in rows) if x is not None]
    above = [r for r in rows if _num(r.get("close")) and _num(r.get("sma50")) and r["close"] > r["sma50"]]
    return {"n": len(rows), "passed": sum(1 for r in rows if r.get("passAll")), "above50": len(above) / len(rows) if rows else 0,
            "rs": round(sum(rs) / len(rs), 1) if rs else None}


def sector_desk(inp: dict) -> tuple[dict, dict]:
    bullets, gaps, tables = [], [], []
    idx = {(m, str(r.get("code")).upper()): r for m in ("us",) for r in inp["rows"][m] if r.get("status") == "OK"}
    chains: dict[str, list[dict]] = {}
    for sym, meta in inp["meta"].items():
        r = idx.get(("us", sym))
        if r and meta.get("AI_ValueChain"):
            chains.setdefault(meta["AI_ValueChain"], []).append(r)
    rows = []
    for name, rs_ in chains.items():
        if len(rs_) >= 3:
            g = _grp(rs_)
            rows.append([f"AI·{name}", g["n"], g["passed"], f"{g['above50']:.0%}", _f(g["rs"], 0)])
    sectors: dict[str, list[dict]] = {}
    for (m, sym), r in idx.items():
        sec = (inp["valuation"].get(sym) or {}).get("sector")
        if sec:
            sectors.setdefault(sec, []).append(r)
    for name, rs_ in sectors.items():
        if len(rs_) >= config.MIN_GROUP_N:
            g = _grp(rs_)
            rows.append([f"미국·{name}", g["n"], g["passed"], f"{g['above50']:.0%}", _f(g["rs"], 0)])
    rows.sort(key=lambda r: (-float(r[3].rstrip("%")), -(float(r[4]) if r[4] != "–" else 0)))
    top, bottom = rows[:config.TOP_SECTORS], rows[-config.TOP_SECTORS:] if len(rows) > config.TOP_SECTORS else []
    table = {"columns": ["그룹", "종목", "추세 통과", "50일선 위", "평균 RS"], "rows": top + bottom} if rows else None
    heat = {}
    for m in ("kr", "us"):
        h = inp["funnel"].get(m)
        if h:
            lvl = {"g": "정상", "y": "주의", "r": "경계"}.get(h.get("level"), h.get("level"))
            heat[m] = h.get("level")
            bullets.append(f"{m.upper()} AI 인프라 바스켓 과열도: {lvl} (적중 {h.get('hits')}/4, 6개월 +100% 이상 {h.get('A_share100', 0):.0%})")
    if not rows:
        gaps.append("섹터 통계를 만들 미국 분석 종목이 없습니다")
    if rows:
        bullets.insert(0, f"50일선 위 비중 상위: {top[0][0]} {top[0][3]}" + (f" / 하위: {bottom[-1][0]} {bottom[-1][3]}" if bottom else ""))
    head = (f"강세 {top[0][0]}" if rows else "섹터 데이터 부족")
    return _desk("sector", "섹터·테마 데스크", head, bullets, table, gaps), {"heat": heat, "rows": rows}


# ---------------------------------------------------------------- 종목
def stock_desk(inp: dict, stance: str | None, today: dt.date) -> tuple[dict, dict]:
    cand = []
    for m in ("kr", "us"):
        for r in inp["rows"][m]:
            if r.get("status") != "OK" or not r.get("passAll"):
                continue
            if r.get("entryVerdict") in ("WATCH", "GO") or r.get("zone") in ("READY", "BREAKOUT_ZONE"):
                cand.append((m, r))
    cand.sort(key=lambda t: -(_num(t[1].get("setupScore")) or 0))
    cand = cand[:config.MAX_CANDIDATES]
    gaps, rows, bullets = [], [], []
    from agents import roster, topic as tp
    evidence_ok = True
    try:
        from personas import build_evidence, normalize_code, track_record, valuation
        ctx = {m: track_record.context_for(m, inp["root"]) for m in ("kr", "us")}
        cache = valuation.load_cache(inp["root"] / "docs" / "data" / "valuation_us.json")
    except Exception as exc:  # noqa: BLE001 — 근거 패킷이 없어도 후보 표는 낸다
        evidence_ok = False
        gaps.append(f"역할별 근거를 만들지 못했습니다({type(exc).__name__})")
    for m, r in cand:
        code = str(r.get("code")).zfill(6) if m == "kr" else str(r.get("code"))
        committee = [roster.ROSTER[a]["label"] for a in ("trend_leader", "setup_ready", "low_risk")
                     if roster.ROSTER[a]["select"]({"id": f"adv:{m}:{code}", "market": m, "code": code, "attributes": r}) is not None]
        concern = "–"
        if evidence_ok:
            try:
                fund = None
                fp = inp["root"] / "docs" / "data" / "fundamentals" / m / f"{code}.json"
                if fp.exists():
                    import json
                    fund = json.loads(fp.read_text(encoding="utf-8"))
                ev = build_evidence(r, fund, today=today, context=ctx[m], valuation=valuation.get(cache, code) if m == "us" else None)
                con = tp._role_cards(ev)
                texts = [c for card in con if card["role"] in ("반론 검토관", "리스크 심사관") for c in card["concerns"]]
                concern = texts[0] if texts else "–"
            except Exception:  # noqa: BLE001
                concern = "근거 생성 실패"
        rows.append([f"{m.upper()} {r.get('name')}", r.get("zone") or "–", _f(_num(r.get("setupScore")), 1), _f(_num(r.get("rsRank")), 0),
                     f"{_f(_num(r.get('initRisk')), 1)}%", ", ".join(committee) or "없음", concern])
    if stance == "defense" and rows:
        bullets.append("방어 입장: 아래는 신규 진입 후보가 아니라 관찰 목록입니다(강세가 확인되는 종목을 미리 보는 용도)")
    if not rows:
        bullets.append("추세 통과 + (WATCH·GO 또는 READY·돌파 구간)을 모두 만족하는 종목이 없습니다")
    league = inp.get("league") or {}
    held = [l for l in (league.get("portfolio") or {}).get("lines", []) if l["target"] > 0]
    bullets.append(f"에이전트 리그 권고 포트폴리오: {len(held)}종목" + (" (실전 계열 시작 전이면 0)" if not held else ""))
    table = {"columns": ["종목", "구간", "셋업", "RS", "초기 위험", "규칙이 고르는 에이전트", "대표 우려(리스크·반론 렌즈)"], "rows": rows} if rows else None
    return _desk("stock", "종목 리서치 데스크", f"관찰 후보 {len(rows)}종목", bullets, table, gaps), {"n": len(rows)}


# ---------------------------------------------------------------- 리스크
def risk_desk(stance: str, inp: dict, market_stats: dict, macro_info: dict) -> tuple[dict, dict]:
    cap = config.EXPOSURE_CAP[stance]
    league = inp.get("league") or {}
    ex = (league.get("portfolio") or {}).get("exposure") or {}
    bullets = [f"입장 '{config.STANCE_LABELS[stance]}' → 권고 총 투입 상한 {cap}% (참고값, 현금 {100 - cap}% 이상)"]
    gross = ex.get("grossPct")
    if gross is not None:
        bullets.append(f"현재 에이전트 리그 권고 포트폴리오 투입 {gross}% — " + ("상한 초과, 줄일 여지 점검" if gross > cap else "상한 이내"))
    sf = {k: s["sizeFactor"] for k, s in market_stats.items() if s.get("sizeFactor") is not None}
    if sf:
        bullets.append("스크리너 자체 권장 진입비중 계수(구간별 중앙값): " + ", ".join(f"{k} {v}" for k, v in sf.items()))
    bullets.append("종목·시장·업종 한도(에이전트 리그와 동일): 한 종목 15%, 한 시장 70%, 한 업종 30%(한국만), 최대 12종목. 미국 섹터·유동성 한도는 아직 미검사")
    gaps = [f"{k} 분석 불가 {s['bad']}종목 — 이 구간의 판단 신뢰도가 낮음" for k, s in market_stats.items() if s["n"] and s["bad"] > s["n"] * 0.2]
    return _desk("risk", "리스크 데스크", f"권고 투입 상한 {cap}%", bullets, None, gaps), {"cap": cap}
