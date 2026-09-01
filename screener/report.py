"""랭킹 리포트 — screening_result.csv 를 사람이 읽기 좋은 Markdown 으로.

예:
  python -m screener.report
  python -m screener.report --input output/screening_result.csv --top 30
"""
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

import pandas as pd

SIGNAL_ORDER = ["BUY", "WATCH", "SELL"]
MARKET_LABEL = {"KOSPI": "한국", "KOSDAQ": "한국", "US": "미국"}
TABLE_COLS = [
    ("rank", "순위"),
    ("symbol", "종목코드"),
    ("name", "종목명"),
    ("composite_score", "종합"),
    ("value_score", "가치"),
    ("momentum_score", "모멘텀"),
    ("quality_score", "퀄리티"),
    ("signal", "신호"),
    ("reason", "판정 사유"),
]


def _fmt(v) -> str:
    if pd.isna(v):
        return "-"
    if isinstance(v, float):
        return f"{v:.1f}"
    return str(v)


def _table(df: pd.DataFrame) -> str:
    head = "| " + " | ".join(label for _, label in TABLE_COLS) + " |"
    sep = "| " + " | ".join("---" for _ in TABLE_COLS) + " |"
    lines = [head, sep]
    for _, r in df.iterrows():
        cells = [_fmt(r.get(key)) for key, _ in TABLE_COLS]
        cells = [c.replace("|", "\\|") for c in cells]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def build_report(df: pd.DataFrame, top: int) -> str:
    df = df.copy()
    df["region"] = df["market"].map(MARKET_LABEL).fillna(df["market"])
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    out = [f"# 스크리닝 랭킹 리포트 ({now})", ""]
    total = df["signal"].value_counts().to_dict()
    out.append("| 신호 | 종목 수 |")
    out.append("| --- | --- |")
    for s in ["BUY", "WATCH", "SELL", "NEUTRAL"]:
        out.append(f"| {s} | {total.get(s, 0)} |")
    out.append("")

    for region in ["한국", "미국"]:
        sub = df[df["region"] == region]
        if sub.empty:
            continue
        out.append(f"## {region}")
        out.append("")
        for sig in SIGNAL_ORDER:
            rows = sub[sub["signal"] == sig].sort_values(
                "composite_score", ascending=False
            ).head(top).reset_index(drop=True)
            n_all = (sub["signal"] == sig).sum()
            out.append(f"### {sig} — 상위 {len(rows)} / {n_all}종목")
            out.append("")
            if rows.empty:
                out.append("_해당 종목 없음_\n")
                continue
            rows.insert(0, "rank", range(1, len(rows) + 1))
            out.append(_table(rows))
            out.append("")

    return "\n".join(out)


def print_console_summary(df: pd.DataFrame) -> None:
    counts = df["signal"].value_counts().to_dict()
    print("\n" + "=" * 60)
    print("랭킹 리포트 요약")
    print("=" * 60)
    print("  " + ", ".join(f"{s} {counts.get(s, 0)}"
                            for s in ["BUY", "WATCH", "SELL", "NEUTRAL"]))
    for sig in SIGNAL_ORDER:
        rows = df[df["signal"] == sig].sort_values(
            "composite_score", ascending=False
        ).head(5)
        print(f"\n── {sig} 상위 {len(rows)} ──")
        for _, r in rows.iterrows():
            print(f"  {r['composite_score']:5.1f}  {r['symbol']:<8} {r['name']:<16}"
                  f" [{r['market']}]")


def run(args) -> Path:
    src = Path(args.input)
    if not src.exists():
        raise SystemExit(f"입력 파일이 없습니다: {src}  (먼저 screener.main 실행)")
    df = pd.read_csv(src, dtype={"symbol": str})

    md = build_report(df, args.top)
    stamp = datetime.now().strftime("%Y%m%d")
    out_path = Path(args.outdir) / f"report_{stamp}.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md, encoding="utf-8")
    print(f"리포트 저장: {out_path}")

    print_console_summary(df)
    return out_path


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="스크리닝 랭킹 리포트 생성")
    p.add_argument("--input", default="output/screening_result.csv")
    p.add_argument("--outdir", default="output")
    p.add_argument("--top", type=int, default=20)
    return p


def main(argv: list[str] | None = None) -> int:
    run(build_parser().parse_args(argv))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
