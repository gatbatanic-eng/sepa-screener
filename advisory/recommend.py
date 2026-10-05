"""공격 진입 추천 오케스트레이터: 추천 팀(picks) → 검증 팀(verify) → 최종 목록. 두 팀은 서로의 코드를 쓰지 않고 여기서만 만난다.

최종 목록 규칙
  1) 후보 풀(계좌복구 모드의 가장 강한 종목)을 추천 팀이 종합 점수순으로 정렬한다.
  2) 검증 팀이 풀 전체를 감사한다(통과 A · 조건부 B · 기각 C).
  3) 기각이 아닌 종목을 종합 점수순으로 최대 3개 담는다. 같은 섹터는 대안이 있으면 1개만.
  4) 기각이 아닌 종목이 2개 미만이면 기각 종목 중 점수가 높은 것으로 2개까지 채우되 '관찰 전용'(비중 0)으로 표시한다.
     — 최소 2개를 채우는 것은 목록의 길이이지 검증을 면제하는 것이 아니다. 등급이 언제나 함께 나간다.
"""
from __future__ import annotations

from pathlib import Path

from . import config, picks, verify


def _pick_view(p: dict, v: dict, stance: str) -> dict:
    r = p["row"]
    pl = picks.plan(r)
    grade = v["grade"]
    w = picks.weight(pl["plannedLossPct"] if pl else None, stance, grade)
    return {"market": p["market"], "code": p["code"], "name": p["name"], "sector": p.get("sector"), "industry": p.get("industry"),
            "composite": p["composite"], "lens": p["lens"], "price": r.get("close"), "priceAsOf": r.get("priceAsOf"),
            "strengthScore": r.get("strengthScore"), "plan": pl, "weight": w,
            "amount": None if w is None else round(w * config.PICK_CAPITAL, 1),
            "grade": grade, "verdict": v["verdict"], "verify": {"fail": v["fail"], "warn": v["warn"], "notes": v["notes"]},
            "entryGate": r.get("entryReason")}


def run(root: Path, stance: str, macro_regime: str | None, today_session: dict | None = None) -> dict:
    pool = picks.load_pool(root)
    ranked = picks.rank(pool["rows"])
    scr = verify.Screener(root)
    ctx = {"macroRegime": macro_regime, "expectedSession": {m: s for m, s in (pool.get("sessions") or {}).items()}}
    audited = []
    for p in ranked:
        m = "us" if str(p["market"]).upper() == "US" else "kr"
        v = verify.audit({"market": m, "code": p["code"], "name": p["name"], "price": p["row"].get("close"), "row": p["row"]}, scr, ctx)
        audited.append((p, v))
    chosen, used_sectors = [], set()
    ok = [(p, v) for p, v in audited if v["verdict"] != "기각"]
    for p, v in ok:
        if len(chosen) >= config.PICK_MAX:
            break
        if p.get("sector") in used_sectors and any(q.get("sector") not in used_sectors for q, _ in ok if (q, _) not in chosen):
            continue
        chosen.append((p, v))
        used_sectors.add(p.get("sector"))
    rejected = [(p, v) for p, v in audited if v["verdict"] == "기각"]
    watch_only = 0
    for p, v in rejected:
        if len(chosen) >= config.PICK_MIN:
            break
        chosen.append((p, v))
        watch_only += 1
    final = [_pick_view(p, v, stance) for p, v in chosen]
    n_a = sum(1 for x in final if x["grade"] == "A")
    n_ok = sum(1 for x in final if x["grade"] in ("A", "B"))
    if not pool["rows"]:
        headline = "후보 풀이 비어 있습니다(계좌복구 모드 데이터 없음)"
    elif n_a:
        headline = f"검증 통과 {n_a}종목 포함 {len(final)}종목 추천"
    elif n_ok:
        headline = f"완전히 통과한 종목은 없고 조건부 {n_ok}종목 — 경고를 확인하고 판단"
    else:
        headline = "오늘은 검증을 통과한 공격 진입 종목이 없습니다 — 아래는 관찰 전용 상위 후보"
    return {"headline": headline, "picks": final, "poolSize": len(pool["rows"]), "audited": len(audited), "rejected": len(rejected),
            "watchOnly": watch_only, "poolSource": pool.get("source"), "poolGeneratedAt": pool.get("generatedAt"), "sessions": pool.get("sessions"),
            "regimes": pool.get("regimes"), "stance": stance, "macroRegime": macro_regime,
            "notes": ["추천 팀: 돌파·리더·안전 3개 렌즈 평균 순위(계좌복구 모드 후보 풀)", "검증 팀: 데이터·리스크·추세·실적·국면·반론 6명의 감사(추천 팀과 별개 입력·기준)",
                      "등급 A 통과 · B 조건부(경고 확인) · C 기각(관찰 전용, 비중 0)", "참고 자료이며 투자 권유가 아닙니다. 매수 판단·책임은 본인에게 있습니다."]}
