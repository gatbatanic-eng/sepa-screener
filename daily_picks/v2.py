"""v2: v1 점수 + 업종 강도 ± + 시장 환경(RED) -. 진입 적합 조건·최대 3개는 v1과 같다. 근거 문장은 수치에서만 만든다(LLM 미사용)."""
from __future__ import annotations

from . import config as C
from .pool import CORE, RESEARCH

NAMES = {**CORE, **RESEARCH}


def _fmt(v, nd=1):
    return "-" if v is None else f"{v:,.{nd}f}"


def regime_of(ctx: dict, exch: str, market: str) -> str | None:
    """한국은 거래소(KOSPI/KOSDAQ)별 국면, 미국은 하나('US')."""
    reg = ctx["regimes"]
    return reg.get("US") if market == "us" else reg.get(exch) or reg.get(exch.upper())


def why_lines(p: dict, v2: dict, ctx: dict, market: str) -> list[dict]:
    """종목 하나의 근거. kind: strategy(전략 선정) / entry(진입 조건) / sector / market / macro / issue."""
    out = []
    strat = ", ".join(NAMES.get(k, k) + (f" {v}위" if k == "funnel" and isinstance(v, int) else "") for k, v in p["strategies"].items())
    out.append({"kind": "strategy", "text": f"{strat} 선정 (v1 점수 {p['score']})"})
    e = p["entry"]
    out.append({"kind": "entry", "text": f"손절폭 {_fmt(e['riskPct'], 2)}%({e['riskSource']}), 추격·NO-GO·유동성 조건 통과"})
    sec = v2.get("sector")
    if sec:
        s = ctx["sectorStats"].get(sec)
        if s:
            tone = "강세" if s["adj"] > 0 else "약세" if s["adj"] < 0 else "중간"
            out.append({"kind": "sector", "text": f"업종 {sec} {tone}: 50일선 위 {s['above50Pct']:.0f}%, 평균 RS {s['avgRs']:.0f}, {s['of']}개 업종 중 {s['rank']}위 (점수 {s['adj']:+d})"})
        else:
            out.append({"kind": "sector", "text": f"업종 {sec}: 세파 유니버스 종목이 {C.SECTOR_MIN_N}개 미만이라 강도를 계산하지 않음 (점수 0)"})
    else:
        out.append({"kind": "sector", "text": "업종을 알 수 없어 섹터 점수 0"})
    exch = p.get("market") or ""
    reg = regime_of(ctx, exch, market)
    if reg:
        out.append({"kind": "market", "text": f"시장 {'미국' if market == 'us' else exch} {reg}" + (f" (점수 {-C.REGIME_PENALTY})" if reg == "RED" else "")})
    kc = ctx.get("krClose") or {}
    if sec and market == "kr":
        for key, label in (("strong", "오늘 업종 강세"), ("weak", "오늘 업종 약세"), ("flowIn", "외국인·기관 순매수 유입 업종"), ("flowOut", "외국인·기관 순매도 업종")):
            x = (kc.get(key) or {}).get(sec)
            if x:
                out.append({"kind": "sector", "text": f"{label}: 평균 {x['avgChg']:+.1f}%, 수급/거래대금 {x['netShare']:+.1f}% (참고, 점수 아님)"})
        iss = (kc.get("issues") or {}).get(p["code"])
        if iss:
            out.append({"kind": "issue", "text": f"오늘 이슈: {iss['chg']:+.1f}% · 거래대금 20일 평균의 {iss['surge']:.0f}배 — 변동 주의 (점수 아님)" + (f" · '{iss['headlines'][0]}'" if iss.get("headlines") else "")})
    m = ctx.get("macro")
    if m:
        bits = [f"VIX {_fmt(m['vix'])}" if m.get("vix") is not None else None, f"달러지수 {_fmt(m['dxy'])}" if m.get("dxy") is not None else None,
                f"원/달러 {_fmt(m['usdkrw'], 0)}" if market == "kr" and m.get("usdkrw") is not None else None,
                f"미 10년 {_fmt(m['us10y'], 2)}%" if m.get("us10y") is not None else None]
        out.append({"kind": "macro", "text": f"매크로 {m['label']} ({', '.join(b for b in bits if b)}) — 참고, 점수 아님"})
    return out


def score_row(r: dict, ctx: dict, sectors: dict[str, str], market: str) -> dict:
    sec = sectors.get(r["code"])
    st = ctx["sectorStats"].get(sec) if sec else None
    s_adj = st["adj"] if st else 0
    reg = regime_of(ctx, r.get("market") or "", market)
    m_adj = -C.REGIME_PENALTY if reg == "RED" else 0
    return {"score": r["score"] + s_adj + m_adj, "sector": sec, "sectorAdj": s_adj, "marketAdj": m_adj}


def _pick_row(r: dict, v2: dict, ctx: dict, market: str, **extra) -> dict:
    return {**{k: r[k] for k in ("code", "name", "market", "price", "score", "strategies", "funnelRank", "entry")}, "v2": v2,
            "why": why_lines(r, v2, ctx, market), **extra}


