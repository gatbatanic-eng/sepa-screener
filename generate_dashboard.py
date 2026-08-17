"""
SEPA 스크리닝 결과를 정적 HTML 대시보드(docs/index.html)로 만든다.

- 입력: screening.py가 이미 만들어 둔 output/latest_{kr,us}_full.csv
  (즉 이 스크립트는 새로 시세를 조회하지 않는다. screening.py 실행 후에 돌린다.)
- 한국/미국은 서로 다른 스케줄로 실행되므로, 한 번의 실행에는 보통 한
  시장의 CSV만 새로 생긴다. 그래서 시장별 최신 스냅샷을
  docs/data/latest_{kr,us}.json 에 저장소 커밋으로 남겨두고, 이번 실행에
  없는 시장은 그 스냅샷을 그대로 이어서 사용해 두 시장이 한 페이지에
  계속 같이 보이게 한다.
- docs/data/history_{kr,us}.json 에 날짜별 요약을 하루 한 줄씩 누적하고,
  대시보드에서 통과 종목 수 추이로 보여준다.
- 결과는 docs/index.html 하나로 자기완결적(외부 CDN/폰트 없음)이라
  GitHub Pages(=docs 폴더 서빙)에 그대로 올리면 된다.

실행 방법
---------
    python screening.py --market KR   # 또는 US, ALL
    python generate_dashboard.py
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"
DOCS_DIR = BASE_DIR / "docs"
DATA_DIR = DOCS_DIR / "data"

MARKETS = {
    "kr": "한국 (코스피/코스닥)",
    "us": "미국 (S&P500)",
}

COLUMN_MAP = {
    "종목코드": "code", "종목명": "name", "시장": "market", "시가총액": "marcap",
    "상태": "status", "제외사유": "reason",
    "종가": "close", "SMA50": "sma50", "SMA150": "sma150", "SMA200": "sma200",
    "52주최고가": "high52w", "52주최저가": "low52w", "52주고점대비_참고용": "high52wPosition",
    "조건1_150200위": "c1", "조건2_150위200": "c2", "조건3_200상승중": "c3",
    "조건4_50위150200": "c4", "조건5_종가위50": "c5", "조건6_저가대비30pct이상": "c6",
    "조건7_고가대비25pct이내": "c7", "조건8_RS랭킹70이상_대체지표": "c8",
    "RS_백분위랭킹": "rsRank",
    "RS_3개월-12개월차_참고용": "rsMomentumDiff", "RS상승중_참고용": "rsRising",
    "충족조건수(8개중, 참고용)": "metCount",
    "전체통과(8개AND)": "passAll",
    "거래량": "volume", "SMA50거래량": "volSma50",
    "Dryup비율_참고용": "dryupRatio", "돌파거래량배율_참고용": "breakoutVolRatio",
    "VCP수축비율_근사치": "vcpRatio", "VCP형성중_근사치": "vcpForming",
    "피벗": "pivot", "피벗대비위치_참고용": "pivotPosition", "피벗임박_참고용": "pivotNear",
    "셋업점수_참고용_매수신호아님": "setupScore",
    "관찰후보_참고용_매수신호아님": "watchCandidate",
    "돌파_참고용_매수신호아님": "breakoutSignal",
}


def load_fresh_rows(prefix: str) -> list | None:
    """이번 실행이 이 시장을 스크리닝했다면 output/의 CSV에서 읽는다."""
    path = OUTPUT_DIR / f"latest_{prefix}_full.csv"
    if not path.exists():
        return None
    df = pd.read_csv(path)
    df = df.rename(columns=COLUMN_MAP)
    keep = [c for c in COLUMN_MAP.values() if c in df.columns]
    df = df[keep]
    df = df.astype(object).where(pd.notnull(df), None)  # float64 컬럼은 object로 먼저 바꿔야 NaN->None이 실제로 반영됨
    return df.to_dict(orient="records")


def load_snapshot(prefix: str) -> list | None:
    """이번 실행에 이 시장 데이터가 없으면, 저장소에 커밋되어 있던 지난 스냅샷을 이어서 쓴다."""
    path = DATA_DIR / f"latest_{prefix}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_snapshot(prefix: str, rows: list) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / f"latest_{prefix}.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")


def upsert_history(prefix: str, run_date: str, rows: list) -> list:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    hist_path = DATA_DIR / f"history_{prefix}.json"
    history = json.loads(hist_path.read_text(encoding="utf-8")) if hist_path.exists() else []

    ok_rows = [r for r in rows if r.get("status") == "OK"]
    pass_rows = [r for r in rows if r.get("passAll") is True]
    rs_vals = [r["rsRank"] for r in ok_rows if r.get("rsRank") is not None]
    entry = {
        "date": run_date,
        "total": len(rows),
        "ok": len(ok_rows),
        "excluded": len(rows) - len(ok_rows),
        "pass": len(pass_rows),
        "avgRs": round(sum(rs_vals) / len(rs_vals), 2) if rs_vals else None,
    }

    history = [h for h in history if h["date"] != run_date]  # 같은 날 재실행 시 갱신
    history.append(entry)
    history.sort(key=lambda h: h["date"])
    history = history[-180:]  # 파일 크기 관리를 위해 최근 180일(거래일 기준 약 8~9개월)만 보관

    hist_path.write_text(json.dumps(history, ensure_ascii=False), encoding="utf-8")
    return history


def load_history(prefix: str) -> list:
    hist_path = DATA_DIR / f"history_{prefix}.json"
    return json.loads(hist_path.read_text(encoding="utf-8")) if hist_path.exists() else []


def build() -> None:
    run_date = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d")
    payload: dict[str, dict] = {}

    for prefix, label in MARKETS.items():
        fresh_rows = load_fresh_rows(prefix)
        if fresh_rows is not None:
            save_snapshot(prefix, fresh_rows)
            history = upsert_history(prefix, run_date, fresh_rows)
            rows = fresh_rows
        else:
            rows = load_snapshot(prefix)
            if rows is None:
                continue  # 이번 실행에도, 과거 스냅샷에도 이 시장 데이터가 아예 없음
            history = load_history(prefix)

        as_of = history[-1]["date"] if history else None
        payload[prefix] = {"label": label, "rows": rows, "history": history, "asOf": as_of}

    if not payload:
        print("생성할 데이터가 없습니다 (output/latest_*_full.csv를 먼저 만들어야 함: screening.py를 먼저 실행하세요)")
        return

    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    (DOCS_DIR / "index.html").write_text(render_html(payload), encoding="utf-8")
    print(f"대시보드 생성 완료: {DOCS_DIR / 'index.html'} (대상: {', '.join(payload.keys())})")


def render_html(payload: dict) -> str:
    data_json = json.dumps(payload, ensure_ascii=False)
    return HTML_TEMPLATE.replace("__DATA_JSON__", data_json)


HTML_TEMPLATE = r"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SEPA 추세 템플릿 스크리너</title>
<style>
  :root {
    --bg: #f5f6f8; --panel: #ffffff; --border: #e3e5e9;
    --text: #1b1e24; --text-dim: #6b7280; --accent: #2563eb;
    --pass-bg: #e6f7ec; --pass-text: #157347; --pass-border: #b7e4c7;
    --fail-bg: #f8f9fa; --na-bg: #fdf2f2; --na-text: #b42318;
    --watch-bg: #eef0ff; --watch-text: #4338ca; --watch-border: #c7cbfa;
    --breakout-bg: #fff2e0; --breakout-text: #b45309; --breakout-border: #fbd9a8;
    --row-hover: #f0f4ff; --shadow: 0 1px 3px rgba(0,0,0,0.06), 0 1px 2px rgba(0,0,0,0.04);
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #0f1115; --panel: #171a21; --border: #2a2e37;
      --text: #e7e9ee; --text-dim: #9aa2b1; --accent: #5b8def;
      --pass-bg: #113322; --pass-text: #6bd08a; --pass-border: #1e5c3a;
      --fail-bg: #171a21; --na-bg: #3a1717; --na-text: #f2a4a0;
      --watch-bg: #201f42; --watch-text: #a5b0fc; --watch-border: #3c3a72;
      --breakout-bg: #3a2712; --breakout-text: #f6b96a; --breakout-border: #6b4a1f;
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
  th:first-child, td:first-child, th.left, td.left { text-align: left; }
  th { color: var(--text-dim); font-weight: 600; cursor: pointer; user-select: none; position: sticky; top: 0; background: var(--panel); }
  th.sorted::after { content: " \25BC"; font-size: 9px; }
  th.sorted.asc::after { content: " \25B2"; }
  tbody tr:hover { background: var(--row-hover); }
  tbody tr.pass-row { background: var(--pass-bg); }
  .table-scroll { overflow-x: auto; max-height: 70vh; overflow-y: auto; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; font-weight: 700; }
  .badge.pass { background: var(--pass-bg); color: var(--pass-text); border: 1px solid var(--pass-border); }
  .badge.fail { background: var(--fail-bg); color: var(--text-dim); border: 1px solid var(--border); }
  .badge.na { background: var(--na-bg); color: var(--na-text); }
  .badge.watch { background: var(--watch-bg); color: var(--watch-text); border: 1px solid var(--watch-border); }
  .badge.breakout { background: var(--breakout-bg); color: var(--breakout-text); border: 1px solid var(--breakout-border); }
  .metbar { display: inline-flex; gap: 2px; vertical-align: middle; }
  .metbar span { width: 6px; height: 12px; border-radius: 1px; background: var(--border); }
  .metbar span.on { background: var(--accent); }
  .code { color: var(--text-dim); font-size: 12px; }
  .empty-msg { text-align: center; color: var(--text-dim); padding: 30px; }
  footer { color: var(--text-dim); font-size: 12px; margin-top: 24px; line-height: 1.7; }
  svg.trend { width: 100%; height: 60px; display: block; }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>SEPA 추세 템플릿 스크리너</h1>
    <div class="subtitle" id="subtitle">미너비니 추세 템플릿 8개 조건 1차 필터 + 타이밍 참고 지표 · 스테이지/베이스단계/펀더멘털/매매신호는 다루지 않음</div>
  </header>

  <div class="tabs" id="tabs"></div>

  <div class="cards" id="cards"></div>

  <div class="panel">
    <h2>일별 8개 조건 전부 통과 종목 수 추이</h2>
    <svg class="trend" id="trend"></svg>
  </div>

  <div class="panel">
    <div class="controls">
      <input type="text" id="search" placeholder="종목코드 또는 종목명 검색...">
      <button class="filter-btn active" data-filter="all">전체</button>
      <button class="filter-btn" data-filter="pass">8개 통과만</button>
      <button class="filter-btn" data-filter="watch">관찰후보만</button>
      <button class="filter-btn" data-filter="breakout">돌파만</button>
      <button class="filter-btn" data-filter="na">확인불가만</button>
      <select id="sortSelect">
        <option value="setupScore">셋업점수순</option>
        <option value="metCount">충족조건수순</option>
        <option value="rsRank">RS백분위순</option>
        <option value="high52wPosition">52주고점대비순</option>
        <option value="marcap">시가총액순</option>
        <option value="code">종목코드순</option>
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
    ※ 8번 조건(상대강도, RS)은 IBD RS가 없는 시장 특성상 각 시장 지수 대비 3·6·12개월 초과수익률을 유니버스 내 백분위로 환산한 대체 지표입니다.<br>
    ※ "충족조건수"는 참고용이며, "전체통과" 배지만 8개 조건을 전부 동시 충족(AND)했다는 공식 판정입니다.<br>
    ※ "셋업점수", "타이밍신호(관찰후보/돌파/VCP/피벗임박)", "RS상승중", "52주고점대비"는 8개 조건 판정과 무관한 진입 타이밍 참고 지표입니다. VCP는 실제 미너비니 방법론(스윙 고점/저점 기반 다중 파동 탐지)이 아닌 고정 4주 구간 비교 근사치이며, "관찰후보"·"돌파" 포함 전부 매수 신호가 아닙니다.<br>
    ※ 이 페이지는 1차 필터 + 타이밍 참고 지표까지만 보여줍니다. 스테이지(와인스타인 4단계) 확정, 베이스 단계, 펀더멘털, 촉매는 별도로 직접 판단해야 합니다.
  </footer>
</div>

<script>
const DATA = __DATA_JSON__;
const marketKeys = Object.keys(DATA);
let currentMarket = marketKeys[0];
let currentFilter = "all";
let sortKey = "metCount";
let sortDir = -1;

function fmtNum(n, digits) {
  if (n === null || n === undefined) return "-";
  return Number(n).toLocaleString("ko-KR", { maximumFractionDigits: digits ?? 0, minimumFractionDigits: 0 });
}

function renderTabs() {
  const el = document.getElementById("tabs");
  if (marketKeys.length <= 1) { el.style.display = "none"; return; }
  el.innerHTML = marketKeys.map(k =>
    `<button class="tab-btn${k===currentMarket?" active":""}" data-market="${k}">${DATA[k].label}</button>`
  ).join("");
  el.querySelectorAll(".tab-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      currentMarket = btn.dataset.market;
      if (sortKey === "marcap" && !hasMarcap()) { sortKey = "metCount"; document.getElementById("sortSelect").value = "metCount"; }
      renderAll();
    });
  });
}

function renderCards() {
  const market = DATA[currentMarket];
  const rows = market.rows;
  const hist = market.history;
  const latest = hist[hist.length - 1] || {};
  const cards = [
    ["기준일", market.asOf || "-"],
    ["스크리닝종목수", latest.total ?? rows.length],
    ["정상판정", latest.ok ?? rows.filter(r => r.status === "OK").length],
    ["확인불가/제외", latest.excluded ?? rows.filter(r => r.status !== "OK").length],
    ["8개조건전부통과", latest.pass ?? rows.filter(r => r.passAll === true).length],
    ["평균 RS백분위", latest.avgRs != null ? fmtNum(latest.avgRs, 1) : "-"],
  ];
  document.getElementById("cards").innerHTML = cards.map(([label, value]) =>
    `<div class="card"><div class="label">${label}</div><div class="value">${value}</div></div>`
  ).join("");

  const dates = marketKeys.map(k => DATA[k].asOf).filter(Boolean);
  const uniqueDates = [...new Set(dates)];
  document.getElementById("subtitle").textContent = uniqueDates.length
    ? `기준일(KST): ${marketKeys.map(k => `${DATA[k].label.split(" ")[0]} ${DATA[k].asOf || "-"}`).join(" · ")} · 미너비니 추세 템플릿 8개 조건 1차 필터 + 타이밍 참고 지표 · 스테이지/베이스단계/펀더멘털/매매신호는 다루지 않음`
    : "미너비니 추세 템플릿 8개 조건 1차 필터 + 타이밍 참고 지표 · 스테이지/베이스단계/펀더멘털/매매신호는 다루지 않음";
}

function renderTrend() {
  const hist = DATA[currentMarket].history;
  const svg = document.getElementById("trend");
  if (hist.length < 2) {
    svg.innerHTML = `<text x="8" y="30" fill="var(--text-dim)" font-size="12">추세를 보려면 이틀 이상의 데이터가 쌓여야 합니다 (현재 ${hist.length}일치).</text>`;
    return;
  }
  const w = 1000, h = 60, pad = 4;
  const vals = hist.map(d => d.pass ?? 0);
  const max = Math.max(...vals, 1);
  const stepX = (w - pad * 2) / (hist.length - 1);
  const pts = vals.map((v, i) => {
    const x = pad + i * stepX;
    const y = h - pad - (v / max) * (h - pad * 2);
    return [x, y];
  });
  const path = pts.map((p, i) => (i === 0 ? "M" : "L") + p[0].toFixed(1) + "," + p[1].toFixed(1)).join(" ");
  const lastPt = pts[pts.length - 1];
  svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
  svg.innerHTML = `
    <path d="${path}" fill="none" stroke="var(--accent)" stroke-width="2"/>
    <circle cx="${lastPt[0]}" cy="${lastPt[1]}" r="3" fill="var(--accent)"/>
    <text x="${pad}" y="${h-2}" fill="var(--text-dim)" font-size="10">${hist[0].date}</text>
    <text x="${w-pad}" y="${h-2}" fill="var(--text-dim)" font-size="10" text-anchor="end">${hist[hist.length-1].date} (${vals[vals.length-1]}종목)</text>
  `;
}

function hasMarcap() {
  return DATA[currentMarket].rows.some(r => r.marcap !== null && r.marcap !== undefined);
}

function getCols() {
  const cols = [
    { key: "rank", label: "#", left: true },
    { key: "code", label: "코드", left: true },
    { key: "name", label: "종목명", left: true },
    { key: "close", label: "종가", fmt: v => fmtNum(v) },
    { key: "metCount", label: "충족", fmt: (v) => metBar(v) },
    { key: "rsRank", label: "RS백분위", fmt: v => v != null ? fmtNum(v, 1) : "-" },
    { key: "high52wPosition", label: "52주고점대비", fmt: v => v != null ? fmtPct(v) : "-" },
  ];
  if (hasMarcap()) {
    cols.push({ key: "marcap", label: "시가총액", fmt: v => v ? fmtNum(v / 1e8, 0) + "억" : "-" });
  }
  cols.push(
    { key: "setupScore", label: "셋업점수", fmt: v => setupScoreBadge(v) },
    { key: "signals", label: "타이밍신호", fmt: (v, r) => timingSignals(r) },
    { key: "passAll", label: "판정", fmt: (v, r) => statusBadge(r) },
  );
  return cols;
}

function fmtPct(v) {
  return (v >= 0 ? "+" : "") + (v * 100).toFixed(1) + "%";
}

function setupScoreBadge(v) {
  if (v === null || v === undefined) return "-";
  const pct = Math.max(0, Math.min(100, v * 10));
  const hue = 4 + (pct / 100) * 146; // 낮으면 빨강 계열, 높으면 초록 계열
  return `<span style="display:inline-block;min-width:34px;padding:2px 6px;border-radius:6px;font-weight:700;background:hsl(${hue},70%,92%);color:hsl(${hue},60%,32%);">${fmtNum(v, 1)}</span>`;
}

function timingSignals(r) {
  const badges = [];
  if (r.watchCandidate === true) badges.push(`<span class="badge watch" title="8/8통과+RS85이상+RS상승중+52주고점-10%이내+셋업8점이상+피벗임박. 매수신호 아님">관찰후보</span>`);
  if (r.breakoutSignal === true) badges.push(`<span class="badge breakout" title="피벗 상향돌파 + 거래량 1.5배 이상. 매수신호 아님">돌파</span>`);
  if (r.vcpForming === true) badges.push(`<span class="badge pass" title="VCP 수축 근사치 조건 충족">VCP</span>`);
  if (r.pivotNear === true) badges.push(`<span class="badge pass" title="피벗 대비 -5%~0% 구간">피벗임박</span>`);
  return badges.length ? badges.join(" ") : "-";
}

function metBar(v) {
  if (v === null || v === undefined) return "-";
  let bars = "";
  for (let i = 0; i < 8; i++) bars += `<span class="${i < v ? "on" : ""}"></span>`;
  return `<span class="metbar">${bars}</span> ${v}/8`;
}

function statusBadge(r) {
  if (r.status !== "OK") return `<span class="badge na">확인불가</span>`;
  return r.passAll ? `<span class="badge pass">전체통과</span>` : `<span class="badge fail">미통과</span>`;
}

function renderTable() {
  const q = document.getElementById("search").value.trim().toLowerCase();
  let rows = DATA[currentMarket].rows.slice();

  if (currentFilter === "pass") rows = rows.filter(r => r.passAll === true);
  else if (currentFilter === "watch") rows = rows.filter(r => r.watchCandidate === true);
  else if (currentFilter === "breakout") rows = rows.filter(r => r.breakoutSignal === true);
  else if (currentFilter === "na") rows = rows.filter(r => r.status !== "OK");

  if (q) rows = rows.filter(r =>
    (r.code || "").toLowerCase().includes(q) || (r.name || "").toLowerCase().includes(q)
  );

  rows.sort((a, b) => {
    const av = a[sortKey], bv = b[sortKey];
    if (av === null || av === undefined) return 1;
    if (bv === null || bv === undefined) return -1;
    if (typeof av === "string") return sortDir * av.localeCompare(bv);
    return sortDir * (av - bv);
  });

  const cols = getCols();
  const thead = document.getElementById("thead-row");
  thead.innerHTML = cols.map(c =>
    `<th class="${c.left ? "left" : ""}${c.key===sortKey?" sorted"+(sortDir===1?" asc":""):""}" data-key="${c.key}">${c.label}</th>`
  ).join("");
  thead.querySelectorAll("th").forEach(th => {
    th.addEventListener("click", () => {
      const key = th.dataset.key;
      if (key === "rank" || key === "signals") return;
      if (sortKey === key) sortDir *= -1; else { sortKey = key; sortDir = -1; }
      document.getElementById("sortSelect").value = ["setupScore","metCount","rsRank","high52wPosition","marcap","code"].includes(key) ? key : sortKey;
      renderTable();
    });
  });

  const marcapOpt = document.querySelector('#sortSelect option[value="marcap"]');
  if (marcapOpt) marcapOpt.hidden = !hasMarcap();

  const tbody = document.getElementById("tbody");
  document.getElementById("emptyMsg").style.display = rows.length ? "none" : "block";
  tbody.innerHTML = rows.map((r, i) => {
    const cells = cols.map(c => {
      const v = r[c.key];
      const content = c.fmt ? c.fmt(v, r) : (v ?? "-");
      return `<td class="${c.left ? "left" : ""}">${c.key === "rank" ? (i + 1) : content}</td>`;
    }).join("");
    return `<tr class="${r.passAll ? "pass-row" : ""}">${cells}</tr>`;
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

renderAll();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    build()
