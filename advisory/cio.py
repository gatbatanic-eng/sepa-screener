"""의장(CIO) 종합: 데스크 신호를 점수로 합쳐 입장(공격·중립·방어)을 정하고, 무엇이 바뀌면 입장이 달라지는지 계산한다."""
from __future__ import annotations

from . import config


def _label(score: int) -> str:
    return "offense" if score >= config.OFFENSE_MIN else "defense" if score <= config.DEFENSE_MAX else "neutral"


def _macro_regime(score: int, th: dict) -> str:
    return "risk_on" if score >= th["risk_on_min"] else "risk_off" if score <= th["risk_off_max"] else "neutral"


REGIME_KO = {"risk_on": "리스크온", "neutral": "중립", "risk_off": "리스크오프"}


def decide(macro: dict, markets: dict, regime_th: dict) -> dict:
    parts = []
    if macro.get("regime"):
        parts.append({"name": "매크로 국면", "value": REGIME_KO[macro["regime"]], "points": config.MACRO_POINTS[macro["regime"]]})
    for seg, s in markets.items():
        if s.get("regime") in config.MARKET_REGIME_POINTS:
            parts.append({"name": f"{seg} 시장 국면", "value": s["regime"], "points": config.MARKET_REGIME_POINTS[s["regime"]]})
        if s.get("breadth") is not None:
            b = s["breadth"]
            parts.append({"name": f"{seg} breadth", "value": f"{b:.0%}", "points": 1 if b >= config.BREADTH_HIGH else -1 if b <= config.BREADTH_LOW else 0})
    held = [f"{seg} 국면·breadth" for seg, s in markets.items() if s.get("regime") not in config.MARKET_REGIME_POINTS and s.get("breadth") is None]
    if not macro.get("regime"):
        held.insert(0, "매크로 국면")
    score = sum(p["points"] for p in parts)
    stance = _label(score)
    flips = []
    for f in macro.get("flips", []):
        new_macro = macro["score"] + f["gain"]
        after = _macro_regime(new_macro, regime_th)
        delta = config.MACRO_POINTS[after] - config.MACRO_POINTS[macro["regime"]]
        res = _label(score + delta)
        flips.append(f"매크로 {f['id']} 해소({f['condition']}) → 매크로 점수 {new_macro:+d}, 국면 {REGIME_KO[after]}" +
                     (f", 합계 {score + delta:+d} → {config.STANCE_LABELS[res]}" if delta else " (국면은 그대로라 입장 변화 없음)"))
    for p in parts:
        if p["points"] < 0 and "시장 국면" in p["name"]:
            flips.append(f"{p['name']}이(가) {p['value']}에서 YELLOW 이상이 되면 +1 → 합계 {score + 1:+d} → {config.STANCE_LABELS[_label(score + 1)]}")
        if p["points"] < 0 and "breadth" in p["name"]:
            flips.append(f"{p['name']}이(가) {config.BREADTH_LOW:.0%}를 넘으면 +1 → 합계 {score + 1:+d} → {config.STANCE_LABELS[_label(score + 1)]}")
    neg = [p["name"] for p in parts if p["points"] < 0]
    pos = [p["name"] for p in parts if p["points"] > 0]
    return {"stance": stance, "label": config.STANCE_LABELS[stance], "score": score, "parts": parts, "held": held, "flips": flips,
            "conflict": {"negative": neg, "positive": pos} if neg and pos else None,
            "thresholds": {"offense": config.OFFENSE_MIN, "defense": config.DEFENSE_MAX},
            "toOffense": max(0, config.OFFENSE_MIN - score), "toDefense": max(0, score - config.DEFENSE_MAX)}


def summary(cio: dict, macro_desk: dict, market_desk: dict, risk_desk: dict) -> list[str]:
    lines = [f"입장 {cio['label']} (합계 {cio['score']:+d}; 공격 ≥ {config.OFFENSE_MIN:+d}, 방어 ≤ {config.DEFENSE_MAX:+d}) — 공격까지 {cio['toOffense']}점, 방어까지 {cio['toDefense']}점",
             f"매크로: {macro_desk['headline']}", f"시장: {market_desk['headline']}", f"리스크: {risk_desk['headline']}"]
    if cio.get("conflict"):
        lines.append("상충 신호: 부정 — " + ", ".join(cio["conflict"]["negative"]) + " / 긍정 — " + ", ".join(cio["conflict"]["positive"]) + " (합계가 중립이어도 방향이 갈린다)")
    if cio["held"]:
        lines.append("판단 보류(점수 제외): " + ", ".join(cio["held"]))
    return lines


def changes(prev: dict | None, cur: dict) -> list[str]:
    if not prev:
        return ["비교할 이전 메모가 없습니다"]
    out = []
    a, b = prev["stance"], cur
    if a["label"] != b["label"] or a["score"] != b["score"]:
        out.append(f"입장 {a['label']}({a['score']:+d}) → {b['label']}({b['score']:+d})")
    pa = {p["name"]: p["value"] for p in a["parts"]}
    for p in b["parts"]:
        if p["name"] in pa and str(pa[p["name"]]) != str(p["value"]):
            out.append(f"{p['name']}: {pa[p['name']]} → {p['value']}")
    return out or ["입장·구성 항목 변화 없음"]