def rank(rows: list[dict], ctx: dict, sectors: dict[str, str], market: str) -> dict:
    """rows: select.pick()의 allRows. 적합 종목만 v2 점수로 다시 줄 세워 최대 3개."""
    scored = [(r, score_row(r, ctx, sectors, market)) for r in rows if not r["reject"]]
    scored.sort(key=lambda x: (-x[1]["score"], x[0]["funnelRank"] or 999, x[0]["entry"]["riskPct"] or 999, x[0]["code"]))
    return {"picks": [_pick_row(r, v2, ctx, market) for r, v2 in scored[:C.MAX_PICKS]], "suitable": len(scored)}


def rank_v3(rows: list[dict], ctx: dict, sectors: dict[str, str], market: str, rs: dict[str, float]) -> dict:
    """v3(2026-10-10 고정, 실험): SEPA가 고른 종목 중 손절폭·NO-GO·추격 필터 없이 v2 점수 순으로 최대 3개를 내고, 손절폭이 8%를 넘으면 권고 비중을 8%/손절폭으로 줄인다.
    근거: 주도주 진입 규칙 백테스트(research/leader_backtest)에서 손절폭 8% 이하 필터(V1)는 추세 통과(TREND)의 초과수익을 없앴다. 유동성 부족·손절폭 미확인은 제외한다."""
    cands, skipped = [], {"유동성 부족": 0, "손절폭 미확인": 0}
    for r in rows:
        if "sepa" not in r["strategies"]:
            continue
        e = r["entry"]
        if e["liquidityOk"] is False:
            skipped["유동성 부족"] += 1
        elif not e["riskPct"] or e["riskPct"] <= 0:
            skipped["손절폭 미확인"] += 1
        else:
            cands.append((r, score_row(r, ctx, sectors, market)))
    cands.sort(key=lambda x: (-x[1]["score"], -(rs.get(x[0]["code"]) or 0), x[0]["entry"]["riskPct"], x[0]["code"]))
    picks = []
    for r, v2 in cands[:C.MAX_PICKS]:
        risk = r["entry"]["riskPct"]
        w = min(1.0, C.MAX_RISK_PCT / risk)
        extra = {"weight": round(w, 2)}
        p = _pick_row(r, v2, ctx, market, **extra)
        p["why"][1] = {"kind": "entry", "text": f"손절폭 {_fmt(risk, 2)}%({r['entry']['riskSource']}) → 권고 비중 {w:.0%}" + ("" if w >= 1 else f" (8% 초과분은 8%÷손절폭으로 축소)")}
        flags = ([f"SEPA {r['entry']['verdict']}"] if r["entry"]["verdict"] == "NO-GO" else []) + r["entry"]["chase"]
        if flags:
            p["why"].insert(2, {"kind": "entry", "text": "참고: " + ", ".join(flags) + " — 백테스트에서 이 필터는 성과를 개선하지 않았음(v3는 거르지 않고 비중으로 조절)"})
        picks.append(p)
    return {"picks": picks, "candidates": len(cands), "skipped": skipped}


def rank_v4(rows: list[dict], ctx: dict, sectors: dict[str, str], market: str, sepa_rows: dict, atr_of) -> dict:
    """v4(2026-10-10 고정, 미국 전용 실험): SEPA 추세 통과(passAll) & RS 70 이상 & ATR14/종가 3% 이상. 손절폭·NO-GO·추격 필터 없음(v3와 같음). 순위는 v2 점수 → RS → ATR, 최대 3개, 권고 비중 min(1, 8%/손절폭).
    근거: 견고성 확인에서 미국의 '추세 통과 + 변동성 3% 이상'이 GOOD 배수 2.6배(전·후반·T3 모두 1.5배 이상), 20일 수익률 ALL 대비 +3.4%p(95% 구간 0.9~5.3)로 PASS. 한국은 FAIL이라 적용하지 않는다."""
    if market != "us":
        return {"picks": [], "candidates": 0, "missingAtr": 0, "skipped": "한국은 견고성 확인에서 FAIL이라 적용하지 않음"}
    by_code = {r["code"]: r for r in rows}
    cands, missing = [], 0
    for code, s in sepa_rows.items():
        if not s.get("passAll") or (s.get("rsRank") or 0) < C.V4_MIN_RS or code not in by_code:
            continue
        a = atr_of(code)
        if a is None:
            missing += 1
        elif a >= C.V4_MIN_ATR_PCT:
            r = by_code[code]
            e = r["entry"]
            if e["liquidityOk"] is False or not e["riskPct"] or e["riskPct"] <= 0:
                continue
            cands.append((r, score_row(r, ctx, sectors, market), a, s.get("rsRank") or 0))
    cands.sort(key=lambda x: (-x[1]["score"], -x[3], -x[2], x[0]["code"]))
    picks = []
    for r, v2, a, rs in cands[:C.MAX_PICKS]:
        risk = r["entry"]["riskPct"]
        w = min(1.0, C.MAX_RISK_PCT / risk)
        p = _pick_row(r, v2, ctx, market, weight=round(w, 2), atrPct=round(a * 100, 2), rs=round(rs, 1))
        p["why"][1] = {"kind": "entry", "text": f"추세 통과(RS {rs:.0f}) · 변동성 ATR {a:.1%} ≥ 3% (v4 조건), 손절폭 {_fmt(risk, 2)}% → 권고 비중 {w:.0%}"}
        picks.append(p)
    return {"picks": picks, "candidates": len(cands), "missingAtr": missing}
