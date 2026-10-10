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


def rank(rows: list[dict], ctx: dict, sectors: dict[str, str], market: str) -> dict:
    """rows: select.pick()의 allRows. 적합 종목만 v2 점수로 다시 줄 세워 최대 3개."""
    scored = []
    for r in rows:
        if r["reject"]:
            continue
        sec = sectors.get(r["code"])
        st = ctx["sectorStats"].get(sec) if sec else None
        s_adj = st["adj"] if st else 0
        exch = r.get("market") or ""
        reg = regime_of(ctx, exch, market)
        m_adj = -C.REGIME_PENALTY if reg == "RED" else 0
        scored.append((r, {"score": r["score"] + s_adj + m_adj, "sector": sec, "sectorAdj": s_adj, "marketAdj": m_adj}))
    scored.sort(key=lambda x: (-x[1]["score"], x[0]["funnelRank"] or 999, x[0]["entry"]["riskPct"] or 999, x[0]["code"]))
    picks = []
    for r, v2 in scored[:C.MAX_PICKS]:
        picks.append({**{k: r[k] for k in ("code", "name", "market", "price", "score", "strategies", "funnelRank", "entry")}, "v2": v2,
                      "why": why_lines(r, v2, ctx, market)})
    return {"picks": picks, "suitable": len(scored)}
