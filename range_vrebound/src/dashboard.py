"""정적 HTML 대시보드 생성 (스펙 32조 Phase 10).

SEPA의 `generate_dashboard.py`(자기완결적 단일 HTML, 외부 CDN 없음)와
같은 방식을 쓴다. 다만 SEPA는 CSV 스냅샷을 별도 JSON으로 누적하지만,
여기서는 SQLite DB 자체가 날짜별 이력을 전부 갖고 있으므로(Phase 9 —
매 실행마다 DB를 저장소에 커밋) 스냅샷 파일 없이 DB를 직접 조회해서
"최신일" 현황과 "일별 추이"를 둘 다 만든다.

payload 조립(`build_payload`)과 HTML 렌더링(`render_html`)을 분리해
데이터 가공 로직만 네트워크/DB 없이 테스트한다.
"""
from __future__ import annotations

import json
from pathlib import Path

from src.models.signal import Signal, SignalState, StrategyName

STRATEGY_LABELS = {
    StrategyName.RANGE_MR: "RANGE-MR (박스권 평균회귀)",
    StrategyName.V_REBOUND: "V-REBOUND (급락 후 반등)",
}


def _enum_value(v) -> str:
    return v.value if hasattr(v, "value") else str(v)


def _signal_to_row(signal: Signal) -> dict:
    return {
        "symbol": signal.symbol,
        "date": signal.date.isoformat(),
        "marketRegime": _enum_value(signal.market_regime),
        "setupScore": signal.setup_score,
        "triggerScore": signal.trigger_score,
        "totalScore": signal.total_score,
        "signal": _enum_value(signal.signal),
        "qualityStatus": _enum_value(signal.quality_status),
        "entry": signal.entry,
        "stop": signal.stop,
        "target1": signal.target_1,
        "target2": signal.target_2,
        "rr1": signal.rr_1,
        "rr2": signal.rr_2,
        "reasons": signal.reasons,
    }


def build_payload(signals: list[Signal]) -> dict:
    """전략별로 (최신일 현황 rows, 일별 추이 history)를 만든다.

    데이터가 전혀 없는 전략은 payload에 아예 넣지 않는다 — 프런트에서
    탭 자체를 숨기기 위함이다(SEPA와 동일한 관례).
    """
    by_strategy: dict[StrategyName, list[Signal]] = {}
    for s in signals:
        by_strategy.setdefault(s.strategy, []).append(s)

    payload: dict[str, dict] = {}
    for strategy, label in STRATEGY_LABELS.items():
        strat_signals = by_strategy.get(strategy, [])
        if not strat_signals:
            continue

        latest_date = max(s.date for s in strat_signals)
        current_rows = [_signal_to_row(s) for s in strat_signals if s.date == latest_date]

        by_date: dict[str, dict] = {}
        for s in strat_signals:
            d = s.date.isoformat()
            entry = by_date.setdefault(d, {"date": d, "total": 0, "buyCandidate": 0})
            entry["total"] += 1
            if s.signal == SignalState.BUY_CANDIDATE:
                entry["buyCandidate"] += 1
        history = sorted(by_date.values(), key=lambda h: h["date"])

        payload[strategy.value] = {
            "label": label,
            "rows": current_rows,
            "history": history,
            "asOf": latest_date.isoformat(),
        }
    return payload


def render_html(payload: dict) -> str:
    data_json = json.dumps(payload, ensure_ascii=False)
    return HTML_TEMPLATE.replace("__DATA_JSON__", data_json)


def build(signals: list[Signal], output_path: Path) -> None:
    payload = build_payload(signals)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_html(payload), encoding="utf-8")


