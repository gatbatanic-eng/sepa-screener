"""신호 속성별 Rank IC 진단(연구용). 신호일 시점 속성이 이후 수익률의 순위를 맞히는지 본다. (python -m agents.ic)

SEPA 추적기(research/{kr,us}.json)의 신호 속성과 이미 계산된 사후 수익률(outcomes[h].returnPct)만 쓴다. 네트워크·가격 조회 없음.
신호일마다 시장 안에서 속성과 수익률의 스피어만 순위상관(IC)을 구하고, 날짜별 IC를 한 관측치로 평균한다(같은 날 신호는 함께 움직인다).
95% 구간은 날짜 단위 부트스트랩이다. 에이전트 규칙·계좌·추천에는 연결하지 않는다 — 규칙을 바꾸는 근거로 쓰려면 새 계열로 센다.
"""
from __future__ import annotations

import argparse
import math
import random
from pathlib import Path

import pandas as pd

from . import config
from .roster import USED_KEYS
from .store import read_json, write_json

OUT_JSON = config.STATE_DIR / "attribute_ic.json"
OUT_MD = config.STATE_DIR / "attribute_ic.md"
MIN_CROSS = 8        # 한 날짜의 신호가 이보다 적으면 그날 IC는 계산하지 않는다
MIN_DATES = 30       # 계산된 날짜가 이보다 적으면 '표본 부족' (에이전트 청산 표본 기준 MIN_CLOSED와 같은 값)
# 가격·거래량 절대 수준은 종목 규모를 반영할 뿐이라 순위상관이 의미 없다.
LEVEL_KEYS = {"close", "originalClose", "volume", "volSma50", "평균거래량10", "평균거래량20", "평균거래량50", "sma50", "sma150", "sma200",
              "high52w", "low52w", "pivot", "pivotV2", "swingLow", "structStop", "atr20", "atr60"}


def numeric_keys(signals: list[dict]) -> list[str]:
    keys: dict[str, int] = {}
    for s in signals:
        for k, v in (s.get("attributes") or {}).items():
            if k not in LEVEL_KEYS and isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v):
                keys[k] = keys.get(k, 0) + 1
    return sorted(keys)


def daily_ics(signals: list[dict], key: str, horizon: str, min_cross: int = MIN_CROSS) -> dict[str, float]:
    """{신호일: IC}. 완료된 수익률과 속성값이 둘 다 있는 신호만, 값이 모두 같은 날은 건너뛴다."""
    by_date: dict[str, list[tuple[float, float]]] = {}
    for s in signals:
        o = (s.get("outcomes") or {}).get(horizon) or {}
        x, y = (s.get("attributes") or {}).get(key), o.get("returnPct")
        if o.get("status") != "complete" or isinstance(x, bool) or not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
            continue
        if math.isfinite(x) and math.isfinite(y):
            by_date.setdefault(s["date"], []).append((float(x), float(y)))
    out = {}
    for d, pairs in by_date.items():
        if len(pairs) < min_cross:
            continue
        df = pd.DataFrame(pairs, columns=["x", "y"])
        if df["x"].nunique() < 2 or df["y"].nunique() < 2:
            continue
        ic = df["x"].rank().corr(df["y"].rank())
        if ic == ic:
            out[d] = float(ic)
    return out


def summarize_ics(ics: dict[str, float], n: int = config.BOOT_N, seed: int = config.BOOT_SEED) -> dict:
    vals = list(ics.values())
    if not vals:
        return {"dates": 0, "meanIc": None, "lo": None, "hi": None, "hitRate": None, "status": "NO_DATA"}
    base = {"dates": len(vals), "meanIc": round(sum(vals) / len(vals), 4), "hitRate": round(sum(v > 0 for v in vals) / len(vals), 3)}
    if len(vals) < MIN_DATES:  # 날짜 몇 개를 재표집한 구간은 실제보다 좁게 나와 오해를 부른다 — 구간을 내지 않는다
        return {**base, "lo": None, "hi": None, "status": "INSUFFICIENT"}
    rng = random.Random(seed)
    means = sorted(sum(rng.choice(vals) for _ in vals) / len(vals) for _ in range(n))
    return {**base, "lo": round(means[int(0.025 * n)], 4), "hi": round(means[int(0.975 * n) - 1], 4), "status": "OK"}


