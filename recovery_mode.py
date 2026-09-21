"""계좌복구 공격매매 모드.

기존 SEPA 공격형 모멘텀 연구 결과(research/aggressive_{kr,us}.json)를 읽어
매일 시장에서 가장 강한 3~5개를 추리고, 그중 진입 조건을 통과한 종목만
300~400만원 규모의 참고 포지션으로 제시한다.

주문은 절대 실행하지 않는다. 결과는 docs/recovery/ 에 정적 대시보드와 JSON으로 남긴다.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RESEARCH = ROOT / "research"
OUT = ROOT / "docs" / "recovery"
DATA = OUT / "data"

TOP_STRONG = 5
TOP_ENTRY = 3
MIN_ENTRY_SCORE = 78.0
STRICT_RISK_PCT = 5.5
ENTRY_MIN_KRW = 3_000_000
ENTRY_MID_KRW = 3_500_000
ENTRY_MAX_KRW = 4_000_000


def _num(v, default=0.0):
    try:
        x = float(v)
        return x if math.isfinite(x) else default
    except (TypeError, ValueError):
        return default


def _clip(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def strength_score(row: dict) -> float:
    """0~100. 추세·고점근접·피벗·거래량·종가위치·RSI·신호수·초기리스크를 결합."""
    slope = _clip(_num(row.get("sma50Slope10Pct")) / 8.0) * 15
    high = _clip((_num(row.get("highProximityPct"), -10.0) + 10.0) / 10.0) * 10

    # 피벗에서 -3~+3%를 가장 좋은 진입대역으로 본다. +5% 이상 추격은 감점.
    pd = _num(row.get("pivotDistancePct"), 99)
    if -3 <= pd <= 3:
        pivot = 15.0
    elif 3 < pd <= 5:
        pivot = 15.0 * (1 - (pd - 3) / 2)
    elif -6 <= pd < -3:
        pivot = 10.0 * (1 - abs(pd + 3) / 3)
    else:
        pivot = 0.0

    vr = _num(row.get("volumeRatio"))
    volume = _clip((vr - 1.0) / 1.5) * 15
    clv = _clip((_num(row.get("clv")) - 0.5) / 0.5) * 10

    rsi = _num(row.get("rsi14"), 50)
    rsi_score = max(0.0, 1.0 - abs(rsi - 65.0) / 15.0) * 10

    signals = _clip(_num(row.get("signalCount")) / 6.0) * 15
    risk = _num(row.get("initialRiskPct"), 99)
    risk_score = _clip((7.0 - risk) / 7.0) * 10

    return round(slope + high + pivot + volume + clv + rsi_score + signals + risk_score, 1)


def rankable(row: dict) -> bool:
    return bool(
        row.get("status") == "OK"
        and row.get("inUniverse") is True
        and row.get("marketOk") is True
        and row.get("trendOkAggressive") is True
        and row.get("nearHigh") is True
        and row.get("rsOk") is True
        and row.get("liquidityOk") is True
    )


def entry_ok(row: dict, score: float) -> bool:
    return bool(
        rankable(row)
        and row.get("aggressiveGo") is True
        and row.get("breakout") is True
        and row.get("volumeOk") is True
        and row.get("clvOk") is True
        and row.get("rsiOk") is True
        and row.get("notExtended") is True
        and _num(row.get("initialRiskPct"), 99) <= STRICT_RISK_PCT
        and score >= MIN_ENTRY_SCORE
    )


def suggested_amount(score: float, risk_pct: float) -> int:
    if score >= 90 and risk_pct <= 4.5:
        return ENTRY_MAX_KRW
    if score >= 84 and risk_pct <= 5.0:
        return ENTRY_MID_KRW
    return ENTRY_MIN_KRW


def load_market(market: str) -> tuple[list[dict], str | None]:
    path = RESEARCH / f"aggressive_{market}.json"
    if not path.exists():
        return [], None
    state = json.loads(path.read_text(encoding="utf-8"))
    return state.get("latestRows") or [], state.get("latestSession")


def build_snapshot() -> dict:
    all_rows = []
    sessions = {}
    for market in ("kr", "us"):
        rows, session = load_market(market)
        sessions[market] = session
        for row in rows:
            r = dict(row)
            r["marketBucket"] = market.upper()
            if rankable(r):
                r["strengthScore"] = strength_score(r)
                all_rows.append(r)

    ranked = sorted(
        all_rows,
        key=lambda r: (
            r["strengthScore"],
            _num(r.get("signalCount")),
            _num(r.get("volumeRatio")),
            _num(r.get("sma50Slope10Pct")),
        ),
        reverse=True,
    )

    strongest = ranked[:TOP_STRONG]
    entries = []
    for r in strongest:
        score = r["strengthScore"]
        if not entry_ok(r, score):
            continue
        risk = _num(r.get("initialRiskPct"))
        amount = suggested_amount(score, risk)
        e = dict(r)
        e.update(
            suggestedAmountKRW=amount,
            estimatedInitialRiskKRW=round(amount * risk / 100),
            oneRKRW=round(amount * risk / 100),
            twoRTargetPct=round(risk * 2, 2),
            managementRule="손절가 이탈 전량정리 / +1R 손절가 매수가 부근 상향 / +2R 1/3 익절 / 5거래일 +3% 미만이면 시간손절",
        )
        entries.append(e)
        if len(entries) >= TOP_ENTRY:
            break

    for r in strongest:
        r["entryPass"] = any(e.get("code") == r.get("code") and e.get("marketBucket") == r.get("marketBucket") for e in entries)

    return {
        "schemaVersion": 1,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "sessions": sessions,
        "rules": {
            "topStrong": TOP_STRONG,
            "topEntry": TOP_ENTRY,
            "minEntryScore": MIN_ENTRY_SCORE,
            "maxInitialRiskPct": STRICT_RISK_PCT,
            "entryKRW": [ENTRY_MIN_KRW, ENTRY_MID_KRW, ENTRY_MAX_KRW],
            "maxNewEntriesPerDay": 2,
            "note": "실제 주문 기능 없음. 하루 신규 진입은 최대 2개로 제한하는 운용 규칙을 권장.",
        },
        "strongest": strongest,
        "entries": entries,
    }


def compact_row(r: dict) -> dict:
    keys = [
        "marketBucket","code","name","close","priceAsOf","strengthScore","entryPass",
        "aggressiveWatch","aggressiveGo","signalCount","volumeRatio","clv","rsi14",
        "sma50Slope10Pct","highProximityPct","pivotDistancePct","breakoutLevel",
        "referenceStop","initialRiskPct","suggestedAmountKRW","estimatedInitialRiskKRW",
        "reason",
    ]
    return {k: r.get(k) for k in keys}


def update_history(snapshot: dict) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    path = DATA / "history.json"
    hist = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    session_key = "|".join(str(snapshot["sessions"].get(k) or "") for k in ("kr","us"))
    rec = {
        "sessionKey": session_key,
        "generatedAt": snapshot["generatedAt"],
        "sessions": snapshot["sessions"],
        "strongest": [compact_row(r) for r in snapshot["strongest"]],
        "entries": [compact_row(r) for r in snapshot["entries"]],
    }
    hist = [h for h in hist if h.get("sessionKey") != session_key]
    hist.append(rec)
    hist = hist[-180:]
    path.write_text(json.dumps(hist, ensure_ascii=False, separators=(",",":")), encoding="utf-8")


def _fmt(v, digits=1):
    if v is None:
        return "-"
    if isinstance(v, (int, float)):
        return f"{v:,.{digits}f}"
    return str(v)


def render_html(snapshot: dict) -> str:
    rows = []
    for i, r in enumerate(snapshot["strongest"], 1):
        passed = "진입" if r.get("entryPass") else "관찰"
        stop = _fmt(r.get("referenceStop"), 2)
        amount = next((e.get("suggestedAmountKRW") for e in snapshot["entries"]
                       if e.get("code")==r.get("code") and e.get("marketBucket")==r.get("marketBucket")), None)
        rows.append(
            f"<tr><td>{i}</td><td>{r.get('marketBucket','')}</td><td><b>{r.get('name') or r.get('code')}</b><br><small>{r.get('code')}</small></td>"
            f"<td>{_fmt(r.get('strengthScore'))}</td><td>{passed}</td><td>{_fmt(r.get('volumeRatio'),2)}x</td>"
            f"<td>{_fmt(r.get('rsi14'))}</td><td>{_fmt(r.get('initialRiskPct'))}%</td><td>{stop}</td>"
            f"<td>{('-' if amount is None else f'{amount/1_000_000:.1f}백만원')}</td></tr>"
        )
    entries = snapshot["entries"]
    entry_text = "오늘 조건 통과 종목 없음" if not entries else " / ".join(
        f"{e.get('name') or e.get('code')} {e.get('suggestedAmountKRW',0)/1_000_000:.1f}백만원"
        for e in entries[:2]
    )
    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>계좌복구 공격매매</title>
<style>
body{{font-family:system-ui,-apple-system,sans-serif;margin:0;background:#f6f7f9;color:#111}}
.wrap{{max-width:1100px;margin:auto;padding:20px}} .card{{background:#fff;border-radius:16px;padding:18px;margin-bottom:16px;box-shadow:0 1px 6px #0001}}
h1{{font-size:26px;margin:0 0 8px}} .sub{{color:#666}} .hero{{font-size:20px;font-weight:700}}
table{{width:100%;border-collapse:collapse;font-size:14px}} th,td{{padding:11px 8px;border-bottom:1px solid #eee;text-align:right}} th:nth-child(3),td:nth-child(3){{text-align:left}}
.badge{{display:inline-block;padding:5px 9px;border-radius:999px;background:#111;color:#fff}} .note{{font-size:13px;color:#666;line-height:1.6}}
@media(max-width:700px){{.wrap{{padding:10px}} table{{font-size:12px}} th,td{{padding:8px 4px}}}}
</style></head><body><div class="wrap">
<div class="card"><h1>계좌복구 공격매매 모드</h1><div class="sub">KR {snapshot['sessions'].get('kr') or '-'} · US {snapshot['sessions'].get('us') or '-'}</div></div>
<div class="card"><div class="hero">오늘 실행 후보: {entry_text}</div><p class="note">강한 종목 5개를 먼저 랭킹한 뒤, 돌파·거래량·CLV·RSI·과열·초기리스크 조건을 모두 통과한 종목만 표시합니다. 실제 주문은 실행하지 않습니다. 하루 신규진입은 최대 2개를 권장합니다.</p></div>
<div class="card"><table><thead><tr><th>#</th><th>시장</th><th>종목</th><th>강도</th><th>판정</th><th>거래량</th><th>RSI</th><th>초기위험</th><th>참고손절</th><th>목표금액</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table></div>
<div class="card note"><b>운용 규칙</b><br>① 강도점수 78 이상 ② 초기리스크 5.5% 이하 ③ 돌파+거래량+CLV+RSI+비과열 동시 충족 ④ 종목당 300~400만원 ⑤ 손절가 이탈 전량정리 ⑥ +1R 이후 방어, +2R에서 1/3 익절 ⑦ 5거래일 안에 +3% 미만이면 시간손절.</div>
</div></body></html>"""


def main():
    snapshot = build_snapshot()
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / "latest.json").write_text(json.dumps(snapshot, ensure_ascii=False, separators=(",",":")), encoding="utf-8")
    update_history(snapshot)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "index.html").write_text(render_html(snapshot), encoding="utf-8")
    print(f"strongest={len(snapshot['strongest'])}, entries={len(snapshot['entries'])}")


if __name__ == "__main__":
    main()