HTML_TEMPLATE = r"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>RANGE-MR / V-REBOUND 스크리너</title>
<style>
  :root {
    --bg: #f5f6f8; --panel: #ffffff; --border: #e3e5e9;
    --text: #1b1e24; --text-dim: #6b7280; --accent: #2563eb;
    --buy-bg: #e6f7ec; --buy-text: #157347; --buy-border: #b7e4c7;
    --watch-bg: #fff2e0; --watch-text: #b45309; --watch-border: #fbd9a8;
    --setup-bg: #eef0ff; --setup-text: #4338ca; --setup-border: #c7cbfa;
    --inval-bg: #fdf2f2; --inval-text: #b42318; --inval-border: #f3c6c2;
    --up: #d92b2b; --down: #1a56db;
    --row-hover: #f0f4ff; --shadow: 0 1px 3px rgba(0,0,0,0.06), 0 1px 2px rgba(0,0,0,0.04);
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #0f1115; --panel: #171a21; --border: #2a2e37;
      --text: #e7e9ee; --text-dim: #9aa2b1; --accent: #5b8def;
      --buy-bg: #113322; --buy-text: #6bd08a; --buy-border: #1e5c3a;
      --watch-bg: #3a2712; --watch-text: #f6b96a; --watch-border: #6b4a1f;
      --setup-bg: #201f42; --setup-text: #a5b0fc; --setup-border: #3c3a72;
      --inval-bg: #3a1717; --inval-text: #f2a4a0; --inval-border: #6b2b26;
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
  header { margin-bottom: 20px; }
  h1 { font-size: 20px; margin: 0 0 4px; }
  .subtitle { color: var(--text-dim); font-size: 13px; }
  .tabs { display: flex; gap: 8px; margin: 16px 0; }
  .tab-btn {
    padding: 8px 16px; border-radius: 8px; border: 1px solid var(--border);
    background: var(--panel); color: var(--text); cursor: pointer; font-size: 13px; font-weight: 600;
  }
  .tab-btn.active { background: var(--accent); color: #fff; border-color: var(--accent); }
  .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; margin-bottom: 18px; }
  .card { background: var(--panel); border: 1px solid var(--border); border-radius: 10px; padding: 14px 16px; box-shadow: var(--shadow); }
  .card .label { color: var(--text-dim); font-size: 12px; margin-bottom: 6px; }
  .card .value { font-size: 22px; font-weight: 700; }
  .panel { background: var(--panel); border: 1px solid var(--border); border-radius: 10px; box-shadow: var(--shadow); padding: 16px; margin-bottom: 18px; }
  .panel h2 { font-size: 14px; margin: 0 0 12px; color: var(--text-dim); font-weight: 600; }
  .controls { display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 14px; align-items: center; }
  .controls input[type=text] {
    flex: 1; min-width: 180px; padding: 8px 12px; border-radius: 8px; border: 1px solid var(--border);
    background: var(--bg); color: var(--text); font-size: 13px;
  }
  .controls select {
    padding: 8px 10px; border-radius: 8px; border: 1px solid var(--border);
    background: var(--bg); color: var(--text); font-size: 13px;
  }
  .filter-btn { padding: 7px 14px; border-radius: 999px; border: 1px solid var(--border); background: var(--bg); color: var(--text); cursor: pointer; font-size: 12px; font-weight: 600; }
  .filter-btn.active { background: var(--accent); color: #fff; border-color: var(--accent); }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th, td { padding: 9px 10px; text-align: right; border-bottom: 1px solid var(--border); white-space: nowrap; }
  th:first-child, td:first-child, th.left, td.left, td.reasons { text-align: left; }
  th { color: var(--text-dim); font-weight: 600; cursor: pointer; user-select: none; position: sticky; top: 0; background: var(--panel); }
  th.sorted::after { content: " \25BC"; font-size: 9px; }
  th.sorted.asc::after { content: " \25B2"; }
  tbody tr:hover { background: var(--row-hover); }
  tbody tr.buy-row { background: var(--buy-bg); }
  .table-scroll { overflow-x: auto; max-height: 70vh; overflow-y: auto; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; font-weight: 700; white-space: nowrap; }
  .badge.buy { background: var(--buy-bg); color: var(--buy-text); border: 1px solid var(--buy-border); }
  .badge.watch { background: var(--watch-bg); color: var(--watch-text); border: 1px solid var(--watch-border); }
  .badge.setup { background: var(--setup-bg); color: var(--setup-text); border: 1px solid var(--setup-border); }
  .badge.inval { background: var(--inval-bg); color: var(--inval-text); border: 1px solid var(--inval-border); }
  .reasons { max-width: 320px; white-space: normal; color: var(--text-dim); font-size: 12px; }
  .code { color: var(--text-dim); font-size: 12px; }
  .empty-msg { text-align: center; color: var(--text-dim); padding: 30px; }
  footer { color: var(--text-dim); font-size: 12px; margin-top: 24px; line-height: 1.7; }
  svg.trend { width: 100%; height: 60px; display: block; }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>RANGE-MR / V-REBOUND 스크리너</h1>
    <div class="subtitle" id="subtitle">박스권 평균회귀(RANGE-MR) · 급락 후 반등(V-REBOUND) — 실전 매수 신호가 아닌 스크리닝 참고 자료</div>
  </header>

  <div class="tabs" id="tabs"></div>

  <div class="cards" id="cards"></div>

  <div class="panel">
    <h2>일별 BUY_CANDIDATE 수 추이</h2>
    <svg class="trend" id="trend"></svg>
  </div>

  <div class="panel">
    <div class="controls">
      <input type="text" id="search" placeholder="종목코드 검색...">
      <button class="filter-btn active" data-filter="all">전체</button>
      <button class="filter-btn" data-filter="BUY_CANDIDATE">BUY_CANDIDATE만</button>
      <button class="filter-btn" data-filter="WATCH_TRIGGER">WATCH/TRIGGER만</button>
      <button class="filter-btn" data-filter="SETUP">SETUP만</button>
      <select id="sortSelect">
        <option value="totalScore">총점순</option>
        <option value="rr1">R/R순</option>
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

  <footer>
    ※ 이 표는 스크리닝 신호를 나열할 뿐 매수 추천이 아닙니다. entry는 신호 발생일(T) 다음 거래일 시가로 체결한다는 가정 하의 값입니다(백테스트 기준) — 이 화면의 값은 실시간 신호 시점 참고용 stop/target/RR입니다.<br>
    ※ SETUP/WATCH/TRIGGER/BUY_CANDIDATE/INVALIDATED 분류와 배점 산식은 range_vrebound/README.md 및 config/strategy_config.yaml을 참고하세요. Quality(펀더멘털) 항목은 데이터 소스가 없어 항상 UNKNOWN이며 총점에서 제외 후 재환산됩니다.
  </footer>
</div>

<script>
const DATA = __DATA_JSON__;
const strategyKeys = Object.keys(DATA);
let currentStrategy = strategyKeys[0];
let currentFilter = "all";
let sortKey = "totalScore";
let sortDir = -1;

function fmtNum(n, digits) {
  if (n === null || n === undefined) return "-";
  return Number(n).toLocaleString("ko-KR", { maximumFractionDigits: digits ?? 1, minimumFractionDigits: 0 });
}

function renderTabs() {
  const el = document.getElementById("tabs");
  if (strategyKeys.length <= 1) { el.style.display = "none"; return; }
  el.innerHTML = strategyKeys.map(k =>
    `<button class="tab-btn${k===currentStrategy?" active":""}" data-strategy="${k}">${DATA[k].label}</button>`
  ).join("");
  el.querySelectorAll(".tab-btn").forEach(btn => {
    btn.addEventListener("click", () => { currentStrategy = btn.dataset.strategy; renderAll(); });
  });
}

function signalBadgeClass(s) {
  if (s === "BUY_CANDIDATE") return "buy";
  if (s === "WATCH" || s === "TRIGGER") return "watch";
  if (s === "INVALIDATED") return "inval";
  return "setup";
}

function renderCards() {
  const strat = DATA[currentStrategy];
  const rows = strat.rows;
  const counts = { BUY_CANDIDATE: 0, WATCH: 0, TRIGGER: 0, SETUP: 0, INVALIDATED: 0 };
  rows.forEach(r => { if (counts[r.signal] !== undefined) counts[r.signal]++; });
  const cards = [
    ["기준일", strat.asOf || "-"],
    ["전체 신호", rows.length],
    ["BUY_CANDIDATE", counts.BUY_CANDIDATE],
    ["WATCH/TRIGGER", counts.WATCH + counts.TRIGGER],
    ["SETUP", counts.SETUP],
  ];
  document.getElementById("cards").innerHTML = cards.map(([label, value]) =>
    `<div class="card"><div class="label">${label}</div><div class="value">${value}</div></div>`
  ).join("");
  document.getElementById("subtitle").textContent =
    `기준일: ${strat.asOf || "-"} · ${strat.label} · 박스권 평균회귀(RANGE-MR) · 급락 후 반등(V-REBOUND) — 실전 매수 신호가 아닌 스크리닝 참고 자료`;
}

function renderTrend() {
  const hist = DATA[currentStrategy].history;
  const svg = document.getElementById("trend");
  if (hist.length < 2) {
    svg.innerHTML = `<text x="8" y="30" fill="var(--text-dim)" font-size="12">추세를 보려면 이틀 이상의 데이터가 쌓여야 합니다 (현재 ${hist.length}일치).</text>`;
    return;
  }
  const w = 1000, h = 60, pad = 4;
  const vals = hist.map(d => d.buyCandidate ?? 0);
  const max = Math.max(...vals, 1);
  const stepX = (w - pad * 2) / (hist.length - 1);
  const pts = vals.map((v, i) => [pad + i * stepX, h - pad - (v / max) * (h - pad * 2)]);
  const path = pts.map((p, i) => (i === 0 ? "M" : "L") + p[0].toFixed(1) + "," + p[1].toFixed(1)).join(" ");
  const lastPt = pts[pts.length - 1];
  svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
  svg.innerHTML = `
    <path d="${path}" fill="none" stroke="var(--accent)" stroke-width="2"/>
    <circle cx="${lastPt[0]}" cy="${lastPt[1]}" r="3" fill="var(--accent)"/>
    <text x="${pad}" y="${h-2}" fill="var(--text-dim)" font-size="10">${hist[0].date}</text>
    <text x="${w-pad}" y="${h-2}" fill="var(--text-dim)" font-size="10" text-anchor="end">${hist[hist.length-1].date} (${vals[vals.length-1]}건)</text>
  `;
}

function renderTable() {
  const q = document.getElementById("search").value.trim().toLowerCase();
  let rows = DATA[currentStrategy].rows.slice();

  if (currentFilter === "BUY_CANDIDATE") rows = rows.filter(r => r.signal === "BUY_CANDIDATE");
  else if (currentFilter === "WATCH_TRIGGER") rows = rows.filter(r => r.signal === "WATCH" || r.signal === "TRIGGER");
  else if (currentFilter === "SETUP") rows = rows.filter(r => r.signal === "SETUP");

  if (q) rows = rows.filter(r => (r.symbol || "").toLowerCase().includes(q));

  rows.sort((a, b) => {
    const av = a[sortKey], bv = b[sortKey];
    if (av === null || av === undefined) return 1;
    if (bv === null || bv === undefined) return -1;
    if (typeof av === "string") return sortDir * av.localeCompare(bv);
    return sortDir * (av - bv);
  });

  const cols = [
    { key: "symbol", label: "종목코드", left: true },
    { key: "signal", label: "판정", fmt: v => `<span class="badge ${signalBadgeClass(v)}">${v}</span>` },
    { key: "marketRegime", label: "시장레짐" },
    { key: "totalScore", label: "총점", fmt: v => fmtNum(v) },
    { key: "triggerScore", label: "트리거점수", fmt: v => v != null ? fmtNum(v) : "-" },
    { key: "qualityStatus", label: "품질" },
    { key: "stop", label: "손절", fmt: v => v != null ? fmtNum(v, 0) : "-" },
    { key: "target1", label: "목표1", fmt: v => v != null ? fmtNum(v, 0) : "-" },
    { key: "target2", label: "목표2", fmt: v => v != null ? fmtNum(v, 0) : "-" },
    { key: "rr1", label: "R/R", fmt: v => v != null ? fmtNum(v, 2) : "-" },
    { key: "reasons", label: "근거", fmt: v => `<span class="reasons">${(v || []).join(" · ")}</span>` },
  ];

  const thead = document.getElementById("thead-row");
  thead.innerHTML = cols.map(c =>
    `<th class="${c.left ? "left" : ""}${c.key===sortKey?" sorted"+(sortDir===1?" asc":""):""}" data-key="${c.key}">${c.label}</th>`
  ).join("");
  thead.querySelectorAll("th").forEach(th => {
    th.addEventListener("click", () => {
      const key = th.dataset.key;
      if (key === "reasons") return;
      if (sortKey === key) sortDir *= -1; else { sortKey = key; sortDir = -1; }
      const opt = document.getElementById("sortSelect");
      if (["totalScore","rr1","symbol"].includes(key)) opt.value = key;
      renderTable();
    });
  });

  const tbody = document.getElementById("tbody");
  document.getElementById("emptyMsg").style.display = rows.length ? "none" : "block";
  tbody.innerHTML = rows.map(r => {
    const cells = cols.map(c => {
      const v = r[c.key];
      const content = c.fmt ? c.fmt(v, r) : (v ?? "-");
      return `<td class="${c.left ? "left" : ""}${c.key==="reasons"?" reasons":""}">${content}</td>`;
    }).join("");
    return `<tr class="${r.signal === "BUY_CANDIDATE" ? "buy-row" : ""}">${cells}</tr>`;
  }).join("");
}

function renderAll() {
  renderTabs();
  renderCards();
  renderTrend();
  renderTable();
}

document.getElementById("search").addEventListener("input", renderTable);
document.querySelectorAll(".filter-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".filter-btn").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    currentFilter = btn.dataset.filter;
    renderTable();
  });
});
document.getElementById("sortSelect").addEventListener("change", (e) => {
  sortKey = e.target.value; sortDir = -1; renderTable();
});

if (strategyKeys.length === 0) {
  document.querySelector(".wrap").innerHTML = '<div class="empty-msg">아직 저장된 신호가 없습니다. run_daily_screen.py를 먼저 실행하세요.</div>';
} else {
  renderAll();
}
</script>
</body>
</html>
"""
