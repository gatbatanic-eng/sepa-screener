"""일간·주간·월간 보고서. 에이전트 리그 산출물(agents.json·권고 포트폴리오 스냅샷·도태 심사)과 스크리너 최신 결과만으로 만든다.
LLM을 부르지 않는다(모든 문장은 수치에서 나온다). 같은 기간 보고서는 한 번만 쓴다(수정 금지).

보고서 = {kind, key, date, title, sections[]}. 섹션은 bullets(문장 목록) 또는 table({columns, rows}).
저장: docs/agents/reports/data/{kind}/{key}.json + .md, 목록: docs/agents/reports/data/index.json.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

from . import config, freshness
from .store import read_json, write_json

REPORT_DIR = config.ROOT / "docs" / "agents" / "reports" / "data"
DISCLAIMER = "가상 계좌 기준의 검증용 보고서이며 투자 권유가 아닙니다. 최종 판단과 책임은 본인에게 있습니다."
EXIT_WARN = ("WATCH_EXIT", "PROFIT_ALERT", "TREND_BREAK", "EXIT")


def period_keys(today: dt.date) -> dict[str, str | None]:
    """오늘 만들어야 하는 보고서 키. 주간=금요일, 월간=이번 달의 마지막 평일(다음 평일이 다음 달이면)."""
    keys: dict[str, str | None] = {"daily": today.isoformat(), "weekly": None, "monthly": None}
    if today.weekday() == 4:
        y, w, _ = today.isocalendar()
        keys["weekly"] = f"{y}-W{w:02d}"
    nxt = today + dt.timedelta(days=1)
    while nxt.weekday() >= 5:
        nxt += dt.timedelta(days=1)
    if nxt.month != today.month and today.weekday() < 5:
        keys["monthly"] = today.strftime("%Y-%m")
    return keys


def _equity_at(curve: list[dict], date: str) -> float | None:
    prior = [c for c in curve if c["date"] <= date]
    return prior[-1]["equity"] if prior else None


def _since(today: dt.date, kind: str) -> str:
    return (today - dt.timedelta(days=7 if kind == "weekly" else 31 if kind == "monthly" else 1)).isoformat()


def _pct(v) -> str:
    return "–" if v is None else f"{v:+.2f}%"


def _snapshots(state_dir: Path) -> list[tuple[str, dict]]:
    out = []
    for p in sorted((state_dir / "portfolio").glob("*.json")):
        d = read_json(p)
        if d:
            out.append((p.stem, d))
    return out


def _market_rows(root: Path) -> dict[str, list[dict]]:
    return {m: (read_json(root / "docs" / "data" / f"latest_{m}.json", []) or []) for m in ("kr", "us")}


def _count(rows, pred) -> int:
    return sum(1 for r in rows if pred(r))


def _segments(rows_by_m: dict) -> dict[str, list[dict]]:
    """스크리너는 한국을 코스피·코스닥으로 나눠 국면·breadth를 따로 계산한다(둘이 다를 수 있다). 미국은 하나."""
    seg: dict[str, list[dict]] = {"KOSPI": [], "KOSDAQ": [], "US": []}
    for m, rows in rows_by_m.items():
        for r in rows:
            key = "US" if m == "us" else "KOSPI" if str(r.get("market")).upper().startswith("KOSPI") else "KOSDAQ"
            seg[key].append(r)
    return seg


def _median(vals):
    vals = sorted(v for v in vals if isinstance(v, (int, float)) and v == v)
    return vals[len(vals) // 2] if vals else None


def _section_market(rows_by_m: dict) -> dict:
    lines = []
    for name, rows in _segments(rows_by_m).items():
        if not rows:
            continue
        ok = [r for r in rows if r.get("status") == "OK"]
        bad = len(rows) - len(ok)
        reg = {}
        for r in ok:
            if r.get("regime"):
                reg[r["regime"]] = reg.get(r["regime"], 0) + 1
        top = max(reg, key=reg.get) if reg else None
        passed = [r for r in ok if r.get("passAll")]
        br, sf = _median([r.get("breadth") for r in ok]), _median([r.get("sizeFactor") for r in ok])
        line = (f"{name}: 분석 {len(ok)}종목 · 국면 {top or '–'} · breadth {f'{br:.0%}' if br is not None else '–'} · 권장 진입비중 {sf if sf is not None else '–'} · "
                f"추세 통과 {len(passed)} · 진입 WATCH {_count(ok, lambda r: r.get('entryVerdict') == 'WATCH')} · "
                f"통과 종목 중 청산 경고 {_count(passed, lambda r: r.get('exitState') in EXIT_WARN)}")
        if bad:
            reasons = {}
            for r in rows:
                if r.get("status") != "OK":
                    k = (r.get("reason") or r.get("status") or "")[:18]
                    reasons[k] = reasons.get(k, 0) + 1
            line += f" · ⚠ 분석 불가 {bad}종목 ({max(reasons, key=reasons.get)} 등)"
        lines.append(line)
    return {"id": "market", "heading": "시장 상태 (스크리너, 코스피·코스닥·미국 구간별)", "bullets": lines}


def _section_agents(agents: dict, series: str, since: str, today: str, heading: str, window: bool) -> dict:
    cols = ["에이전트", "구간 수익률" if window else "누적 수익률", "지수 기준", "최대 낙폭", "청산", "보유", "도태 심사"]
    rows = []
    for aid, a in agents.items():
        s = a["series"][series]["summary"]
        if s.get("status") == "NO_DATA":
            rows.append([a["label"], "데이터 없음", "–", "–", "–", "–", a["review"]["status"]])
            continue
        ret = s["returnPct"]
        if window:
            curve = a["series"][series]["curve"]
            e0, e1 = _equity_at(curve, since), _equity_at(curve, today)
            ret = None if e0 is None or e1 is None else round((e1 / e0 - 1) * 100, 3)
        rows.append([a["label"], _pct(ret), _pct(s.get("referencePct")), _pct(s["maxDrawdownPct"]), s["closed"], s["open"], a["review"]["status"]])
    return {"id": f"agents_{series}", "heading": heading, "table": {"columns": cols, "rows": rows}}


def _diff_portfolio(prev: dict | None, cur: dict) -> list[str]:
    key = lambda l: f"{l['market'].upper()} {l['code']}"
    p = {key(l): l for l in (prev or {}).get("lines", []) if l["target"] > 0}
    c = {key(l): l for l in cur.get("lines", []) if l["target"] > 0}
    new, gone = sorted(set(c) - set(p)), sorted(set(p) - set(c))
    out = []
    if new:
        out.append("신규: " + ", ".join(f"{k}({c[k].get('name') or ''})" for k in new))
    if gone:
        out.append("제외: " + ", ".join(f"{k}({p[k].get('name') or ''})" for k in gone))
    return out or (["변화 없음"] if prev else ["비교할 이전 권고안이 없습니다"])


def _section_portfolio(cur: dict, prev: dict | None, prev_label: str) -> dict:
    ex = cur["exposure"]
    bullets = [f"투입 {ex['grossPct']}% · 현금 {ex['cashPct']}% · 한국 {ex['kr']}% · 미국 {ex['us']}% · {ex['names']}종목",
               f"{prev_label} 대비 — " + " / ".join(_diff_portfolio(prev, cur))]
    bullets += cur.get("warnings", [])
    trimmed = [l for l in cur["lines"] if l["verdict"] != "APPROVED"]
    if trimmed:
        bullets.append("리스크 심사 축소·제외: " + ", ".join(f"{l['market'].upper()} {l['code']}({'; '.join(l['reasons'])})" for l in trimmed[:8]))
    bullets.append("미검사 한도: " + ", ".join(ex["notChecked"]))
    return {"id": "portfolio", "heading": "권고 포트폴리오", "bullets": bullets}


def _section_alerts(agents: dict, rows_by_m: dict, cur: dict) -> dict:
    latest = {(m, str(r.get("code")).zfill(6) if m == "kr" else str(r.get("code"))): r for m, rows in rows_by_m.items() for r in rows}
    lines = []
    for l in cur.get("lines", []):
        if l["state"] != "HELD" or l["target"] <= 0:
            continue
        r = latest.get((l["market"], l["code"]))
        if r and r.get("exitState") in EXIT_WARN:
            lines.append(f"{l['market'].upper()} {l['code']} {l.get('name') or ''}: 청산 경고 {r['exitState']}")
        if l.get("stopPrice") and l.get("lastPrice") and l["lastPrice"] <= l["stopPrice"] * 1.03:
            lines.append(f"{l['market'].upper()} {l['code']} {l.get('name') or ''}: 손절가 {l['stopPrice']} 3% 이내 (현재 {l['lastPrice']:.2f})")
    for aid, a in agents.items():
        for h in a["review"].get("history", [])[-1:]:
            lines.append(f"{a['label']}: {h['date']} {h['from']}→{h['to']} ({h['reason']})")
        n = sum(1 for k in a["series"]["live"]["skipped"] if k["reason"] == "NO_PRICE_DATA")
        if n:
            lines.append(f"{a['label']}: 가격 조회 불가 {n}건")
    return {"id": "alerts", "heading": "주의 항목", "bullets": lines or ["특이사항 없음"]}


def _section_trades(agents: dict, since: str) -> dict:
    rows = []
    for aid, a in agents.items():
        for t in a["series"]["live"]["trades"]:
            if t["exitDate"] > since:
                rows.append([a["label"], f"{t['market'].upper()} {t['code']} {t.get('name') or ''}", t["entryDate"], t["exitDate"], _pct(t["returnPct"]), t["reason"]])
    rows.sort(key=lambda r: r[3], reverse=True)
    return {"id": "trades", "heading": "기간 내 청산", "table": {"columns": ["에이전트", "종목", "진입일", "청산일", "수익률", "사유"], "rows": rows[:30]}} if rows \
        else {"id": "trades", "heading": "기간 내 청산", "bullets": ["청산된 거래 없음"]}


def _section_progress(agents: dict) -> dict:
    need = config.MIN_CLOSED
    lines = []
    for aid, a in agents.items():
        s = a["series"]["live"]["summary"]
        n = 0 if s.get("status") == "NO_DATA" else s["closed"]
        lines.append(f"{a['label']}: 청산 {n}/{need}건 ({min(100, round(100 * n / need))}%) — " + ("표본 충분" if n >= need else "표본 부족, 우열·도태 판단 보류"))
    return {"id": "progress", "heading": "표본 진행도", "bullets": lines}


def _section_signals(root: Path, since: str, state_dir: Path) -> dict:
    lines = []
    for m in ("kr", "us"):
        sigs = (read_json(state_dir / f"signals_{m}.json", {"signals": []}) or {"signals": []})["signals"]
        new = [s for s in sigs if s["date"] > since]
        lines.append(f"{m.upper()}: 신규 SEPA 신호 {len(new)}건 (누적 보관 {len(sigs)}건)" +
                     (" — " + ", ".join(dict.fromkeys(f"{s.get('name') or s['code']}" for s in new))[:160] if new else ""))
    return {"id": "signals", "heading": "신규 신호", "bullets": lines}


def build(kind: str, today: dt.date, root: Path | None = None, state_dir: Path | None = None) -> dict | None:
    root = root or config.ROOT
    state_dir = state_dir or config.STATE_DIR
    data = read_json(root / "docs" / "research" / "agents.json")
    if not data:
        return None
    agents, cur = data["agents"], data["portfolio"]
    snaps = _snapshots(state_dir)
    since = _since(today, kind)
    prev = next((s for d, s in reversed(snaps) if d <= since), None) if kind != "daily" else (snaps[-2][1] if len(snaps) >= 2 else None)
    label = {"daily": "직전 권고안", "weekly": "1주 전", "monthly": "1개월 전"}[kind]
    window = kind != "daily"
    sections = [_section_market(_market_rows(root)),
                _section_agents(agents, "live", since, today.isoformat(), "에이전트 성과 (실전 계열)", window),
                _section_portfolio(cur, prev, label),
                _section_alerts(agents, _market_rows(root), cur),
                _section_signals(root, since, state_dir)]
    if kind != "daily":
        sections.append(_section_trades(agents, since))
    sections.append(_section_progress(agents))
    if kind == "monthly":
        rules = data["rules"]
        sections.append({"id": "rules", "heading": "규칙 점검", "bullets": [
            f"규칙 고정일 {rules['frozenOn']}, 실전 시작 {rules['inception']}. 월간 점검에서는 규칙을 바꾸지 않습니다. 바꿀 사유가 있으면 AGENTS.md 변경 이력에 기록하고 새 계열로 셉니다.",
            "대조군(무작위)보다 일관되게 나은지가 핵심입니다. 표본 부족이면 비교하지 않습니다."]})
    sections.append(_section_agents(agents, "preview", since, today.isoformat(), "참고: 사후 적용 계열 (결과를 알고 만든 규칙 · 근거로 쓰지 말 것)", window))
    title = {"daily": "일간 보고서", "weekly": "주간 보고서", "monthly": "월간 보고서"}[kind]
    key = period_keys(today)[kind] or today.isoformat()
    return {"schemaVersion": 1, "kind": kind, "key": key, "date": today.isoformat(), "title": f"{title} {key}",
            "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(), "dataAsOf": data["generatedAt"],
            "sections": sections, "disclaimer": DISCLAIMER}


def to_markdown(rep: dict) -> str:
    out = [f"# {rep['title']}", f"> {rep['disclaimer']}", f"> 데이터 기준: {rep['dataAsOf']}", ""]
    if rep.get("basis"):
        b = rep["basis"]
        out.insert(3, f"> 시세 기준: 한국 {b['kr']} · 미국 {b['us']} 장 마감")
    if rep.get("staleNote"):
        out.insert(3, f"> ⚠ {rep['staleNote']}")
    for s in rep["sections"]:
        out.append(f"## {s['heading']}")
        if "bullets" in s:
            out += [f"- {b}" for b in s["bullets"]]
        else:
            cols = s["table"]["columns"]
            out += ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
            out += ["| " + " | ".join(str(c) for c in r) + " |" for r in s["table"]["rows"]]
        out.append("")
    return "\n".join(out)


def save(rep: dict, report_dir: Path | None = None) -> bool:
    """같은 (종류, 기간) 보고서는 한 번만 쓴다. 새로 썼으면 True."""
    d = (report_dir or REPORT_DIR)
    path = d / rep["kind"] / f"{rep['key']}.json"
    if path.exists():
        return False
    write_json(path, rep)
    path.with_suffix(".md").write_text(to_markdown(rep), encoding="utf-8")
    index = read_json(d / "index.json", {"schemaVersion": 1, "reports": []})
    index["reports"] = sorted([r for r in index["reports"] if not (r["kind"] == rep["kind"] and r["key"] == rep["key"])] +
                              [{"kind": rep["kind"], "key": rep["key"], "title": rep["title"], "date": rep["date"]}],
                              key=lambda r: (r["date"], r["kind"]), reverse=True)
    write_json(d / "index.json", index)
    return True


def run(today: dt.date, force: list[str] | None = None, new_list: Path | None = None, hold: bool = False, note: str | None = None,
        basis: dict | None = None) -> list[str]:
    """새로 쓴 보고서의 .md 경로를 new_list 파일에 한 줄씩 남긴다(알림 단계가 읽는다)."""
    keys = period_keys(today)
    made, paths = [], []
    for kind in ("daily", "weekly", "monthly"):
        if hold:
            break  # 시세가 아직 최신이 아님: 한 번만 쓰는 보고서를 미룬다(agents.freshness)
        if keys[kind] or (force and kind in force):
            rep = build(kind, today)
            if rep and note:
                rep["staleNote"] = note
            if rep and basis:
                rep["basis"] = basis
            if rep and save(rep):
                made.append(f"{kind}:{rep['key']}")
                paths.append(str(REPORT_DIR / kind / f"{rep['key']}.md"))
    if new_list:
        new_list.write_text("\n".join(paths) + ("\n" if paths else ""), encoding="utf-8")
    return made


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--today", default=config.session_date().isoformat())
    p.add_argument("--force", nargs="*", choices=["daily", "weekly", "monthly"], help="주기와 상관없이 만든다(이미 있으면 건너뜀)")
    p.add_argument("--new-list", type=Path, help="새로 만든 보고서 .md 경로를 적을 파일")
    a = p.parse_args()
    today = dt.date.fromisoformat(a.today)
    fr = freshness.check(today)
    if fr["note"]:
        print(fr["note"])
    print("생성:", run(today, a.force, a.new_list, fr["hold"], fr["note"] if fr["forced"] else None, fr.get("basis")) or "없음")


if __name__ == "__main__":
    main()
