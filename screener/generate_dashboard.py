"""멀티팩터 스크리너 결과를 정적 HTML 대시보드로 만든다.

- 입력:
    output/screening_result.csv   (screener.main 출력)
    output/index_history.csv      (screener.index_builder 출력)
    output/index_state.json       (screener.index_builder 출력)
- 출력:
    docs/screener/index.html                 자기완결형 대시보드
    docs/screener/data/signal_history.json   실행일별 신호 수 추이(순방향 누적)

GitHub Pages 가 docs/ 를 서빙하므로 /screener/ 경로로 접근한다.
(SEPA=/, RANGE-MR·V-REBOUND=/range_vrebound/ 와 경로가 겹치지 않는다.)

실행:
    python -m screener.main --market all
    python -m screener.report
    python -m screener.index_builder --top-n 20
    python -m screener.generate_dashboard
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "output"
DOCS_DIR = BASE_DIR / "docs" / "screener"
DATA_DIR = DOCS_DIR / "data"

SCREENING_CSV = OUTPUT_DIR / "screening_result.csv"
INDEX_HISTORY = OUTPUT_DIR / "index_history.csv"
INDEX_STATE = OUTPUT_DIR / "index_state.json"
SIGNAL_HISTORY = DATA_DIR / "signal_history.json"

MARKET_GROUP = {"KOSPI": "KR", "KOSDAQ": "KR", "US": "US"}
GROUP_LABEL = {"KR": "한국 (KOSPI·KOSDAQ)", "US": "미국 (S&P 500)"}
SIGNALS = ["BUY", "WATCH", "SELL", "NEUTRAL"]


def _f(v, digits: int = 2):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    try:
        return round(float(v), digits)
    except (TypeError, ValueError):
        return None


def build_rows(df: pd.DataFrame) -> dict[str, dict]:
    groups: dict[str, dict] = {}
    for _, r in df.iterrows():
        g = MARKET_GROUP.get(str(r["market"]), "US")
        groups.setdefault(g, {"label": GROUP_LABEL.get(g, g), "rows": []})
        groups[g]["rows"].append({
            "symbol": str(r["symbol"]),
            "name": str(r.get("name") or r["symbol"]),
            "market": str(r["market"]),
            "signal": str(r["signal"]),
            "composite": _f(r.get("composite_score"), 1),
            "value": _f(r.get("value_score"), 1),
            "momentum": _f(r.get("momentum_score"), 1),
            "quality": _f(r.get("quality_score"), 1),
            "price": _f(r.get("price"), 2),
            "ret6m": _f(r.get("ret_6m"), 4),
            "ret12m": _f(r.get("ret_12m"), 4),
            "per": _f(r.get("per"), 2),
            "pbr": _f(r.get("pbr"), 2),
            "roe": _f(r.get("roe"), 1),
            "debtToEquity": _f(r.get("debt_to_equity"), 1),
            "reason": str(r.get("reason") or ""),
        })
    for g in groups.values():
        g["rows"].sort(key=lambda x: (x["composite"] is None, -(x["composite"] or 0)))
    return groups


def build_index_block(name_map: dict[str, str]) -> dict:
    if not INDEX_HISTORY.exists():
        return {"available": False}
    hist = pd.read_csv(INDEX_HISTORY)
    history = [
        {
            "date": str(row["date"]),
            "value": round(float(row["index_value"]), 2),
            "isRebalance": str(row["is_rebalance_day"]).lower() == "true",
        }
        for _, row in hist.iterrows()
    ]
    state = json.loads(INDEX_STATE.read_text(encoding="utf-8")) if INDEX_STATE.exists() else {}
    holdings = [
        {
            "symbol": h["symbol"],
            "name": name_map.get(h["symbol"], h["symbol"]),
            "weight": round(float(h.get("weight") or 0), 4),
            "entryPrice": h.get("entry_price"),
            "entryDate": h.get("entry_date"),
        }
        for h in state.get("holdings", [])
    ]
    last = history[-1]["value"] if history else None
    prev = history[-2]["value"] if len(history) >= 2 else state.get("base_value")
    change = (last / prev - 1.0) if (last and prev) else 0.0
    return {
        "available": True,
        "value": last,
        "baseValue": state.get("base_value", 1000.0),
        "baseDate": state.get("base_date"),
        "change": round(change, 6),
        "nextRebalance": state.get("next_rebalance_date"),
        "freq": state.get("freq", "monthly"),
        "topN": state.get("top_n"),
        "history": history,
        "holdings": holdings,
    }


def upsert_signal_history(as_of: str, df: pd.DataFrame) -> list[dict]:
    counts = df["signal"].value_counts().to_dict()
    entry = {"date": as_of, **{s: int(counts.get(s, 0)) for s in SIGNALS}}
    history: list[dict] = []
    if SIGNAL_HISTORY.exists():
        try:
            history = json.loads(SIGNAL_HISTORY.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            history = []
    history = [h for h in history if h.get("date") != as_of]
    history.append(entry)
    history.sort(key=lambda h: h["date"])
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SIGNAL_HISTORY.write_text(json.dumps(history, ensure_ascii=False, indent=1), encoding="utf-8")
    return history


def build() -> None:
    if not SCREENING_CSV.exists():
        print(f"입력 파일이 없습니다: {SCREENING_CSV} (screener.main 을 먼저 실행)")
        return
    df = pd.read_csv(SCREENING_CSV, dtype={"symbol": str})
    as_of = datetime.now().strftime("%Y-%m-%d")

    name_map = {str(r["symbol"]): str(r["name"]) for _, r in df.iterrows()}
    payload = {
        "asOf": as_of,
        "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "counts": {s: int((df["signal"] == s).sum()) for s in SIGNALS},
        "total": int(len(df)),
        "markets": build_rows(df),
        "index": build_index_block(name_map),
        "signalHistory": upsert_signal_history(as_of, df),
    }

    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    html = HTML_TEMPLATE.replace("__DATA_JSON__", json.dumps(payload, ensure_ascii=False))
    (DOCS_DIR / "index.html").write_text(html, encoding="utf-8")
    print(f"대시보드 생성 완료: {DOCS_DIR / 'index.html'}")
    print(f"  종목 {payload['total']}  |  " + "  ".join(f"{s} {payload['counts'][s]}" for s in SIGNALS))
    if payload["index"].get("available"):
        print(f"  지수값 {payload['index']['value']:.2f}  구성종목 {len(payload['index']['holdings'])}")


HTML_TEMPLATE = r"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>멀티팩터 스크리너 · Trending Value–Quality</title>
<style>
  :root {
    --bg: #f5f6f8; --panel: #ffffff; --border: #e3e5e9;
    --text: #1b1e24; --text-dim: #6b7280; --accent: #2563eb;
    --buy-bg: #e6f7ec; --buy-text: #157347; --buy-border: #b7e4c7;
    --watch-bg: #fff2e0; --watch-text: #b45309; --watch-border: #fbd9a8;
    --sell-bg: #fdf2f2; --sell-text: #b42318; --sell-border: #f3c6c2;
    --neutral-bg: #f8f9fa; --neutral-text: #6b7280; --neutral-border: #e3e5e9;
    --up: #d92b2b; --down: #1a56db;
    --row-hover: #f0f4ff; --shadow: 0 1px 3px rgba(0,0,0,0.06), 0 1px 2px rgba(0,0,0,0.04);
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #0f1115; --panel: #171a21; --border: #2a2e37;
      --text: #e7e9ee; --text-dim: #9aa2b1; --accent: #5b8def;
      --buy-bg: #113322; --buy-text: #6bd08a; --buy-border: #1e5c3a;
      --watch-bg: #3a2712; --watch-text: #f6b96a; --watch-border: #6b4a1f;
      --sell-bg: #3a1717; --sell-text: #f2a4a0; --sell-border: #6b2b26;
      --neutral-bg: #1b1f27; --neutral-text: #9aa2b1; --neutral-border: #2a2e37;
      --up: #f0605f; --down: #6ea8fe;
      --row-hover: #1d2230; --shadow: 0 1px 3px rgba(0,0,0,0.4);
    }
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Malgun Gothic", sans-serif;
    font-size: 14px; line-height: 1.5;
  }
  .wrap { max-width: 1200px; margin: 0 auto; padding: 24px 20px 60px; }
  header { margin-bottom: 16px; }
  h1 { font-size: 20px; margin: 0 0 4px; }
  .subtitle { color: var(--text-dim); font-size: 13px; }
  .nav { display: flex; gap: 10px; margin: 10px 0 4px; flex-wrap: wrap; }
  .nav a { font-size: 12px; color: var(--text-dim); text-decoration: none; border: 1px solid var(--border);
           border-radius: 999px; padding: 4px 12px; background: var(--panel); }
  .nav a:hover { color: var(--accent); border-color: var(--accent); }
  .nav a.here { color: #fff; background: var(--accent); border-color: var(--accent); }
  .tabs { display: flex; gap: 8px; margin: 16px 0; }
  .tab-btn { padding: 8px 16px; border-radius: 8px; border: 1px solid var(--border);
    background: var(--panel); color: var(--text); cursor: pointer; font-size: 13px; font-weight: 600; }
  .tab-btn.active { background: var(--accent); color: #fff; border-color: var(--accent); }
  .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 12px; margin-bottom: 18px; }
  .card { background: var(--panel); border: 1px solid var(--border); border-radius: 10px; padding: 14px 16px; box-shadow: var(--shadow); }
  .card .label { color: var(--text-dim); font-size: 12px; margin-bottom: 6px; }
  .card .value { font-size: 22px; font-weight: 700; }
  .card .sub { font-size: 12px; font-weight: 600; margin-top: 2px; }
  .card.buy .value { color: var(--buy-text); }
  .card.watch .value { color: var(--watch-text); }
  .card.sell .value { color: var(--sell-text); }
  .panel { background: var(--panel); border: 1px solid var(--border); border-radius: 10px; box-shadow: var(--shadow); padding: 16px; margin-bottom: 18px; }
  .panel h2 { font-size: 14px; margin: 0 0 12px; color: var(--text-dim); font-weight: 600; }
  .idx-head { display: flex; align-items: baseline; gap: 14px; flex-wrap: wrap; margin-bottom: 6px; }
  .idx-val { font-size: 30px; font-weight: 800; }
  .idx-chg { font-size: 14px; font-weight: 700; }
  .idx-meta { color: var(--text-dim); font-size: 12px; margin-left: auto; text-align: right; }
  .change.up { color: var(--up); } .change.down { color: var(--down); }
  svg.chart { width: 100%; height: 220px; display: block; }
  svg.trend { width: 100%; height: 56px; display: block; }
  .holdings { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }
  .chip { font-size: 11px; border: 1px solid var(--border); border-radius: 999px; padding: 3px 9px; background: var(--bg); color: var(--text-dim); }
  .controls { display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 14px; align-items: center; }
  .controls input[type=text] { flex: 1; min-width: 180px; padding: 8px 12px; border-radius: 8px;
    border: 1px solid var(--border); background: var(--bg); color: var(--text); font-size: 13px; }
  .controls select { padding: 8px 10px; border-radius: 8px; border: 1px solid var(--border);
    background: var(--bg); color: var(--text); font-size: 13px; }
  .filter-btn { padding: 7px 14px; border-radius: 999px; border: 1px solid var(--border); background: var(--bg);
    color: var(--text); cursor: pointer; font-size: 12px; font-weight: 600; }
  .filter-btn.active { background: var(--accent); color: #fff; border-color: var(--accent); }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th, td { padding: 9px 10px; text-align: right; border-bottom: 1px solid var(--border); white-space: nowrap; }
  th:first-child, td:first-child, th.left, td.left, td.reason { text-align: left; }
  th { color: var(--text-dim); font-weight: 600; cursor: pointer; user-select: none; position: sticky; top: 0; background: var(--panel); }
  th.sorted::after { content: " \25BC"; font-size: 9px; }
  th.sorted.asc::after { content: " \25B2"; }
  tbody tr:hover { background: var(--row-hover); }
  tbody tr.buy-row { background: var(--buy-bg); }
  .table-scroll { overflow-x: auto; max-height: 72vh; overflow-y: auto; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; font-weight: 700; }
  .badge.buy { background: var(--buy-bg); color: var(--buy-text); border: 1px solid var(--buy-border); }
  .badge.watch { background: var(--watch-bg); color: var(--watch-text); border: 1px solid var(--watch-border); }
  .badge.sell { background: var(--sell-bg); color: var(--sell-text); border: 1px solid var(--sell-border); }
  .badge.neutral { background: var(--neutral-bg); color: var(--neutral-text); border: 1px solid var(--neutral-border); }
  .bars { display: inline-flex; gap: 2px; vertical-align: middle; }
  .bars span { width: 5px; height: 12px; border-radius: 1px; background: var(--border); }
  .bars span.on { background: var(--accent); }
  .code { color: var(--text-dim); font-size: 12px; }
  .reason { max-width: 360px; white-space: normal; color: var(--text-dim); font-size: 12px; }
  .empty-msg { text-align: center; color: var(--text-dim); padding: 30px; }
  footer { color: var(--text-dim); font-size: 12px; margin-top: 24px; line-height: 1.7; }
  a.ext { color: var(--text-dim); text-decoration: none; }
  a.ext:hover { color: var(--accent); }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>멀티팩터 스크리너 — Trending Value–Quality</h1>
    <div class="subtitle" id="subtitle">가치(40%) + 모멘텀(30%) + 퀄리티(30%) 복합 팩터 · 실전 매수 신호가 아닌 스크리닝 참고 자료</div>
    <div class="nav">
      <a href="../">SEPA 추세템플릿</a>
      <a href="../range_vrebound/">RANGE-MR · V-REBOUND</a>
      <a class="here" href="./">멀티팩터</a>
    </div>
  </header>

  <div class="panel" id="indexPanel">
    <h2>스크리너 인덱스 (BUY 상위 종목 동일가중 · 순방향 추적)</h2>
    <div class="idx-head">
      <span class="idx-val" id="idxVal">-</span>
      <span class="idx-chg" id="idxChg"></span>
      <span class="idx-meta" id="idxMeta"></span>
    </div>
    <svg class="chart" id="idxChart"></svg>
    <div class="holdings" id="idxHoldings"></div>
  </div>

  <div class="cards" id="cards"></div>

  <div class="panel">
    <h2>실행일별 신호 수 추이</h2>
    <svg class="trend" id="trend"></svg>
  </div>

  <div class="tabs" id="tabs"></div>

  <div class="panel">
    <div class="controls">
      <input type="text" id="search" placeholder="종목코드 또는 종목명 검색...">
      <button class="filter-btn active" data-filter="all">전체</button>
      <button class="filter-btn" data-filter="BUY">BUY</button>
      <button class="filter-btn" data-filter="WATCH">WATCH</button>
      <button class="filter-btn" data-filter="SELL">SELL</button>
      <button class="filter-btn" data-filter="NEUTRAL">NEUTRAL</button>
      <select id="sortSelect">
        <option value="composite">종합점수순</option>
        <option value="value">가치점수순</option>
        <option value="momentum">모멘텀점수순</option>
        <option value="quality">퀄리티점수순</option>
        <option value="ret6m">6개월수익률순</option>
        <option value="symbol">종목코드순</option>
      </select>
    </div>
    <div class="table-scroll">
      <table id="table">
        <thead><tr id="thead-row"></tr></thead>
        <tbody id="tbody"></tbody>
      </table>
      <div class="empty-msg" id="emptyMsg" style="display:none;">검색 결과가 없습니다.</div>
    </div>
  </div>

  <footer id="footer"></footer>
</div>

<script>
const DATA = __DATA_JSON__;
const groupKeys = Object.keys(DATA.markets);
let currentGroup = groupKeys[0];
let currentFilter = "all";
let sortKey = "composite";
let sortDir = -1;

function fmt(n, d) {
  if (n === null || n === undefined || Number.isNaN(n)) return "-";
  return Number(n).toLocaleString("ko-KR", { maximumFractionDigits: d ?? 1, minimumFractionDigits: 0 });
}
function pct(n) {
  if (n === null || n === undefined) return "-";
  return (n >= 0 ? "+" : "") + (n * 100).toFixed(1) + "%";
}
function badgeClass(s) { return s ? s.toLowerCase() : "neutral"; }

function renderIndex() {
  const ix = DATA.index || {};
  const panel = document.getElementById("indexPanel");
  if (!ix.available) { panel.style.display = "none"; return; }
  document.getElementById("idxVal").textContent = fmt(ix.value, 2);
  const chg = ix.change || 0;
  const chgEl = document.getElementById("idxChg");
  chgEl.textContent = (chg >= 0 ? "▲ " : "▼ ") + (chg * 100).toFixed(2) + "% (직전 실행 대비)";
  chgEl.className = "idx-chg change " + (chg >= 0 ? "up" : "down");
  document.getElementById("idxMeta").innerHTML =
    `기준 ${fmt(ix.baseValue,0)} (${ix.baseDate || "-"})<br>` +
    `리밸런싱 ${ix.freq === "weekly" ? "주간" : "월간"} · 다음 ${ix.nextRebalance || "-"} · 상위 ${ix.topN ?? "-"}종목`;

  drawIndexChart(ix.history || []);

  const hold = ix.holdings || [];
  document.getElementById("idxHoldings").innerHTML = hold.length
    ? hold.map(h => `<span class="chip">${h.name} <b>${(h.weight*100).toFixed(1)}%</b></span>`).join("")
    : `<span class="chip">구성종목 없음 (이번 리밸런싱에서 BUY 신호 0개)</span>`;
}

function drawIndexChart(hist) {
  const svg = document.getElementById("idxChart");
  if (hist.length < 2) {
    svg.innerHTML = `<text x="8" y="28" fill="var(--text-dim)" font-size="12">지수 그래프는 2회 이상 실행이 쌓이면 표시됩니다 (현재 ${hist.length}회).</text>`;
    svg.setAttribute("viewBox", "0 0 1000 220");
    return;
  }
  const w = 1000, h = 220, padL = 52, padR = 12, padT = 12, padB = 22;
  const vals = hist.map(d => d.value);
  let lo = Math.min(...vals), hi = Math.max(...vals);
  if (lo === hi) { lo -= 1; hi += 1; }
  const mrg = (hi - lo) * 0.12; lo -= mrg; hi += mrg;
  const x = i => padL + i * (w - padL - padR) / (hist.length - 1);
  const y = v => padT + (hi - v) / (hi - lo) * (h - padT - padB);
  const line = hist.map((d, i) => (i ? "L" : "M") + x(i).toFixed(1) + "," + y(d.value).toFixed(1)).join(" ");
  const area = line + ` L${x(hist.length-1).toFixed(1)},${y(lo).toFixed(1)} L${x(0).toFixed(1)},${y(lo).toFixed(1)} Z`;
  const base = DATA.index.baseValue;
  const gridVals = [lo + (hi-lo)*0.15, (lo+hi)/2, hi - (hi-lo)*0.15];
  let g = "";
  gridVals.forEach(gv => {
    g += `<line x1="${padL}" y1="${y(gv).toFixed(1)}" x2="${w-padR}" y2="${y(gv).toFixed(1)}" stroke="var(--border)" stroke-width="1"/>`;
    g += `<text x="${padL-6}" y="${(y(gv)+3).toFixed(1)}" fill="var(--text-dim)" font-size="10" text-anchor="end">${fmt(gv,0)}</text>`;
  });
  if (base >= lo && base <= hi) {
    g += `<line x1="${padL}" y1="${y(base).toFixed(1)}" x2="${w-padR}" y2="${y(base).toFixed(1)}" stroke="var(--text-dim)" stroke-width="1" stroke-dasharray="4 3"/>`;
  }
  let marks = "";
  hist.forEach((d, i) => {
    if (d.isRebalance) marks += `<circle cx="${x(i).toFixed(1)}" cy="${y(d.value).toFixed(1)}" r="3.5" fill="var(--watch-text)"/>`;
  });
  const lastI = hist.length - 1;
  svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
  svg.innerHTML = `
    ${g}
    <path d="${area}" fill="var(--accent)" opacity="0.08"/>
    <path d="${line}" fill="none" stroke="var(--accent)" stroke-width="2"/>
    ${marks}
    <circle cx="${x(lastI).toFixed(1)}" cy="${y(vals[lastI]).toFixed(1)}" r="3.5" fill="var(--accent)"/>
    <text x="${padL}" y="${h-6}" fill="var(--text-dim)" font-size="10">${hist[0].date}</text>
    <text x="${w-padR}" y="${h-6}" fill="var(--text-dim)" font-size="10" text-anchor="end">${hist[lastI].date}</text>
  `;
}

function renderCards() {
  const c = DATA.counts;
  const cards = [
    ["기준일", DATA.asOf || "-", "", ""],
    ["대상 종목", DATA.total, "", ""],
    ["BUY", c.BUY, "buy", ""],
    ["WATCH", c.WATCH, "watch", ""],
    ["SELL", c.SELL, "sell", ""],
    ["NEUTRAL", c.NEUTRAL, "neutral", ""],
  ];
  document.getElementById("cards").innerHTML = cards.map(([l, v, cls]) =>
    `<div class="card ${cls}"><div class="label">${l}</div><div class="value">${v}</div></div>`
  ).join("");
  document.getElementById("subtitle").textContent =
    `기준일 ${DATA.asOf} · 생성 ${DATA.generatedAt} · 가치(40%)+모멘텀(30%)+퀄리티(30%) 복합 팩터 · 실전 매수 신호가 아닌 스크리닝 참고 자료`;
}

function renderTrend() {
  const hist = DATA.signalHistory || [];
  const svg = document.getElementById("trend");
  if (hist.length < 2) {
    svg.innerHTML = `<text x="8" y="30" fill="var(--text-dim)" font-size="12">추이는 2회 이상 실행이 쌓이면 표시됩니다 (현재 ${hist.length}회).</text>`;
    return;
  }
  const w = 1000, h = 56, pad = 4;
  const vals = hist.map(d => d.BUY || 0);
  const max = Math.max(...vals, 1);
  const stepX = (w - pad * 2) / (hist.length - 1);
  const pts = vals.map((v, i) => [pad + i * stepX, h - pad - (v / max) * (h - pad * 2)]);
  const path = pts.map((p, i) => (i === 0 ? "M" : "L") + p[0].toFixed(1) + "," + p[1].toFixed(1)).join(" ");
  const last = pts[pts.length - 1];
  svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
  svg.innerHTML = `
    <path d="${path}" fill="none" stroke="var(--buy-text)" stroke-width="2"/>
    <circle cx="${last[0]}" cy="${last[1]}" r="3" fill="var(--buy-text)"/>
    <text x="${pad}" y="${h-2}" fill="var(--text-dim)" font-size="10">${hist[0].date}</text>
    <text x="${w-pad}" y="${h-2}" fill="var(--text-dim)" font-size="10" text-anchor="end">${hist[hist.length-1].date} · BUY ${vals[vals.length-1]}건</text>
  `;
}

function renderTabs() {
  const el = document.getElementById("tabs");
  el.innerHTML = groupKeys.map(k =>
    `<button class="tab-btn${k===currentGroup?" active":""}" data-g="${k}">${DATA.markets[k].label} (${DATA.markets[k].rows.length})</button>`
  ).join("");
  el.querySelectorAll(".tab-btn").forEach(b =>
    b.addEventListener("click", () => { currentGroup = b.dataset.g; renderTabs(); renderTable(); }));
}

function bars(score) {
  const n = Math.round((score || 0) / 20);
  let s = '<span class="bars">';
  for (let i = 0; i < 5; i++) s += `<span class="${i < n ? "on" : ""}"></span>`;
  return s + "</span>";
}

function extLink(r) {
  const url = /^\d{6}$/.test(r.symbol)
    ? `https://finance.naver.com/item/main.naver?code=${r.symbol}`
    : `https://finance.yahoo.com/quote/${encodeURIComponent(r.symbol)}`;
  return ` <a class="ext" href="${url}" target="_blank" rel="noopener noreferrer" title="외부 차트">↗</a>`;
}

function renderTable() {
  const q = document.getElementById("search").value.trim().toLowerCase();
  let rows = DATA.markets[currentGroup].rows.slice();
  if (currentFilter !== "all") rows = rows.filter(r => r.signal === currentFilter);
  if (q) rows = rows.filter(r => (r.symbol + " " + r.name).toLowerCase().includes(q));

  rows.sort((a, b) => {
    const av = a[sortKey], bv = b[sortKey];
    if (av === null || av === undefined) return 1;
    if (bv === null || bv === undefined) return -1;
    if (typeof av === "string") return sortDir * av.localeCompare(bv);
    return sortDir * (av - bv);
  });

  const cols = [
    { key: "symbol", label: "종목", left: true, fmt: (v, r) => `${r.name} <span class="code">${v}</span>${extLink(r)}` },
    { key: "signal", label: "신호", fmt: v => `<span class="badge ${badgeClass(v)}">${v}</span>` },
    { key: "composite", label: "종합", fmt: v => `${fmt(v)} ${bars(v)}` },
    { key: "value", label: "가치", fmt: v => fmt(v) },
    { key: "momentum", label: "모멘텀", fmt: v => fmt(v) },
    { key: "quality", label: "퀄리티", fmt: v => fmt(v) },
    { key: "price", label: "현재가", fmt: v => fmt(v, 2) },
    { key: "ret6m", label: "6M", fmt: v => `<span class="change ${v>=0?"up":"down"}">${pct(v)}</span>` },
    { key: "per", label: "PER", fmt: v => fmt(v, 1) },
    { key: "pbr", label: "PBR", fmt: v => fmt(v, 2) },
    { key: "roe", label: "ROE%", fmt: v => fmt(v, 1) },
    { key: "debtToEquity", label: "부채%", fmt: v => fmt(v, 0) },
    { key: "reason", label: "판정 사유", left: true, fmt: v => `<span class="reason">${v || ""}</span>` },
  ];

  const thead = document.getElementById("thead-row");
  thead.innerHTML = cols.map(c =>
    `<th class="${c.left ? "left" : ""}${c.key===sortKey?" sorted"+(sortDir===1?" asc":""):""}" data-key="${c.key}">${c.label}</th>`
  ).join("");
  thead.querySelectorAll("th").forEach(th => th.addEventListener("click", () => {
    const k = th.dataset.key;
    if (k === "reason") return;
    if (sortKey === k) sortDir *= -1; else { sortKey = k; sortDir = (k === "symbol") ? 1 : -1; }
    renderTable();
  }));

  const tbody = document.getElementById("tbody");
  document.getElementById("emptyMsg").style.display = rows.length ? "none" : "block";
  tbody.innerHTML = rows.map(r => {
    const cells = cols.map(c => {
      const v = r[c.key];
      const content = c.fmt ? c.fmt(v, r) : (v ?? "-");
      return `<td class="${c.left ? "left" : ""}${c.key==="reason"?" reason":""}">${content}</td>`;
    }).join("");
    return `<tr class="${r.signal === "BUY" ? "buy-row" : ""}">${cells}</tr>`;
  }).join("");
}

function renderFooter() {
  document.getElementById("footer").innerHTML = `
    ※ 팩터 정의·신호 임계값은 저장소의 <b>STRATEGY.md</b> 참고. 각 팩터는 유니버스 내 0~100 백분위 순위로 환산 후 결합한다.<br>
    ※ <b>스크리너 인덱스</b>는 BUY 신호 상위 N종목을 동일가중한 <b>실시간 순방향 추적</b> 지수이며 과거 백테스트가 아니다.
       지수값은 거래비용·세금·슬리피지가 반영되지 않은 이론치이고, 매 실행 시점 종가를 사용하므로 실행 빈도·시각에 따라 경로가 달라질 수 있다.
       주황색 점은 리밸런싱일, 점선은 기준값(1000)이다.<br>
    ※ 한국 재무지표는 네이버 금융, 미국은 yfinance 에서 수집한다. EV/EBITDA 는 한국 종목에서 무료 소스가 없어 제외된다.
       재무지표(최근 연간)와 가격·모멘텀(당일)의 시점이 혼재하므로 정밀 분석용이 아닌 현재 시점 스크리닝용이다.<br>
    ※ 이 페이지는 매수 추천이 아니다. BUY/WATCH/SELL 은 규칙 기반 분류일 뿐이며, 스테이지·촉매·포지션 관리는 별도로 판단해야 한다.
  `;
}

function renderAll() {
  renderIndex();
  renderCards();
  renderTrend();
  renderTabs();
  renderTable();
  renderFooter();
}

document.getElementById("search").addEventListener("input", renderTable);
document.querySelectorAll(".filter-btn").forEach(btn => btn.addEventListener("click", () => {
  document.querySelectorAll(".filter-btn").forEach(b => b.classList.remove("active"));
  btn.classList.add("active");
  currentFilter = btn.dataset.filter;
  renderTable();
}));
document.getElementById("sortSelect").addEventListener("change", e => {
  sortKey = e.target.value; sortDir = (sortKey === "symbol") ? 1 : -1; renderTable();
});

if (groupKeys.length === 0) {
  document.querySelector(".wrap").innerHTML = '<div class="empty-msg">아직 결과가 없습니다. python -m screener.main 을 먼저 실행하세요.</div>';
} else {
  renderAll();
}
</script>
</body>
</html>
"""


if __name__ == "__main__":
    build()
