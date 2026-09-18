"""
technical_signals/generate_dashboard.py — docs/technical/index.html 생성
============================================================================

- 입력: run_daily_screen.py가 만든 output/latest_{kr,us}.json
  (이 스크립트는 새로 시세를 조회하지 않는다.)
- 한국/미국은 서로 다른 스케줄로 실행되므로, 한 번의 실행에는 보통 한
  시장의 결과만 새로 생긴다. 그래서 시장별 최신 스냅샷을
  docs/technical/data/latest_{kr,us}.json 에 저장소 커밋으로 남겨두고,
  이번 실행에 없는 시장은 그 스냅샷을 그대로 이어 쓴다
  (루트 generate_dashboard.py와 동일한 패턴).
- 결과는 docs/technical/index.html 하나로 자기완결적이라 GitHub Pages에
  그대로 올리면 된다.

실행 방법 (technical_signals/ 안에서)
------------------------------------
    python run_daily_screen.py --market KR   # 또는 US, ALL
    python generate_dashboard.py
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parent
OUTPUT_DIR = BASE_DIR / "output"
DOCS_DIR = REPO_ROOT / "docs" / "technical"
DATA_DIR = DOCS_DIR / "data"

MARKETS = {
    "kr": "한국 (코스피/코스닥 시총상위)",
    "us": "미국 (S&P500)",
}


def load_fresh_rows(prefix: str) -> list | None:
    path = OUTPUT_DIR / f"latest_{prefix}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def load_snapshot(prefix: str) -> list | None:
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
    scores = [r["compositeScore"] for r in ok_rows if r.get("compositeScore") is not None]
    entry = {
        "date": run_date,
        "total": len(rows),
        "ok": len(ok_rows),
        "goldenCross": sum(1 for r in ok_rows if r.get("goldenCross") is True),
        "avgScore": round(sum(scores) / len(scores), 1) if scores else None,
    }

    history = [h for h in history if h["date"] != run_date]
    history.append(entry)
    history.sort(key=lambda h: h["date"])
    history = history[-180:]

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
                continue
            history = load_history(prefix)

        as_of = history[-1]["date"] if history else None
        payload[prefix] = {"label": label, "rows": rows, "history": history, "asOf": as_of}

    if not payload:
        print("생성할 데이터가 없습니다 (output/latest_*.json을 먼저 만들어야 함: run_daily_screen.py를 먼저 실행하세요)")
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
<title>기술적 신호 스크리너</title>
<style>
  :root {
    --bg: #f5f6f8; --panel: #ffffff; --border: #e3e5e9;
    --text: #1b1e24; --text-dim: #6b7280; --accent: #2563eb;
    --pass-bg: #e6f7ec; --pass-text: #157347; --pass-border: #b7e4c7;
    --na-bg: #fdf2f2; --na-text: #b42318;
    --watch-bg: #eef0ff; --watch-text: #4338ca;
    --breakout-bg: #fff2e0; --breakout-text: #b45309;
    --up: #d92b2b; --down: #1a56db;
    --row-hover: #f0f4ff; --shadow: 0 1px 3px rgba(0,0,0,0.06), 0 1px 2px rgba(0,0,0,0.04);
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #0f1115; --panel: #171a21; --border: #2a2e37;
      --text: #e7e9ee; --text-dim: #9aa2b1; --accent: #5b8def;
      --pass-bg: #113322; --pass-text: #6bd08a; --pass-border: #1e5c3a;
      --na-bg: #3a1717; --na-text: #f2a4a0;
      --watch-bg: #201f42; --watch-text: #a5b0fc;
      --breakout-bg: #3a2712; --breakout-text: #f6b96a;
      --up: #f0605f; --down: #6ea8fe;
      --row-hover: #1d2230; --shadow: 0 1px 3px rgba(0,0,0,0.4);
    }
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--text); font-family: -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Malgun Gothic",sans-serif; font-size: 13px; }
  .wrap { max-width: 1400px; margin: 0 auto; padding: 20px 16px 40px; }
  h1 { font-size: 18px; margin: 0 0 4px; }
  .sub { color: var(--text-dim); font-size: 12px; margin-bottom: 14px; }
  .nav { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 16px; }
  .nav a { padding: 7px 12px; border-radius: 999px; border: 1px solid var(--border); background: var(--panel); color: var(--text); text-decoration: none; font-size: 12px; font-weight: 600; }
  .nav a.here { background: var(--accent); color: #fff; border-color: var(--accent); }
  .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px,1fr)); gap: 10px; margin-bottom: 16px; }
  .card { background: var(--panel); border: 1px solid var(--border); border-radius: 10px; padding: 10px 12px; box-shadow: var(--shadow); }
  .card .lbl { color: var(--text-dim); font-size: 11px; }
  .card .val { font-size: 20px; font-weight: 700; margin-top: 2px; }
  .controls { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; margin-bottom: 10px; }
  .controls input, .controls select { padding: 7px 10px; border-radius: 8px; border: 1px solid var(--border); background: var(--panel); color: var(--text); font-size: 12px; }
  .filter-btn { padding: 7px 14px; border-radius: 999px; border: 1px solid var(--border); background: var(--bg); color: var(--text); cursor: pointer; font-size: 12px; font-weight: 600; }
  .filter-btn.active { background: var(--accent); color: #fff; border-color: var(--accent); }
  .panel { background: var(--panel); border: 1px solid var(--border); border-radius: 12px; padding: 14px; box-shadow: var(--shadow); }
  .table-scroll { overflow-x: auto; }
  table { border-collapse: collapse; width: 100%; white-space: nowrap; }
  th, td { padding: 7px 10px; text-align: right; border-bottom: 1px solid var(--border); font-size: 12px; }
  th.left, td.left { text-align: left; }
  th { color: var(--text-dim); font-weight: 600; cursor: pointer; user-select: none; position: sticky; top: 0; background: var(--panel); }
  th.sorted { color: var(--accent); }
  tbody tr:hover { background: var(--row-hover); }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; font-weight: 700; }
  .badge.on { background: var(--pass-bg); color: var(--pass-text); }
  .badge.off { background: var(--bg); color: var(--text-dim); }
  .badge.warn { background: var(--breakout-bg); color: var(--breakout-text); }
  .badge.na { background: var(--na-bg); color: var(--na-text); }
  .change { font-weight: 600; }
  .change.up { color: var(--up); }
  .change.down { color: var(--down); }
  .empty-msg { text-align: center; color: var(--text-dim); padding: 30px; }
  footer { color: var(--text-dim); font-size: 12px; margin-top: 20px; line-height: 1.7; }
  .tab-btn { padding: 7px 14px; border-radius: 999px; border: 1px solid var(--border); background: var(--panel); color: var(--text); cursor: pointer; font-size: 12px; font-weight: 600; }
  .tab-btn.active { background: var(--text); color: var(--bg); border-color: var(--text); }
  .tabs { display: flex; gap: 6px; margin-bottom: 12px; }
</style>
</head>
<body>
<div class="wrap">
  <h1>기술적 신호 스크리너</h1>
  <div class="sub">이동평균 교차·MACD·RSI·스토캐스틱·볼린저밴드·OBV·ADX·이격도 — 추세템플릿/펀더멘털과 무관한 순수 기술적 지표. 전부 참고용이며 매수 신호가 아닙니다.</div>

  <div class="nav">
    <a href="../">SEPA 추세템플릿</a>
    <a href="../range_vrebound/">RANGE-MR · V-REBOUND</a>
    <a href="../screener/">멀티팩터</a>
    <a class="here" href="./">기술적 신호</a>
  </div>

  <div class="tabs" id="tabs"></div>

  <div class="cards" id="cards"></div>

  <div class="panel">
    <div class="controls">
      <input type="text" id="search" placeholder="종목코드 또는 종목명 검색...">
      <button class="filter-btn active" data-filter="all">전체</button>
      <button class="filter-btn" data-filter="golden">골든크로스</button>
      <button class="filter-btn" data-filter="macd">MACD매수</button>
      <button class="filter-btn" data-filter="rsi">RSI회복</button>
      <button class="filter-btn" data-filter="stoch">스토캐스틱매수</button>
      <button class="filter-btn" data-filter="bb">볼린저신호</button>
      <button class="filter-btn" data-filter="obv">OBV상승</button>
      <button class="filter-btn" data-filter="trending">추세강함(ADX)</button>
      <button class="filter-btn" data-filter="multi">복합신호(2개+)</button>
      <button class="filter-btn" data-filter="na">확인불가</button>
      <select id="sortSelect">
        <option value="compositeScore">복합점수순</option>
        <option value="rsiValue">RSI순</option>
        <option value="adxValue">ADX순</option>
        <option value="disparity20">이격도순</option>
        <option value="changePct">등락률순</option>
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
    <b>전부 참고용 지표이며 매수·매도 신호가 아닙니다.</b> 펀더멘털·추세템플릿·시장국면은 고려하지 않은 순수 가격/거래량 기술적 지표입니다.<br>
    ※ <b>골든/데드크로스</b>: SMA50이 SMA200을 최근 5거래일 내 상향/하향 돌파. <b>MACD(12,26,9)</b>: MACD선이 시그널선을 최근 3거래일 내 상향/하향 돌파.<br>
    ※ <b>RSI(14)</b>: 30선을 최근 3거래일 내 상향 이탈(회복) / 70 이상(과매수). <b>스토캐스틱(14,3,3)</b>: %K가 20 이하 구간에서 %D를 최근 3거래일 내 상향 돌파.<br>
    ※ <b>볼린저밴드(20,2σ)</b>: 하단이탈 후 재진입(역추세) 또는 상단돌파+거래량 1.5배 이상(추세추종), 밴드폭이 120거래일 신저치면 "수축(스퀴즈, 변동성 축소 셋업)".<br>
    ※ <b>OBV</b>: 누적거래량이 20일 평균보다 위(거래량 추세 확인용, 단독 신호 아님). <b>ADX(14)</b>: 25 이상이면 "추세 있음" — 그 자체로 매수 신호가 아니라 다른 신호의 신뢰도를 보정하는 필터입니다. <b>이격도</b> = (종가/20일 이평 − 1)×100, 수치 참고용.<br>
    ※ <b>복합점수</b>(0~100, 랭킹용)는 위 신호 중 계산 가능한 것만 가중평균한 것입니다(골든크로스 0.20·MACD 0.20·RSI 0.15·스토캐스틱 0.15·볼린저 0.15·OBV 0.10·ADX 0.05). 하드 게이트가 아니며, 임계값은 <code>technical_signals/config.py</code>에서 조정됩니다.
  </footer>
</div>

<script>
const DATA = __DATA_JSON__;
const marketKeys = Object.keys(DATA);
let currentMarket = marketKeys[0];
let currentFilter = "all";
let sortKey = "compositeScore";
let sortDir = -1;

function fmtNum(n, digits) {
  if (n === null || n === undefined || n === "") return "-";
  return Number(n).toLocaleString("ko-KR", { maximumFractionDigits: digits ?? 0, minimumFractionDigits: 0 });
}
function toNum(v) { return (v === null || v === undefined || v === "") ? null : Number(v); }
function fmtPct(v) {
  if (v === null || v === undefined) return "-";
  return (v >= 0 ? "+" : "") + (v * 100).toFixed(1) + "%";
}
function changeBadge(v) {
  if (v === null || v === undefined) return "-";
  const cls = v > 0 ? "up" : (v < 0 ? "down" : "");
  return `<span class="change ${cls}">${fmtPct(v)}</span>`;
}
function boolBadge(v, onLabel, offLabel) {
  if (v === null || v === undefined) return `<span class="badge na">-</span>`;
  return v ? `<span class="badge on">${onLabel}</span>` : `<span class="badge off">${offLabel || "-"}</span>`;
}

function signalCount(r) {
  const flags = [r.goldenCross, r.macdBullCross, r.rsiOversoldExit, r.stochBullCross,
                 (r.bbLowerRevert || r.bbUpperBreakout)];
  return flags.filter(v => v === true).length;
}

function renderTabs() {
  const el = document.getElementById("tabs");
  if (marketKeys.length <= 1) { el.style.display = "none"; return; }
  el.innerHTML = marketKeys.map(k =>
    `<button class="tab-btn${k===currentMarket?" active":""}" data-market="${k}">${DATA[k].label}</button>`
  ).join("");
  el.querySelectorAll(".tab-btn").forEach(btn => {
    btn.addEventListener("click", () => { currentMarket = btn.dataset.market; renderAll(); });
  });
}

function renderCards() {
  const market = DATA[currentMarket];
  const rows = market.rows;
  const ok = rows.filter(r => r.status === "OK");
  const golden = ok.filter(r => r.goldenCross === true).length;
  const multi = ok.filter(r => signalCount(r) >= 2).length;
  const trending = ok.filter(r => r.adxTrending === true).length;
  const cards = [
    ["기준일", market.asOf || "-"],
    ["스크리닝종목수", rows.length],
    ["정상판정", ok.length],
    ["골든크로스", golden],
    ["복합신호(2개+)", multi],
    ["추세강함(ADX≥25)", trending],
  ];
  document.getElementById("cards").innerHTML = cards.map(([l, v]) =>
    `<div class="card"><div class="lbl">${l}</div><div class="val">${v}</div></div>`
  ).join("");
}

function getCols() {
  return [
    { key: "rank", label: "#", left: true },
    { key: "code", label: "코드", left: true },
    { key: "name", label: "종목명", left: true },
    { key: "close", label: "종가", fmt: v => fmtNum(v, 2) },
    { key: "changePct", label: "등락률", fmt: v => changeBadge(v) },
    { key: "goldenCross", label: "골든크로스", fmt: (v, r) => r.deadCross ? `<span class="badge warn">데드</span>` : boolBadge(v, "골든") },
    { key: "macdBullCross", label: "MACD", fmt: (v, r) => r.macdBearCross ? `<span class="badge warn">약세돌파</span>` : boolBadge(v, "매수돌파") },
    { key: "rsiValue", label: "RSI(14)", fmt: (v, r) => (v == null ? "-" : `${v.toFixed(1)} ${r.rsiOversoldExit ? '<span class="badge on">회복</span>' : (r.rsiOverbought ? '<span class="badge warn">과매수</span>' : "")}`) },
    { key: "stochBullCross", label: "스토캐스틱", fmt: (v, r) => (r.stochK == null ? "-" : `${r.stochK.toFixed(0)}/${r.stochD.toFixed(0)} ${boolBadge(v, "매수교차", "")}`) },
    { key: "bbSignal", label: "볼린저", fmt: (v, r) => {
        if (r.bbUpper == null) return "-";
        if (r.bbUpperBreakout) return `<span class="badge on">상단돌파</span>`;
        if (r.bbLowerRevert) return `<span class="badge on">하단복귀</span>`;
        if (r.bbSqueeze) return `<span class="badge warn">수축</span>`;
        return "-";
      } },
    { key: "obvRising", label: "OBV", fmt: v => boolBadge(v, "상승", "하락") },
    { key: "adxValue", label: "ADX(14)", fmt: (v, r) => v == null ? "-" : `${v.toFixed(1)} ${r.adxTrending ? '<span class="badge on">추세</span>' : ""}` },
    { key: "disparity20", label: "이격도(20)", fmt: v => v == null ? "-" : (v > 0 ? "+" : "") + v.toFixed(1) + "%" },
    { key: "compositeScore", label: "복합점수", fmt: v => v == null ? "-" : `<span class="badge ${v >= 60 ? "on" : (v >= 30 ? "warn" : "off")}">${v.toFixed(0)}</span>` },
    { key: "status", label: "상태", fmt: (v, r) => v === "OK" ? "" : `<span class="badge na" title="${r.reason || ""}">확인불가</span>` },
  ];
}

function renderTable() {
  const q = document.getElementById("search").value.trim().toLowerCase();
  let rows = DATA[currentMarket].rows.slice();

  const F = {
    golden: r => r.goldenCross === true,
    macd: r => r.macdBullCross === true,
    rsi: r => r.rsiOversoldExit === true,
    stoch: r => r.stochBullCross === true,
    bb: r => r.bbLowerRevert === true || r.bbUpperBreakout === true,
    obv: r => r.obvRising === true,
    trending: r => r.adxTrending === true,
    multi: r => signalCount(r) >= 2,
    na: r => r.status !== "OK",
  };
  if (F[currentFilter]) rows = rows.filter(F[currentFilter]);

  if (q) rows = rows.filter(r =>
    (r.code || "").toLowerCase().includes(q) || (r.name || "").toLowerCase().includes(q)
  );

  rows.sort((a, b) => {
    let av = a[sortKey], bv = b[sortKey];
    if (av === null || av === undefined) return 1;
    if (bv === null || bv === undefined) return -1;
    if (typeof av === "string" && isNaN(Number(av))) return sortDir * av.localeCompare(bv);
    return sortDir * (Number(av) - Number(bv));
  });

  const cols = getCols();
  const thead = document.getElementById("thead-row");
  thead.innerHTML = cols.map(c =>
    `<th class="${c.left ? "left" : ""}${c.key===sortKey?" sorted":""}" data-key="${c.key}">${c.label}</th>`
  ).join("");
  thead.querySelectorAll("th").forEach(th => {
    th.addEventListener("click", () => {
      const key = th.dataset.key;
      if (key === "rank" || key === "status" || key === "bbSignal") return;
      if (sortKey === key) sortDir *= -1; else { sortKey = key; sortDir = -1; }
      const sel = document.getElementById("sortSelect");
      if ([...sel.options].some(o => o.value === key)) sel.value = key;
      renderTable();
    });
  });

  const tbody = document.getElementById("tbody");
  document.getElementById("emptyMsg").style.display = rows.length ? "none" : "block";
  tbody.innerHTML = rows.map((r, i) => {
    const cells = cols.map(c => {
      const v = r[c.key];
      const content = c.fmt ? c.fmt(v, r) : (v ?? "-");
      return `<td class="${c.left ? "left" : ""}">${c.key === "rank" ? (i + 1) : content}</td>`;
    }).join("");
    return `<tr>${cells}</tr>`;
  }).join("");
}

function renderAll() {
  renderTabs();
  renderCards();
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
