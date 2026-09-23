"""personas/generate.py — 추세통과(trendOk) 종목의 페르소나 코멘트를 생성해 docs/data/personas/ 에 쓴다.

    python -m personas.generate --market kr|us|all

하는 일
-------
1. 시장별 ``latest_{market}.json`` 에서 ``trendOk`` 종목을 골라, 각 종목마다
   ``personas.build_evidence`` 로 규칙 기반 증거 패킷(근거/우려/체크, 전부 결정론적)을 만든다.
2. (ANTHROPIC_API_KEY 있으면) 그 증거 패킷만 근거로 페르소나별 3~5문장 자연어 코멘트를 Claude 에게
   요청한다. 새 사실을 만들거나 매수·매도를 지시하면 안 된다고 시스템 프롬프트에 명시하고,
   응답을 ``FORBIDDEN_PHRASES`` 로 다시 검사한다 — 걸리거나 API 가 실패하면 그 페르소나(또는 종목)만
   규칙 텍스트(``supports``/``concerns``/``checks`` 그대로)로 대체한다. 절대 조용히 건너뛰지 않는다.
3. 종목별로 ``docs/data/personas/{market}/{code}.json`` 을 쓰고, 대시보드가 버튼 노출 여부를 한 번에
   알 수 있게 ``docs/data/personas/{market}/index.json`` (code -> {name, generatedAt, usedLLM}) 도 쓴다.
4. 문제는 조용히 넘기지 않는다: LLM 실패는 경고로 출력(GitHub Actions 에서 ``::warning::``)하고,
   API 키가 없으면(=전량 규칙 텍스트로 생성) 경고 한 줄만 남기고 계속 진행한다(버튼은 계속 동작해야 함).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable, Optional

from personas import track_record, valuation
from personas.logic import FORBIDDEN_PHRASES, PERSONAS, build_evidence, normalize_code

ROOT = Path(__file__).resolve().parents[1]
MODEL_ENV = "CLAUDE_MODEL"
DEFAULT_MODEL = "claude-sonnet-4-5"

SYSTEM_PROMPT = (
    "당신은 아래 7명의 투자 페르소나 각각의 시각에서, 이미 규칙으로 계산된 사실(evidence)만 가지고 "
    "짧은 코멘트를 쓰는 보조 작가입니다. 페르소나 목록과 관점(lens)은 사용자 메시지에 주어집니다.\n\n"
    "절대 원칙:\n"
    "1. 주어진 finding 텍스트·수치 외의 새로운 사실이나 전망을 지어내지 마세요.\n"
    "2. \"매수하세요\", \"매도하세요\", \"사세요\", \"지금 진입\", \"목표가\", \"익절/손절하세요\" 같은 "
    "행동 지시·매수매도 권유 표현을 절대 쓰지 마세요. 근거·우려·확인할 점만 서술합니다.\n"
    "3. 각 페르소나는 자신의 lens 로 supports(근거)·concerns(우려, severity 높을수록 비중 있게)·"
    "checks(확인할 점)를 종합해 한국어 평서문(~다체) 3~5문장으로 씁니다. 데이터가 거의 없으면 "
    "그 사실과 한계를 솔직히 말하는 것으로 충분합니다(억지로 판단하지 않음).\n"
    "4. dataGaps 가 있으면 마지막에 한계로 짧게 언급하세요.\n"
    "5. 반드시 JSON 객체 하나만 출력하세요. 키는 페르소나 id, 값은 코멘트 문자열입니다. 다른 텍스트 금지."
)


def _persona_prompt_block() -> str:
    return "\n".join(f"- {pid}: {m['name']} — {m['lens']}" for pid, m in PERSONAS.items())


def _evidence_for_prompt(ev: dict) -> str:
    lines = [f"## 종목: {ev['name']} ({ev['code']}, {ev['market']})", f"스냅샷: {json.dumps(ev['snapshot'], ensure_ascii=False)}"]
    for p in ev["personas"]:
        lines.append(f"\n### {p['id']} ({p['name']})")
        for tag, key in (("근거", "supports"), ("우려", "concerns"), ("체크", "checks")):
            for f in p[key]:
                sev = f" [심각도 {f['severity']}]" if key == "concerns" else ""
                lines.append(f"- {tag}{sev}: {f['text']}")
        if p["dataGaps"]:
            lines.append("- 데이터 없음: " + " / ".join(p["dataGaps"]))
    if ev["dataGaps"]:
        lines.append("\n### 종목 전체 데이터 갭\n" + "\n".join(f"- {g}" for g in ev["dataGaps"]))
    return "\n".join(lines)


def _contains_forbidden(text: str) -> Optional[str]:
    for bad in FORBIDDEN_PHRASES:
        if bad in text:
            return bad
    return None


def _rule_based_comment(p: dict) -> str:
    """LLM 을 쓰지 않거나(또는 그 페르소나만) 실패했을 때의 대체 — 근거/우려/체크를 그대로 문장으로."""
    parts = []
    for tag, key in (("근거", "supports"), ("우려", "concerns"), ("체크", "checks")):
        items = [f["text"] for f in p[key]]
        if items:
            parts.append(f"[{tag}] " + " / ".join(items))
    if p["dataGaps"]:
        parts.append("[데이터 없음] " + " / ".join(p["dataGaps"]))
    return "\n".join(parts) if parts else "판단에 쓸 근거가 부족합니다."


def call_llm(ev: dict, *, client: Any, model: str) -> tuple[Optional[dict], Optional[str]]:
    """(persona_id -> comment, 오류사유). 응답이 JSON 이 아니거나 금지표현이 있으면 오류로 취급."""
    user_msg = f"## 페르소나\n{_persona_prompt_block()}\n\n{_evidence_for_prompt(ev)}"
    try:
        resp = client.messages.create(
            model=model, max_tokens=1600, system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        raw = resp.content[0].text.strip()
    except Exception as exc:  # noqa: BLE001 - 종목 하나의 실패가 전체를 죽이면 안 됨
        return None, f"{type(exc).__name__}: {str(exc)[:150]}"
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.startswith("json"):
            raw = raw[4:]
    try:
        obj = json.loads(raw)
    except ValueError:
        return None, "응답이 JSON 이 아님"
    if not isinstance(obj, dict):
        return None, "응답이 JSON 객체가 아님"
    out: dict[str, str] = {}
    for pid in PERSONAS:
        v = obj.get(pid)
        if not isinstance(v, str) or not v.strip():
            continue
        bad = _contains_forbidden(v)
        if bad:
            continue  # 이 페르소나만 규칙 텍스트로 대체(아래 merge 단계)
        out[pid] = v.strip()
    if not out:
        return None, "모든 페르소나 응답이 비었거나 금지표현 포함"
    return out, None


def build_stock_output(ev: dict, llm_comments: Optional[dict]) -> dict:
    personas_out = []
    for p in ev["personas"]:
        comment = (llm_comments or {}).get(p["id"])
        used_llm = comment is not None
        personas_out.append({
            "id": p["id"], "name": p["name"], "lens": p["lens"],
            "comment": comment or _rule_based_comment(p),
            "usedLLM": used_llm,
            "supports": p["supports"], "concerns": p["concerns"], "checks": p["checks"],
            "dataGaps": p["dataGaps"],
        })
    return {"code": ev["code"], "name": ev["name"], "market": ev["market"], "snapshot": ev["snapshot"],
            "personas": personas_out, "dataGaps": ev["dataGaps"], "disclaimer": ev["disclaimer"]}


def _write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":"), allow_nan=False), encoding="utf-8")
    tmp.replace(path)


def _load_fund(root: Path, market: str, code: str) -> Optional[dict]:
    fp = root / "docs" / "data" / "fundamentals" / market / f"{code}.json"
    if not fp.exists():
        return None
    try:
        return json.loads(fp.read_text(encoding="utf-8"))
    except ValueError:
        return None


def generate_market(market: str, root: Path = ROOT, *, client: Optional[Any] = None, model: Optional[str] = None,
                    limit: Optional[int] = None, sleep: Callable[[float], None] = time.sleep,
                    delay: float = 0.2, today: Optional[dt.date] = None, write: bool = True) -> dict:
    m = market.lower()
    result: dict = {"market": m, "problems": [], "warnings": [], "stocks": 0, "usedLLM": 0, "ruleOnly": 0}
    latest_path = root / "docs" / "data" / f"latest_{m}.json"
    if not latest_path.exists():
        result["problems"].append(f"{m.upper()} latest_{m}.json 없음 — 스크리닝을 먼저 실행해야 함")
        return result
    rows = json.loads(latest_path.read_text(encoding="utf-8"))
    trend_rows = [r for r in rows if r.get("trendOk") in (True, "True", "TRUE")]
    if limit:
        trend_rows = trend_rows[:limit]

    ctx = track_record.context_for(m, root)
    yv_cache = valuation.load_cache(root / "docs" / "data" / "valuation_us.json") if m == "us" else None

    index: dict[str, dict] = {}
    stamp = dt.datetime.now(dt.timezone.utc).isoformat()
    for r in trend_rows:
        code = normalize_code(m, r.get("code"))
        fund = _load_fund(root, m, code)
        yv = valuation.get(yv_cache, code) if yv_cache else None
        ev = build_evidence(r, fund, today=today, context=ctx, valuation=yv)

        comments, err = (None, None)
        if client is not None:
            comments, err = call_llm(ev, client=client, model=model or os.environ.get(MODEL_ENV, DEFAULT_MODEL))
            if err:
                result["warnings"].append(f"{code} LLM 코멘트 실패({err}) — 규칙 텍스트로 대체")
            sleep(delay)
        out = build_stock_output(ev, comments)
        n_llm = sum(1 for p in out["personas"] if p["usedLLM"])
        result["usedLLM"] += n_llm
        result["ruleOnly"] += len(out["personas"]) - n_llm
        result["stocks"] += 1
        index[code] = {"name": out["name"], "generatedAt": stamp, "usedLLM": n_llm == len(out["personas"])}
        if write:
            _write_json(root / "docs" / "data" / "personas" / m / f"{code}.json", out)

    if write:
        for stale in (root / "docs" / "data" / "personas" / m).glob("*.json"):
            if stale.stem not in index and stale.stem != "index":
                stale.unlink()
        _write_json(root / "docs" / "data" / "personas" / m / "index.json",
                    {"schemaVersion": 1, "market": m, "generatedAt": stamp, "symbols": index})

    if client is None:
        result["warnings"].append("ANTHROPIC_API_KEY 없음 → 전 종목 규칙 기반 텍스트로만 생성(자연어 코멘트 생략)")
    elif trend_rows and result["usedLLM"] == 0:
        result["problems"].append(f"{m.upper()} 전 종목 LLM 코멘트 생성 실패 — API 상태 확인 필요")
    return result


def _make_client() -> Optional[Any]:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    try:
        import anthropic
    except ImportError:
        return None
    return anthropic.Anthropic(api_key=key)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="페르소나 코멘트 생성(규칙 기반 + 선택적 LLM)")
    ap.add_argument("--market", choices=["kr", "us", "all"], default="all")
    ap.add_argument("--limit", type=int, default=None, help="테스트용: 시장당 종목 수 제한")
    ap.add_argument("--no-llm", action="store_true", help="LLM 호출 생략(규칙 텍스트만)")
    ap.add_argument("--dry-run", action="store_true", help="파일을 쓰지 않고 결과만 출력")
    a = ap.parse_args(argv)
    client = None if a.no_llm else _make_client()
    on_actions = bool(os.environ.get("GITHUB_ACTIONS"))
    exit_code = 0
    for m in (["kr", "us"] if a.market == "all" else [a.market]):
        r = generate_market(m, client=client, limit=a.limit, write=not a.dry_run)
        print(f"[{m.upper()}] 종목 {r['stocks']} | LLM 코멘트 {r['usedLLM']} | 규칙 텍스트 대체 {r['ruleOnly']}")
        for msg in r["warnings"]:
            print(f"  경고: {msg}")
            if on_actions:
                print(f"::warning::[personas/{m}] {msg}")
        for msg in r["problems"]:
            print(f"  문제: {msg}", file=sys.stderr)
            if on_actions:
                print(f"::error::[personas/{m}] {msg}")
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
