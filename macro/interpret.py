"""
interpret.py — rules.yaml 규칙 평가 + 국면 판정 + (선택) Claude API 3문장 요약

입력: fetch_macro.build_snapshot() 결과
출력: dict (docs/macro.json 으로 저장됨)
"""
from __future__ import annotations

import os
import json
import logging
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

import yaml

log = logging.getLogger("macro.interpret")
HERE = os.path.dirname(os.path.abspath(__file__))
RULES_PATH = os.path.join(HERE, "rules.yaml")
KST = timezone(timedelta(hours=9))


class _Missing:
    """결측 지표: 어떤 비교든 False, 포맷하면 'n/a'"""
    def __getattr__(self, _):
        return self
    def __bool__(self):
        return False
    def __lt__(self, o): return False
    __le__ = __gt__ = __ge__ = __lt__
    def __eq__(self, o): return False
    def __ne__(self, o): return True
    def __mul__(self, o): return self
    __rmul__ = __truediv__ = __rtruediv__ = __add__ = __radd__ = __sub__ = __rsub__ = __neg__ = __mul__
    def __format__(self, spec): return "n/a"
    def __repr__(self): return "n/a"
    def __hash__(self): return 0


class _Ind(SimpleNamespace):
    """None 속성은 _Missing 으로 대체해 비교식이 조용히 False 가 되게 함"""
    def __getattribute__(self, k):
        v = super().__getattribute__(k)
        return _Missing() if v is None else v


def _namespace(snapshot: dict) -> dict:
    ns = {k: _Ind(**v) for k, v in snapshot.items()}
    # 규칙에서 참조했는데 수집 안 된 지표
    for iid in ("US10Y", "US2Y", "SPREAD_2S10S", "KR3Y", "DXY", "USDKRW", "USDJPY", "WTI",
                "COPPER", "GOLD", "VIX", "HY_OAS", "SOX", "KOSPI", "KOSDAQ",
                "KOSPI_FOREIGN", "COPPER_GOLD"):
        ns.setdefault(iid, _Missing())
    return ns


def load_rules(path: str = RULES_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def evaluate_rules(snapshot: dict, rules_cfg: dict) -> list[dict]:
    ns = _namespace(snapshot)
    safe_globals = {"__builtins__": {"abs": abs, "min": min, "max": max, "round": round}}
    fired = []
    for r in rules_cfg["rules"]:
        try:
            ok = bool(eval(r["condition"], safe_globals, dict(ns)))
        except Exception as e:  # noqa: BLE001
            log.warning("규칙 %s 평가 실패: %s", r["id"], e)
            ok = False
        if not ok:
            continue
        try:
            # message 안의 {US10Y.chg_1w:.0f} 같은 표현식을 f-string 으로 평가
            msg = eval("f" + repr(r["message"]), safe_globals, dict(ns))
        except Exception as e:  # noqa: BLE001
            log.warning("규칙 %s 메시지 포맷 실패: %s", r["id"], e)
            msg = r["message"]
        fired.append(dict(id=r["id"], group=r["group"], message=msg, score=int(r.get("score", 0))))
    return fired


def regime_of(score: int, cfg: dict) -> str:
    rc = cfg["regime"]
    if score >= rc["risk_on_min"]:
        return "risk_on"
    if score <= rc["risk_off_max"]:
        return "risk_off"
    return "neutral"


# ------------------------------------------------------------
# Claude API 요약 (ANTHROPIC_API_KEY 없으면 건너뜀)
# ------------------------------------------------------------
SUMMARY_SYSTEM = (
    "당신은 한국·미국 주식에 투자하는 개인 스윙트레이더를 위한 매크로 해설자입니다. "
    "반도체·AI 인프라 비중이 큰 포트폴리오, 미너비니 SEPA(추세추종)와 BNF(급락 평균회귀) 두 전략을 씁니다. "
    "주어진 수치와 규칙 판정만 근거로, 정확히 3문장으로 오늘의 매크로 상황을 요약하세요. "
    "1문장: 가장 중요한 변화 하나. 2문장: 그것이 반도체/코스피에 갖는 함의. 3문장: 오늘 SEPA/BNF 운용에 대한 한 줄 시사점. "
    "수치를 지어내지 말고, 근거 없는 전망은 하지 마세요. 존댓말 없이 간결한 평서문(~다)으로."
)


def claude_summary(snapshot: dict, fired: list[dict], regime_label: str, score: int) -> str | None:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        log.info("ANTHROPIC_API_KEY 없음 → Claude 요약 생략")
        return None
    try:
        import anthropic
    except ImportError:
        log.warning("anthropic 패키지 없음 → 요약 생략")
        return None

    rows = []
    for iid, d in snapshot.items():
        rows.append(f"{iid}({d['name']}): {d['value']:.4g}{d['unit']} | 1d {d['chg_1d']} | 1w {d['chg_1w']} "
                    f"| 1m {d['chg_1m']} | 1y백분위 {d['pct_1y']}")
    fired_txt = "\n".join(f"- [{f['id']}] {f['message']} (점수 {f['score']:+d})" for f in fired) or "- 해당 규칙 없음"
    user_msg = (f"## 지표 (금리류 변화는 bp, 나머지는 %)\n" + "\n".join(rows) +
                f"\n\n## 규칙 판정\n{fired_txt}\n\n## 국면: {regime_label} (합계 {score:+d})")
    try:
        client = anthropic.Anthropic(api_key=key)
        resp = client.messages.create(
            model=os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-5"),
            max_tokens=400,
            system=SUMMARY_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
        )
        return resp.content[0].text.strip()
    except Exception as e:  # noqa: BLE001
        log.error("Claude 요약 실패: %s", e)
        return None


# ------------------------------------------------------------
def interpret(snapshot: dict, rules_path: str = RULES_PATH, use_claude: bool = True) -> dict:
    cfg = load_rules(rules_path)
    fired = evaluate_rules(snapshot, cfg)
    score = sum(f["score"] for f in fired)
    regime = regime_of(score, cfg)
    label = cfg["regime"]["labels"][regime]
    summary = claude_summary(snapshot, fired, label, score) if use_claude else None

    from fetch_macro import GROUPS
    return dict(
        generated_at=datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"),
        regime=regime,
        regime_label=label,
        score=score,
        sepa_note=cfg["regime"]["sepa_note"][regime],
        signals=fired,
        summary=summary,
        groups=GROUPS,
        indicators=snapshot,
    )


def to_json(result: dict, path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1, default=str)
