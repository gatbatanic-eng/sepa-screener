"""위원회 메모: 6개 데스크 + 의장 종합을 한 장으로 묶는다. 같은 (종류, 기간) 메모는 한 번만 쓴다(수정 금지)."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from . import cio, config, data, desks, recommend
from .store import read_json, write_json

DISCLAIMER = "규칙 기반 참고 자료이며 투자 권유가 아닙니다. 최종 판단과 책임은 본인에게 있습니다."
WINDOW_DAYS = {"daily": 1, "weekly": 7, "monthly": 31}
TITLES = {"daily": "일간 투자 자문 메모", "weekly": "주간 투자 자문 메모", "monthly": "월간 투자 자문 메모"}


def _prior(kind: str, today: dt.date, out_dir: Path) -> dict | None:
    """비교 대상: 같은 종류에서 기간 창만큼 이전(없으면 그 전 가장 가까운) 메모."""
    idx = read_json(out_dir / "index.json", {"memos": []}) or {"memos": []}
    cutoff = (today - dt.timedelta(days=WINDOW_DAYS[kind])).isoformat()
    cands = [m for m in idx["memos"] if m["kind"] == kind and m["date"] <= cutoff]
    if kind != "daily" and not cands:
        cands = [m for m in idx["memos"] if m["kind"] == "daily" and m["date"] <= cutoff]
    if not cands:
        return None
    m = max(cands, key=lambda m: m["date"])
    return read_json(out_dir / m["kind"] / f"{m['key']}.json")


def _track_summary(out_dir: Path) -> dict | None:
    s = read_json(out_dir / "picks_stats.json")
    if not s:
        return None
    return {"recordedDays": s["recordedDays"], "minN": s["minN"],
            "groups": {g: {h: {k: v[k] for k in ("n", "meanReturn", "meanExcess", "winRate", "status")} for h, v in hs.items()} for g, hs in s["groups"].items()}}


def _history(kind: str, today: dt.date, out_dir: Path) -> dict | None:
    if kind == "daily":
        return None
    idx = read_json(out_dir / "index.json", {"memos": []}) or {"memos": []}
    since = (today - dt.timedelta(days=WINDOW_DAYS[kind])).isoformat()
    rows = []
    for m in sorted((m for m in idx["memos"] if m["kind"] == "daily" and m["date"] > since), key=lambda m: m["date"]):
        d = read_json(out_dir / "daily" / f"{m['key']}.json")
        if d:
            rows.append([m["date"], d["stance"]["label"], f"{d['stance']['score']:+d}", d["macro"].get("regime") or "–"])
    return {"id": "history", "heading": "입장 추이 (일간 메모 기준)", "table": {"columns": ["날짜", "입장", "합계", "매크로 국면"], "rows": rows}} if rows else None


def build(kind: str, today: dt.date, root: Path | None = None, out_dir: Path | None = None, now: dt.datetime | None = None) -> dict:
    out_dir = out_dir or config.OUT_DIR
    now = now or dt.datetime.now(dt.timezone.utc)
    inp = data.load(root)
    d_macro, macro = desks.macro_desk(inp, now)
    d_market, markets = desks.market_desk(inp)
    decision = cio.decide(macro, markets, inp["regime_thresholds"])
    d_sector, sector = desks.sector_desk(inp)
    d_stock, stock = desks.stock_desk(inp, decision["stance"], today)
    d_risk, risk = desks.risk_desk(decision["stance"], inp, markets, macro)
    rec = recommend.run(inp["root"], decision["stance"], macro.get("regime"))
    rec["track"] = _track_summary(out_dir)
    prev = _prior(kind, today, out_dir)
    stance = {**decision, "summary": cio.summary(decision, d_macro, d_market, d_risk),
              "changes": cio.changes(prev, {**decision})}
    sections = [d_macro, d_market, d_sector, d_stock, d_risk]
    hist = _history(kind, today, out_dir)
    from agents.report import period_keys
    key = period_keys(today)[kind] or today.isoformat()
    memo = {"schemaVersion": 1, "kind": kind, "key": key, "date": today.isoformat(), "title": f"{TITLES[kind]} {key}",
            "generatedAt": now.isoformat(), "stance": stance, "picks": rec, "desks": sections, "macro": {k: macro.get(k) for k in ("regime", "score", "generated_at", "levels")},
            "markets": {m: {k: v for k, v in s.items() if k != "zones"} for m, s in markets.items()},
            "dataAsOf": {"macro": macro.get("generated_at"), "league": (inp.get("league") or {}).get("generatedAt")},
            "disclaimer": DISCLAIMER}
    if hist:
        memo["history"] = hist
    return memo


def to_markdown(m: dict) -> str:
    s = m["stance"]
    o = [f"# {m['title']}", f"> {m['disclaimer']}", f"> 데이터 기준: 매크로 {m['dataAsOf']['macro']}", "",
         f"## 의장 종합 — 입장: **{s['label']}** (합계 {s['score']:+d})"]
    o += [f"- {x}" for x in s["summary"]]
    o += ["", "| 구성 | 값 | 점수 |", "|---|---|---|"] + [f"| {p['name']} | {p['value']} | {p['points']:+d} |" for p in s["parts"]]
    o += ["", "**지난 메모 대비**"] + [f"- {x}" for x in s["changes"]]
    if s["flips"]:
        o += ["", "**입장이 바뀌는 조건**"] + [f"- {x}" for x in s["flips"]]
    if m.get("picks"):
        pk = m["picks"]
        o += ["", f"## 공격 진입 추천 — {pk['headline']}", "> 추천 팀(돌파·리더·안전 렌즈)이 고르고, 별개 검증 팀(데이터·리스크·추세·실적·국면·반론)이 감사합니다. 등급 A 통과 · B 조건부 · C 관찰 전용(비중 0)."]
        c = ["종목", "등급", "종합", "돌파/리더/안전", "진입가", "손절", "계획 손실", "권고 비중", "검증 요약"]
        o += ["", "| " + " | ".join(c) + " |", "|" + "---|" * len(c)]
        for p in pk["picks"]:
            pl = p.get("plan") or {}
            summ = "; ".join((p["verify"]["fail"] + p["verify"]["warn"])[:2]) or "경고 없음"
            o.append("| " + " | ".join(str(x) for x in [f"{p['market']} {p['name']}", f"{p['grade']}({p['verdict']})", p["composite"],
                     "/".join(str(v) for v in p["lens"].values()), pl.get("entryPriceMax", "–"), pl.get("referenceStop", "–"),
                     f"{pl['plannedLossPct']:.1f}%" if pl.get("plannedLossPct") else "–", f"{p['weight']:.1%} (≈{p['amount']:,.0f})" if p.get("weight") else "0 (관찰)" if p["grade"] == "C" else "–", summ]) + " |")
        if pk.get("track"):
            o += ["", f"사후 검증: 기록 {pk['track']['recordedDays']}일 — 표본 {pk['track']['minN']}건 전에는 성과를 근거로 쓰지 않습니다."]
        else:
            o += ["", "사후 검증: 아직 기록 없음(추천을 기록한 날부터 5·20·40거래일 뒤 성과가 쌓입니다)."]
    for d in m["desks"]:
        o += ["", f"## {d['name']} — {d['headline']}"] + [f"- {b}" for b in d["bullets"]]
        if d.get("table"):
            c = d["table"]["columns"]
            o += ["", "| " + " | ".join(c) + " |", "|" + "---|" * len(c)] + ["| " + " | ".join(str(x) for x in r) + " |" for r in d["table"]["rows"]]
        o += [f"- ⚠ {g}" for g in d["gaps"]]
    if m.get("history"):
        h = m["history"]
        o += ["", f"## {h['heading']}", "| " + " | ".join(h["table"]["columns"]) + " |", "|---|---|---|---|"] + ["| " + " | ".join(str(x) for x in r) + " |" for r in h["table"]["rows"]]
    return "\n".join(o) + "\n"


def save(m: dict, out_dir: Path | None = None, archive: bool = True) -> bool:
    """latest.json은 항상 갱신. 보관본은 같은 (종류, 기간)이 없을 때만 쓰고 True를 돌려준다."""
    d = out_dir or config.OUT_DIR
    write_json(d / "latest.json", m)
    if not archive:
        return False
    path = d / m["kind"] / f"{m['key']}.json"
    if path.exists():
        return False
    write_json(path, m)
    path.with_suffix(".md").write_text(to_markdown(m), encoding="utf-8")
    idx = read_json(d / "index.json", {"schemaVersion": 1, "memos": []}) or {"schemaVersion": 1, "memos": []}
    idx["memos"] = sorted([x for x in idx["memos"] if not (x["kind"] == m["kind"] and x["key"] == m["key"])] +
                          [{"kind": m["kind"], "key": m["key"], "title": m["title"], "date": m["date"], "stance": m["stance"]["label"]}],
                          key=lambda x: (x["date"], x["kind"]), reverse=True)
    write_json(d / "index.json", idx)
    return True
