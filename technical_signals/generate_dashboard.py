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
    "kr": "한국 (코스피/코스닥 전체)",
    "us": "미국 (S&P500)",
}


def load_fresh(prefix: str) -> dict | None:
    """run_daily_screen.py가 만든 {"rows": [...], "regime": {...}}."""
    path = OUTPUT_DIR / f"latest_{prefix}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def load_snapshot(prefix: str) -> dict | None:
    path = DATA_DIR / f"latest_{prefix}.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        # 2026-09-18 스키마 변경 전({"rows":[...]} 감싸기 전) 스냅샷과의 호환 —
        # 아직 새 스키마로 갱신 안 된 시장(다른 스케줄로 도는 시장)이 있을 수 있다.
        return {"rows": data, "regime": None}
    return data


def save_snapshot(prefix: str, data: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / f"latest_{prefix}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def upsert_history(prefix: str, run_date: str, rows: list) -> list:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    hist_path = DATA_DIR / f"history_{prefix}.json"
    history = json.loads(hist_path.read_text(encoding="utf-8")) if hist_path.exists() else []

    ok_rows = [r for r in rows if r.get("status") == "OK"]
    trend_scores = [r["trendScore"] for r in ok_rows if r.get("trendScore") is not None]
    rebound_scores = [r["reboundScore"] for r in ok_rows if r.get("reboundScore") is not None]
    entry = {
        "date": run_date,
        "total": len(rows),
        "ok": len(ok_rows),
        "goldenCross": sum(1 for r in ok_rows if r.get("goldenCross") is True),
        "trendReview": sum(1 for r in ok_rows if r.get("trendVerdict") in ("매수검토", "진입준비")),
        "reboundReview": sum(1 for r in ok_rows if r.get("reboundVerdict") in ("매수검토", "진입준비")),
        "avgTrendScore": round(sum(trend_scores) / len(trend_scores), 1) if trend_scores else None,
        "avgReboundScore": round(sum(rebound_scores) / len(rebound_scores), 1) if rebound_scores else None,
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
        fresh = load_fresh(prefix)
        if fresh is not None:
            save_snapshot(prefix, fresh)
            history = upsert_history(prefix, run_date, fresh["rows"])
            rows, regime = fresh["rows"], fresh.get("regime")
        else:
            snap = load_snapshot(prefix)
            if snap is None:
                continue
            rows, regime = snap["rows"], snap.get("regime")
            history = load_history(prefix)

        as_of = history[-1]["date"] if history else None
        payload[prefix] = {"label": label, "rows": rows, "history": history, "asOf": as_of, "regime": regime}

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
  .tracks { display: flex; gap: 6px; margin-bottom: 12px; }
  .track-btn { padding: 8px 16px; border-radius: 10px; border: 1px solid var(--border); background: var(--panel); color: var(--text); cursor: pointer; font-size: 13px; font-weight: 700; }
  .track-btn.active { background: var(--accent); color: #fff; border-color: var(--accent); }
  .regime-bar { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; background: var(--panel); border: 1px solid var(--border); border-radius: 10px; padding: 8px 14px; margin-bottom: 12px; font-size: 12px; }
  .regime-bar .lbl { color: var(--text-dim); }
  .regime-chip { display: inline-block; padding: 2px 10px; border-radius: 999px; font-weight: 700; font-size: 11px; }
  .regime-GREEN { background: var(--pass-bg); color: var(--pass-text); }
  .regime-YELLOW { background: var(--breakout-bg); color: var(--breakout-text); }
  .regime-RED { background: var(--na-bg); color: var(--na-text); }
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

  <div class="regime-bar" id="regimeBar"></div>

  <div class="tracks" id="tracks"></div>

  <div class="cards" id="cards"></div>

  <div class="panel">
    <div class="controls" id="filterBar">
      <input type="text" id="search" placeholder="종목코드 또는 종목명 검색...">
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
    <b>전부 참고용 지표이며 매수·매도 신호가 아닙니다.</b> 펀더멘털은 고려하지 않은 순수 가격/거래량 기술적 지표입니다.<br>
    ※ <b>두 트랙으로 분리</b>: 고점 <b>돌파</b>(추세추종)와 하단 <b>반등</b>(박스권 평균회귀)은 방향이 반대일 수 있어 점수·판정을 따로 냅니다. 같은 MACD·RSI·스토캐스틱이라도 트랙에 따라 쓰이는 방식이 다릅니다(추세추종엔 "RSI 50~70 건강한 유지", 박스권반등엔 "RSI 30선 회복").<br>
    ※ <b>그룹별 점수</b>(0~100, 랭킹용, 트랙별): 추세30·트리거25·모멘텀20·변동성15·거래량10점. 그룹 안에 지표가 여러 개 있어도(예: 모멘텀의 MACD+RSI) 그 그룹 배점을 넘지 못합니다 — 같은 상승을 여러 번 세지 않기 위함입니다.<br>
    ※ <b>돌파 트리거</b>: 최근 60거래일 고가(피벗, 당일 제외)를 종가가 넘고, 거래량 1.5배 이상, 종가위치(CLV) 0.6 이상, 거래대금 최소치 이상을 최근 5거래일 내 동시에 만족. <b>하단복귀 트리거</b>: 볼린저 하단 이탈 후 재진입.<br>
    ※ <b>손절/리스크</b>: 스윙저점-0.5×ATR(구조적)과 종가-1.75×ATR(변동성 기준) 중 더 보수적인(리스크가 더 크게 잡히는) 쪽을 손절가로 씁니다. 리스크%가 7% 초과면 위험 표시.<br>
    ※ <b>추격 경고</b>: RSI 75 이상이거나 피벗 대비 +5% 이상 이격되면 뜹니다 — 점수가 높아도 진입 위치가 나쁠 수 있다는 뜻입니다.<br>
    ※ <b>시장 국면</b>: 개별 종목과 별개로 지수(종가&gt;SMA200, SMA50&gt;SMA200)와 breadth(유니버스 중 SMA50 위 비율 ≥50%) 3조건으로 GREEN/YELLOW/RED를 판정합니다. RED면 신규 진입 판정 자체를 보류로 내립니다.<br>
    ※ <b>매수검토/진입준비/진입보류</b>: 점수만으로 매수 신호를 만들지 않습니다. 매수검토=정배열(또는 눌린위치)+트리거+점수70↑+리스크7%이내 전부 충족. 진입준비=매수검토 + 거래량1.3배↑ + 모멘텀확인 + 시장국면 GREEN/YELLOW + 점수80↑. 진입보류=위 조건을 충족했더라도 추격경고·리스크7%초과·시장국면RED 중 하나라도 해당.<br>
    ※ 백테스트(과거 신호의 5·20·60거래일 성과 추적)는 아직 없습니다 — 별도로 준비 중입니다. 모든 임계값은 <code>technical_signals/config.py</code>에서 조정됩니다.
  </footer>
</div>

<script>
const DATA = __DATA_JSON__;
const marketKeys = Object.keys(DATA);
let currentMarket = marketKeys[0];
let currentTrack = "trend";   // "trend"(추세돌파) | "rebound"(박스권반등)
let currentFilter = "all";
let sortKey = "trendScore";
let sortDir = -1;

const TRACKS = {
  trend: { label: "추세돌파", scoreKey: "trendScore", verdictKey: "trendVerdict", reasonsKey: "trendVerdictReasons" },
  rebound: { label: "박스권반등", scoreKey: "reboundScore", verdictKey: "reboundVerdict", reasonsKey: "reboundVerdictReasons" },
};

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
function verdictBadge(v) {
  if (!v) return "-";
  const cls = v === "진입준비" ? "on" : (v === "매수검토" ? "warn" : "na");
  return `<span class="badge ${cls}">${v}</span>`;
}
function scoreBadge(v) {
  if (v == null) return "-";
  return `<span class="badge ${v >= 70 ? "on" : (v >= 40 ? "warn" : "off")}">${v.toFixed(0)}</span>`;
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

function renderTracks() {
  const el = document.getElementById("tracks");
  el.innerHTML = Object.entries(TRACKS).map(([key, t]) =>
    `<button class="track-btn${key===currentTrack?" active":""}" data-track="${key}">${t.label}</button>`
  ).join("");
  el.querySelectorAll(".track-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      currentTrack = btn.dataset.track;
      sortKey = TRACKS[currentTrack].scoreKey;
      currentFilter = "all";
      renderAll();
    });
  });
}

function renderRegimeBar() {
  const market = DATA[currentMarket];
  const reg = market.regime;
  const el = document.getElementById("regimeBar");
  if (!reg || reg.regime == null) {
    el.innerHTML = `<span class="lbl">시장 국면(참고용)</span><span class="regime-chip regime-YELLOW">판정불가</span>`;
    return;
  }
  const breadthPct = reg.breadth != null ? (reg.breadth * 100).toFixed(0) + "%" : "-";
  el.innerHTML = `
    <span class="lbl">시장 국면(참고용, 개별종목과 별개)</span>
    <span class="regime-chip regime-${reg.regime}">${reg.regime}</span>
    <span class="lbl">지수&gt;SMA200 ${reg.aboveSma200 ? "✓" : "✗"}</span>
    <span class="lbl">SMA50&gt;SMA200 ${reg.sma50AboveSma200 ? "✓" : "✗"}</span>
    <span class="lbl">breadth(SMA50 위 비율) ${breadthPct}</span>
    <span class="lbl" style="margin-left:auto">GREEN=신규진입 허용 · YELLOW=확인 강화 · RED=신규진입 보류(관찰만)</span>
  `;
}

function renderCards() {
  const market = DATA[currentMarket];
  const rows = market.rows;
  const ok = rows.filter(r => r.status === "OK");
  const t = TRACKS[currentTrack];
  const review = ok.filter(r => r[t.verdictKey] === "매수검토").length;
  const ready = ok.filter(r => r[t.verdictKey] === "진입준비").length;
  const hold = ok.filter(r => r[t.verdictKey] === "진입보류").length;
  const cards = [
    ["기준일", market.asOf || "-"],
    ["스크리닝종목수", rows.length],
    ["정상판정", ok.length],
    [`${t.label} 매수검토`, review],
    [`${t.label} 진입준비`, ready],
    [`${t.label} 진입보류`, hold],
  ];
  document.getElementById("cards").innerHTML = cards.map(([l, v]) =>
    `<div class="card"><div class="lbl">${l}</div><div class="val">${v}</div></div>`
  ).join("");
}

function getCols() {
  const t = TRACKS[currentTrack];
  const common = [
    { key: "rank", label: "#", left: true },
    { key: "code", label: "코드", left: true },
    { key: "name", label: "종목명", left: true },
    { key: "close", label: "종가", fmt: v => fmtNum(v, 2) },
    { key: "changePct", label: "등락률", fmt: v => changeBadge(v) },
    { key: t.verdictKey, label: "판정", fmt: (v, r) => `<span title="${(r[t.reasonsKey] || []).join(', ')}">${verdictBadge(v)}</span>` },
    { key: t.scoreKey, label: "점수", fmt: v => scoreBadge(v) },
  ];
  const trendOnly = [
    { key: "trendAligned", label: "정배열", fmt: v => boolBadge(v, "정배열", "-") },
    { key: "goldenCross", label: "골든크로스", fmt: (v, r) => r.deadCross ? `<span class="badge warn">데드</span>` : boolBadge(v, "골든") },
    { key: "breakoutTrigger", label: "돌파트리거", fmt: v => boolBadge(v, "돌파", "-") },
    { key: "pivotDistancePct", label: "피벗대비", fmt: (v, r) => v == null ? "-" : `${v > 0 ? "+" : ""}${v.toFixed(1)}% ${r.chaseWarning ? '<span class="badge warn">추격주의</span>' : ""}` },
    { key: "macdBullCross", label: "MACD", fmt: v => boolBadge(v, "매수돌파") },
    { key: "rsiValue", label: "RSI(14)", fmt: (v, r) => v == null ? "-" : `${v.toFixed(1)} ${r.rsiHealthyTrend ? '<span class="badge on">건강</span>' : (r.rsiOverbought ? '<span class="badge warn">과매수</span>' : "")}` },
    { key: "bbSqueeze", label: "볼린저수축", fmt: v => boolBadge(v, "수축", "-") },
    { key: "obvRising", label: "OBV", fmt: v => boolBadge(v, "상승", "하락") },
    { key: "adxValue", label: "ADX(14)", fmt: (v, r) => v == null ? "-" : `${v.toFixed(1)} ${r.adxTrending ? '<span class="badge on">추세</span>' : ""}` },
  ];
  const reboundOnly = [
    { key: "disparityOversold", label: "눌린위치", fmt: (v, r) => r.disparity20 == null ? "-" : `${r.disparity20.toFixed(1)}% ${v ? '<span class="badge on">과매도</span>' : ""}` },
    { key: "bbLowerRevert", label: "하단복귀", fmt: v => boolBadge(v, "복귀", "-") },
    { key: "rsiOversoldExit", label: "RSI회복", fmt: (v, r) => v == null && r.rsiValue == null ? "-" : `${r.rsiValue != null ? r.rsiValue.toFixed(1) : "-"} ${boolBadge(v, "회복", "")}` },
    { key: "stochBullCross", label: "스토캐스틱", fmt: (v, r) => r.stochK == null ? "-" : `${r.stochK.toFixed(0)}/${r.stochD.toFixed(0)} ${boolBadge(v, "매수교차", "")}` },
    { key: "bbSqueeze", label: "볼린저수축", fmt: v => boolBadge(v, "수축", "-") },
    { key: "obvRising", label: "OBV", fmt: v => boolBadge(v, "상승", "하락") },
  ];
  const riskCols = [
    { key: "riskPct", label: "리스크%", fmt: (v, r) => v == null ? "-" : `${v.toFixed(1)}% ${r.riskTooHigh ? '<span class="badge warn">7%초과</span>' : ""}` },
    { key: "stopPrice", label: "손절가", fmt: v => v == null ? "-" : fmtNum(v, 2) },
    { key: "volumeRatio50", label: "거래량/50일", fmt: v => v == null ? "-" : v.toFixed(2) + "x" },
    { key: "status", label: "상태", fmt: (v, r) => v === "OK" ? "" : `<span class="badge na" title="${r.reason || ""}">확인불가</span>` },
  ];
  return common.concat(currentTrack === "trend" ? trendOnly : reboundOnly, riskCols);
}

function getFilters() {
  if (currentTrack === "trend") {
    return [
      ["all", "전체"], ["review", "매수검토+"], ["ready", "진입준비"], ["hold", "진입보류"],
      ["golden", "골든크로스"], ["breakout", "돌파트리거"], ["macd", "MACD매수"],
      ["squeeze", "볼린저수축"], ["obv", "OBV상승"], ["chase", "추격경고"], ["na", "확인불가"],
    ];
  }
  return [
    ["all", "전체"], ["review", "매수검토+"], ["ready", "진입준비"], ["hold", "진입보류"],
    ["position", "눌린위치"], ["revert", "하단복귀"], ["rsi", "RSI회복"], ["stoch", "스토캐스틱매수"],
    ["squeeze", "볼린저수축"], ["obv", "OBV상승"], ["na", "확인불가"],
  ];
}

function filterPredicate(key, t) {
  const F_TREND = {
    review: r => r[t.verdictKey] === "매수검토" || r[t.verdictKey] === "진입준비",
    ready: r => r[t.verdictKey] === "진입준비",
    hold: r => r[t.verdictKey] === "진입보류",
    golden: r => r.goldenCross === true,
    breakout: r => r.breakoutTrigger === true,
    macd: r => r.macdBullCross === true,
    squeeze: r => r.bbSqueeze === true,
    obv: r => r.obvRising === true,
    chase: r => r.chaseWarning === true,
    na: r => r.status !== "OK",
  };
  const F_REBOUND = {
    review: r => r[t.verdictKey] === "매수검토" || r[t.verdictKey] === "진입준비",
    ready: r => r[t.verdictKey] === "진입준비",
    hold: r => r[t.verdictKey] === "진입보류",
    position: r => r.disparityOversold === true,
    revert: r => r.bbLowerRevert === true,
    rsi: r => r.rsiOversoldExit === true,
    stoch: r => r.stochBullCross === true,
    squeeze: r => r.bbSqueeze === true,
    obv: r => r.obvRising === true,
    na: r => r.status !== "OK",
  };
  return (currentTrack === "trend" ? F_TREND : F_REBOUND)[key];
}

function renderFilterBar() {
  const el = document.getElementById("filterBar");
  const search = document.getElementById("search");
  el.querySelectorAll(".filter-btn").forEach(b => b.remove());
  getFilters().forEach(([key, label]) => {
    const btn = document.createElement("button");
    btn.className = "filter-btn" + (key === currentFilter ? " active" : "");
    btn.dataset.filter = key;
    btn.textContent = label;
    btn.addEventListener("click", () => {
      currentFilter = key;
      el.querySelectorAll(".filter-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      renderTable();
    });
    el.insertBefore(btn, search);
  });
}

function renderTable() {
  const q = document.getElementById("search").value.trim().toLowerCase();
  let rows = DATA[currentMarket].rows.slice();
  const t = TRACKS[currentTrack];

  const pred = filterPredicate(currentFilter, t);
  if (currentFilter !== "all" && pred) rows = rows.filter(pred);

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
      if (key === "rank" || key === "status") return;
      if (sortKey === key) sortDir *= -1; else { sortKey = key; sortDir = -1; }
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
  renderTracks();
  renderRegimeBar();
  renderCards();
  renderFilterBar();
  renderTable();
}

document.getElementById("search").addEventListener("input", renderTable);

renderAll();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    build()