def run(research_dir: Path | None = None) -> dict:
    root = research_dir or config.ROOT / "research"
    out = {"schemaVersion": 1, "minCross": MIN_CROSS, "minDates": MIN_DATES, "markets": {}}
    for market in ("kr", "us"):
        sigs = (read_json(root / f"{market}.json", {}) or {}).get("signals", [])
        horizons = sorted({h for s in sigs for h, o in (s.get("outcomes") or {}).items() if o.get("status") == "complete"}, key=int)
        rows = []
        for key in numeric_keys(sigs):
            for h in horizons:
                r = summarize_ics(daily_ics(sigs, key, h))
                if r["dates"]:
                    rows.append({"attribute": key, "horizon": int(h), "usedByAgents": key in USED_KEYS, **r})
        ok = [r for r in rows if r["status"] == "OK"]
        tested = len(ok)
        out["markets"][market] = {"signals": len(sigs), "horizons": [int(h) for h in horizons], "tested": tested,
                                  "insufficient": len(rows) - tested,
                                  "excludesZero": sum(r["lo"] > 0 or r["hi"] < 0 for r in ok),
                                  "expectedByChance": round(0.05 * tested, 1), "rows": rows}
    return out


def render(res: dict) -> str:
    L = ["# 신호 속성별 Rank IC (연구용)", "",
         "신호일 시점 속성이 이후 수익률(신호 종가 기준, 비용 전)의 순위와 얼마나 맞는지 본다. IC가 +이면 값이 클수록 이후 수익률이 높았다는 뜻이다.",
         "날짜별 IC를 한 관측치로 평균, 95% 구간은 날짜 단위 부트스트랩. **투자 권유·규칙 변경 근거가 아니다.**", ""]
    for m, d in res["markets"].items():
        L += [f"## {m.upper()} (신호 {d['signals']}건)", "",
              f"- 날짜 {res['minDates']}개 이상이라 판정 가능한 (속성×기간) {d['tested']}개, 표본 부족 {d['insufficient']}개(구간을 내지 않고 평균 IC만 표시).",
              f"- 판정 가능한 것 중 구간이 0을 벗어난 것 {d['excludesZero']}개 — **우연만으로도 약 {d['expectedByChance']}개**는 나온다. 이 수를 크게 넘지 않으면 신호로 보지 않는다.", ""]
        rows = [r for r in d["rows"] if r["usedByAgents"]] + sorted((r for r in d["rows"] if not r["usedByAgents"]),
                                                                    key=lambda r: -abs(r["meanIc"]))[:10]
        L += ["| 속성 | 기간 | 평균 IC | 95% 구간 | 날짜 수 | IC>0 비율 | 에이전트 사용 | 판정 |", "|---|---|---|---|---|---|---|---|"]
        for r in rows:
            note = "표본 부족" if r["status"] != "OK" else ("0 포함" if r["lo"] <= 0 <= r["hi"] else "0 제외")
            ci = "—" if r["lo"] is None else f"{r['lo']:+.3f} ~ {r['hi']:+.3f}"
            L.append(f"| {r['attribute']} | {r['horizon']}일 | {r['meanIc']:+.3f} | {ci} | {r['dates']} | "
                     f"{r['hitRate']:.0%} | {'예' if r['usedByAgents'] else ''} | {note} |")
        L += ["", "(에이전트가 쓰는 속성 전부 + 그 외 |IC| 상위 10개)", ""]
    L += ["## 읽을 때 주의",
          "- 같은 종목이 여러 날 반복 신호로 잡히고 5·20일 구간이 겹쳐 날짜 간 독립이 아니다. 구간은 실제보다 좁게 나온다.",
          "- 한 날짜의 신호가 적어(수십 건) 날짜별 IC 자체가 잡음이 크다. 날짜 수가 30에 닿기 전에는 방향만 참고한다.",
          "- 범주형(zone 등)·참/거짓 속성은 보지 않았다. 가격·거래량 절대 수준은 규모만 반영해 제외했다.", ""]
    return "\n".join(L)


def main() -> None:
    argparse.ArgumentParser(description=__doc__).parse_args()
    res = run()
    write_json(OUT_JSON, res, indent=1)
    OUT_MD.write_text(render(res), encoding="utf-8")
    for m, d in res["markets"].items():
        print(f"{m}: 검정 {d['tested']}개, 0 제외 {d['excludesZero']}개 (우연 기대 {d['expectedByChance']}개)")


if __name__ == "__main__":
    main()
