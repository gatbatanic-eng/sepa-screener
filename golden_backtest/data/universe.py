"""P1 유니버스 제외 규칙 적용(순수 함수). 규칙과 사유는 config/universe.yaml 한 곳에 있다."""
from __future__ import annotations


def compute_exclusions(records: dict[str, dict], cfg: dict, failed: dict[str, str] | None = None) -> dict[str, list[str]]:
    """records[저장명] = {"errors": [...DHR형 보정 오류 봉], "nan_runs": [...]}  →  {저장명: [사유, ...]} (제외 종목만).

    failed: 수집에 실패한 종목 {요청 티커: 오류}. 데이터가 없으므로 유니버스에서 뺀다(정적 사유가 있으면 그것을, 없으면 오류를 사유로).
    """
    ex = cfg["exclude"]
    out: dict[str, list[str]] = {}

    def add(sym: str, reason: str) -> None:
        out.setdefault(sym, []).append(reason)

    for sym, reason in ex.get("static", {}).items():
        if sym in records or sym in (failed or {}):
            add(sym, reason)
    for sym, err in (failed or {}).items():
        if sym not in out:
            add(sym, f"수집 실패: {err}")

    rule = ex["rules"]["adjustment_error_dhr_type"]
    for sym, rec in records.items():
        hits = [a for a in rec.get("errors", []) if a["date"] >= rule["since"]]
        if hits:
            add(sym, f"{rule['reason']} ({', '.join(a['date'] for a in hits)})")

    rule = ex["rules"]["consecutive_missing"]
    for sym, rec in records.items():
        hits = [r for r in rec.get("nan_runs", []) if r["start"] >= rule["since"] and r["bars"] > rule["max_run"]]
        if hits:
            add(sym, f"{rule['reason']} ({', '.join(r['start'] + '~' + r['end'] + ' ' + str(r['bars']) + '봉' for r in hits)})")
    return out
