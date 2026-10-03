"""주제·섹터·종목 선택형 보고서. 질의를 대상 종목으로 풀고, 역할별 에이전트가 각자의 렌즈로 근거·우려·체크포인트를 쓴다.

역할(모두 규칙 기반, LLM 호출 없음 — 문장은 수치에서만 나온다):
  리서처 · 추세/기술 애널리스트 · 펀더멘털 애널리스트 · 퀀트 · 리스크 심사관 · 반론 검토관 (personas.logic 증거 패킷 재사용)
  에이전트 위원회(우리 3개 에이전트 규칙을 현재 값에 적용) · 포트폴리오 매니저(권고 포트폴리오 한도·쏠림)
매수·매도 결론은 내지 않는다. 종합표는 '관찰 우선순위용 요약'이다.

질의: 쉼표로 여러 개. 각 토큰은 종목 코드/이름, AI 밸류체인·서브섹터·병목(data/tenbagger_universe.csv), 미국 sector/industry(Yahoo 저장값),
      산업코드 접두(SIC/KSIC 숫자)와 맞춰 본다. 어디에 맞았는지 종목마다 matchedBy로 남긴다.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import re
from pathlib import Path

from personas import build_evidence, normalize_code, track_record, valuation
from personas.logic import DISCLAIMER

from . import config, roster
from .store import read_json, write_json

TOPIC_DIR = config.ROOT / "docs" / "agents" / "topics"
ROLES = (  # (역할, 담당 페르소나 id들)
    ("추세·기술 애널리스트", ("trend", "technical")),
    ("펀더멘털 애널리스트", ("growth", "value")),
    ("퀀트", ("quant",)),
    ("리스크 심사관", ("risk",)),
    ("반론 검토관", ("contrarian",)),
)
MAX_SUPPORT, MAX_CONCERN, MAX_CHECK = 3, 4, 2


def _csv(path: Path) -> list[dict]:
    return list(csv.DictReader(path.open(encoding="utf-8-sig"))) if path.exists() else []


def load_universe(root: Path) -> dict:
    rows = {m: read_json(root / "docs" / "data" / f"latest_{m}.json", []) or [] for m in ("kr", "us")}
    idx = {(m, normalize_code(m, r.get("code"))): r for m, rs in rows.items() for r in rs}
    meta = {str(r["Ticker"]).strip().upper(): r for r in _csv(root / "data" / "tenbagger_universe.csv")}
    val = (read_json(root / "docs" / "data" / "valuation_us.json", {}) or {}).get("symbols", {})
    return {"rows": idx, "meta": meta, "valuation": val, "root": root}


def _industry_code(root: Path, market: str, code: str) -> str | None:
    d = read_json(root / "docs" / "data" / "fundamentals" / market / f"{code}.json")
    return str(d["industryCode"]) if d and d.get("industryCode") else None


def resolve(query: str, uni: dict, limit: int = 12) -> list[dict]:
    """질의 → [{market, code, name, matchedBy[]}]. 먼저 맞은 순서를 유지하고 limit에서 자른다."""
    found: dict[tuple[str, str], dict] = {}

    def add(key, why):
        r = uni["rows"].get(key)
        if r is None:
            return
        e = found.setdefault(key, {"market": key[0], "code": key[1], "name": r.get("name"), "matchedBy": []})
        if why not in e["matchedBy"]:
            e["matchedBy"].append(why)

    for tok in [t.strip() for t in re.split(r"[,\n]", query) if t.strip()]:
        low = tok.lower()
        for (m, code), r in uni["rows"].items():
            if low == code.lower() or (len(low) >= 2 and low in str(r.get("name") or "").lower()):
                add((m, code), f"종목:{tok}")
        for sym, meta in uni["meta"].items():
            hay = " ".join(str(meta.get(k) or "") for k in ("AI_ValueChain", "AI_Subsector", "Bottleneck_Type", "Company")).lower()
            if len(low) >= 2 and low in hay:
                for m in ("us", "kr"):
                    add((m, normalize_code(m, sym)), f"테마·밸류체인:{tok}")
        if len(low) >= 3:
            for sym, v in uni["valuation"].items():
                if low in f"{v.get('sector') or ''} {v.get('industry') or ''}".lower():
                    add(("us", sym), f"미국 섹터·산업:{tok}")
        if tok.isdigit() and len(tok) <= 5:
            for (m, code) in uni["rows"]:
                ic = _industry_code(uni["root"], m, code)
                if ic and ic.startswith(tok):
                    add((m, code), f"산업코드 {tok}*")
    return list(found.values())[:limit]


def _pick(findings: list[dict], n: int) -> list[str]:
    return [f["text"] for f in findings[:n]]


def _role_cards(ev: dict) -> list[dict]:
    by = {p["id"]: p for p in ev["personas"]}
    cards = []
    for role, pids in ROLES:
        ps = [by[i] for i in pids if i in by]
        sup = [f for p in ps for f in p["supports"]]
        con = sorted((f for p in ps for f in p["concerns"]), key=lambda f: -f["severity"])
        chk = [f for p in ps for f in p["checks"]]
        cards.append({"role": role, "supports": _pick(sup, MAX_SUPPORT), "concerns": _pick(con, MAX_CONCERN),
                      "checks": _pick(chk, MAX_CHECK), "maxSeverity": max((f["severity"] for f in con), default=0),
                      "gaps": sorted({g for p in ps for g in p["dataGaps"]})})
    return cards


def _committee(row: dict, market: str, code: str) -> list[str]:
    """우리 에이전트 규칙(roster)을 현재 값에 적용. 신호 기록이 아니라 '오늘 값' 기준의 참고 판정이다."""
    sig = {"id": f"topic:{market}:{code}", "market": market, "code": code, "attributes": row}
    return [roster.ROSTER[a]["label"] for a in ("trend_leader", "setup_ready", "low_risk") if roster.ROSTER[a]["select"](sig) is not None]


def _theme_notes(meta: dict | None) -> dict | None:
    if not meta:
        return None
    keys = {"AI_ValueChain": "밸류체인", "AI_Subsector": "서브섹터", "Bottleneck_Type": "병목", "Catalyst": "촉매", "Risk": "리스크", "Competitors": "경쟁사"}
    return {v: meta[k] for k, v in keys.items() if meta.get(k)}


def build(query: str, root: Path | None = None, limit: int = 12, today: dt.date | None = None) -> dict:
    root = root or config.ROOT
    today = today or dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).date()
    uni = load_universe(root)
    targets = resolve(query, uni, limit)
    portfolio = (read_json(root / "docs" / "research" / "agents.json", {}) or {}).get("portfolio", {})
    held = {(l["market"], l["code"]): l for l in portfolio.get("lines", []) if l["target"] > 0}
    ctx = {m: track_record.context_for(m, root) for m in ("kr", "us")}
    cache = valuation.load_cache(root / "docs" / "data" / "valuation_us.json")
    stocks = []
    for t in targets:
        m, code = t["market"], t["code"]
        row = uni["rows"][(m, code)]
        fund = read_json(root / "docs" / "data" / "fundamentals" / m / f"{code}.json")
        ev = build_evidence(row, fund, today=today, context=ctx[m], valuation=valuation.get(cache, code) if m == "us" else None)
        cards = _role_cards(ev)
        comm = _committee(row, m, code)
        pl = held.get((m, code))
        stocks.append({**t, "snapshot": ev["snapshot"], "status": row.get("status"), "statusReason": row.get("reason"),
                       "theme": _theme_notes(uni["meta"].get(code.upper())), "roles": cards,
                       "committee": comm, "inPortfolio": None if pl is None else {"amount": pl["target"], "weight": pl["weight"], "verdict": pl["verdict"]},
                       "dataGaps": ev["dataGaps"]})
    mk = {}
    for s in stocks:
        mk[s["market"]] = mk.get(s["market"], 0) + 1
    mgr = [f"선택 {len(stocks)}종목 — " + ", ".join(f"{k.upper()} {v}" for k, v in mk.items()) if stocks else "맞는 종목이 없습니다."]
    if stocks:
        n_theme = {}
        for s in stocks:
            if s["theme"]:
                n_theme[s["theme"].get("서브섹터", "")] = n_theme.get(s["theme"].get("서브섹터", ""), 0) + 1
        top = max(n_theme, key=n_theme.get) if n_theme else None
        if top and n_theme[top] >= 2:
            mgr.append(f"같은 서브섹터 쏠림: '{top}' {n_theme[top]}종목 — 함께 담으면 같이 흔들릴 수 있음")
        cap = config.MAX_SINGLE_WEIGHT
        mgr.append(f"권고 포트폴리오 한도: 한 종목 {cap:.0%}, 한 시장 {config.MAX_MARKET_WEIGHT:.0%}, 최대 {config.MAX_NAMES}종목. 선택 종목을 동일 비중으로 모두 담는다고 가정하면 종목당 {100 / len(stocks):.1f}% — "
                   + ("한도 초과" if 1 / len(stocks) > cap else "한도 이내"))
        mgr.append("이미 권고 포트폴리오에 있는 종목: " + (", ".join(f"{k[0].upper()} {k[1]}" for k in held if any(s["market"] == k[0] and s["code"] == k[1] for s in stocks)) or "없음"))
    summary = [[f"{s['market'].upper()} {s['code']}", s["name"], s["status"] if s["status"] != "OK" else "정상",
                (s["snapshot"].get("entryState") or "–"), (s["snapshot"].get("zone") or "–") if "zone" in s["snapshot"] else "–",
                max(c["maxSeverity"] for c in s["roles"]), ", ".join(s["committee"]) or "없음"] for s in stocks]
    key = hashlib.sha1(query.encode()).hexdigest()[:8]
    slug = re.sub(r"[^0-9A-Za-z가-힣]+", "-", query).strip("-")[:30] or "topic"
    return {"schemaVersion": 1, "id": f"{today.isoformat()}-{slug}-{key}", "query": query, "date": today.isoformat(),
            "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(), "limit": limit,
            "scope": {"matched": len(targets), "targets": [{k: t[k] for k in ("market", "code", "name", "matchedBy")} for t in targets],
                      "truncatedAt": limit if len(targets) >= limit else None},
            "researcher": {"notes": ["데이터는 저장소의 스크리너 최신 결과·공시 재무·저장된 Yahoo 지표입니다. 뉴스·컨센서스·실적 발표일은 없습니다(체크포인트로만 표시)."]
                           + ([f"{s['market'].upper()} {s['code']}: 분석 불가 — {s['statusReason']}" for s in stocks if s["status"] != "OK"])},
            "stocks": stocks, "manager": mgr,
            "summary": {"columns": ["종목", "이름", "분석 상태", "진입 상태", "구간", "최대 우려 강도(0~3)", "규칙이 고르는 에이전트"], "rows": summary},
            "disclaimer": DISCLAIMER}


def to_markdown(rep: dict) -> str:
    o = [f"# 주제 보고서: {rep['query']}", f"> {rep['disclaimer']}", f"> 생성 {rep['generatedAt']} · 맞은 종목 {rep['scope']['matched']}개", "", "## 리서처 — 범위"]
    o += [f"- {t['market'].upper()} {t['code']} {t['name']} ← {', '.join(t['matchedBy'])}" for t in rep["scope"]["targets"]] + [f"- {n}" for n in rep["researcher"]["notes"]] + [""]
    o += ["## 종합 (관찰 우선순위용 요약 — 매수·매도 의견 아님)", "| " + " | ".join(rep["summary"]["columns"]) + " |", "|" + "---|" * len(rep["summary"]["columns"])]
    o += ["| " + " | ".join(str(c) for c in r) + " |" for r in rep["summary"]["rows"]] + ["", "## 포트폴리오 매니저"] + [f"- {m}" for m in rep["manager"]] + [""]
    for s in rep["stocks"]:
        o.append(f"## {s['name']} ({s['market'].upper()} {s['code']})")
        if s.get("theme"):
            o += [f"- 테마 메모 · {k}: {v}" for k, v in s["theme"].items()]
        for c in s["roles"]:
            o.append(f"### {c['role']}")
            o += [f"- 근거: {x}" for x in c["supports"]] + [f"- 우려: {x}" for x in c["concerns"]] + [f"- 체크: {x}" for x in c["checks"]] + [f"- [데이터 없음] {g}" for g in c["gaps"]]
        o += [f"### 에이전트 위원회 (오늘 값에 규칙 적용)", "- " + (", ".join(s["committee"]) or "규칙을 통과한 에이전트 없음"), ""]
    return "\n".join(o)


def save(rep: dict, topic_dir: Path | None = None) -> Path:
    d = topic_dir or TOPIC_DIR
    write_json(d / f"{rep['id']}.json", rep)
    (d / f"{rep['id']}.md").write_text(to_markdown(rep), encoding="utf-8")
    index = read_json(d / "index.json", {"schemaVersion": 1, "topics": []})
    index["topics"] = sorted([t for t in index["topics"] if t["id"] != rep["id"]] +
                             [{"id": rep["id"], "query": rep["query"], "date": rep["date"], "matched": rep["scope"]["matched"]}],
                             key=lambda t: t["id"], reverse=True)
    write_json(d / "index.json", index)
    return d / f"{rep['id']}.json"


def main() -> None:
    p = argparse.ArgumentParser(description="주제·섹터·종목 선택형 에이전트 보고서")
    p.add_argument("--query", required=True, help="쉼표 구분. 예: 'AI 반도체, NVDA, 삼성전자, 전력·냉각'")
    p.add_argument("--max", type=int, default=12)
    a = p.parse_args()
    rep = build(a.query, limit=max(1, min(a.max, 30)))
    if not rep["stocks"]:
        raise SystemExit("질의에 맞는 종목이 없습니다. 종목 코드·이름, AI 밸류체인/서브섹터, 미국 섹터, 산업코드 접두를 써 보세요.")
    print("저장:", save(rep))


if __name__ == "__main__":
    main()
