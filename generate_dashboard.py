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
- docs/data/charts_{kr,us}.json 에는 8/8 통과 종목의 미니차트용 시계열
  (종가/SMA/거래량/RSI, screening.py가 만든 output/chart_data_*.json을
  그대로 옮긴 것)을 최신 스냅샷으로 유지한다.
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
    "종가": "close", "등락률": "changePct", "SMA50": "sma50", "SMA150": "sma150", "SMA200": "sma200",
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
    "돌파_참고용_매수신호아님": "breakoutSignal",
    "시장게이팅_참고용": "marketGate",
    "진입체크리스트_충족수_참고용": "entryChecklistCount",
    "진입판정_참고용_매수신호아님": "entryVerdict",
    # --- SEPA Screener v2 ---
    "20일평균거래대금": "avgTradingValue20", "유니버스포함": "inUniverse",
    "TREND_OK_v2": "trendOk", "조건8_RS_v2": "c8v2",
    "RS_Score": "rsScore", "RS_Score_20일전": "rsScorePrev", "RS_20D_Change": "rsChange20d",
    "RS_Line_신고가": "rsLineHigh",
    "52주고점근접비율": "highProximity", "고점근접등급": "highTier",
    "base길이": "baseLength", "range10_pct": "range10",
    "ATR20": "atr20", "ATR60": "atr60", "ATR수축비율": "atrContraction",
    "거래량Dryup비율": "volDryup",
    "피벗가격_v2": "pivotV2", "피벗거리_pct": "pivotDist", "피벗산출방식": "pivotSrc",
    "수축횟수": "contractionCount", "수축폭목록": "contractionWidths",
    "SETUP_READY": "setupReady", "SetupQuality점수": "setupQuality",
    "피벗구간": "zone", "확인된돌파": "confirmedBo",
    "돌파거래량비율_50": "boVolRatio", "돌파CLV": "boClv",
    "최근돌파_며칠전": "boDaysAgo", "눌림목": "pullback",
    "EntryState": "entryState", "EntryState사유": "entryReason",
    "ExitState": "exitState", "ExitWarnings": "exitWarnings", "ExitState사유": "exitReason",
    "구조적손절가": "structStop", "스윙저점": "swingLow",
    "초기리스크_pct": "initRisk", "진입리스크플래그": "riskFlag",
    "시장국면_v2": "regime", "breadth50": "breadth", "권장진입비중": "sizeFactor",
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


def load_fresh_charts(prefix: str) -> dict | None:
    """이번 실행에서 screening.py가 만든 8/8 통과 종목 미니차트 데이터(있으면)."""
    path = OUTPUT_DIR / f"chart_data_{prefix}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def load_chart_snapshot(prefix: str) -> dict:
    path = DATA_DIR / f"charts_{prefix}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def save_chart_snapshot(prefix: str, charts: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / f"charts_{prefix}.json").write_text(json.dumps(charts, ensure_ascii=False), encoding="utf-8")


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

        fresh_charts = load_fresh_charts(prefix)
        if fresh_charts is not None:
            save_chart_snapshot(prefix, fresh_charts)
            charts = fresh_charts
        else:
            charts = load_chart_snapshot(prefix)

        payload[prefix] = {"label": label, "rows": rows, "history": history, "asOf": as_of, "charts": charts}

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
    --up: #d92b2b; --down: #1a56db;
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
  .nav { display: flex; gap: 10px; margin: 10px 0 4px; flex-wrap: wrap; }
  .nav a { font-size: 12px; color: var(--text-dim); text-decoration: none; border: 1px solid var(--border);
           border-radius: 999px; padding: 4px 12px; background: var(--panel); }
  .nav a:hover { color: var(--accent); border-color: var(--accent); }
  .nav a.here { color: #fff; background: var(--accent); border-color: var(--accent); }
  .tabs { display: flex; gap: 8px; margin: 16px 0; }
  .tab-btn {
    padding: 8px 16px; border-radius: 8px; border: 1px solid var(--border);
    background: var(--panel); color: var(--text); cursor: pointer; font-size: 13px; font-weight: 600;
  }
  .tab-btn.active { background: var(--accent); color: #fff; border-color: var(--accent); }
  .gate-row { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 16px; }
  .gate-chip { display: inline-flex; align-items: center; gap: 6px; padding: 6px 12px; border-radius: 8px; font-size: 12px; font-weight: 700; border: 1px solid transparent; }
  .tier-good { background: var(--pass-bg); color: var(--pass-text); border-color: var(--pass-border); }
  .tier-mid { background: var(--breakout-bg); color: var(--breakout-text); border-color: var(--breakout-border); }
  .tier-bad { background: var(--fail-bg); color: var(--text-dim); border-color: var(--border); }
  /* --- v2: 시장국면 배너 + EntryState/ExitState 배지 --- */
  .regime-bar { display: flex; flex-wrap: wrap; gap: 8px 16px; align-items: center; padding: 10px 14px;
                border-radius: 10px; margin-bottom: 16px; border: 1px solid var(--border);
                background: var(--panel); box-shadow: var(--shadow); font-size: 13px; }
  .regime-bar .lbl { color: var(--text-dim); font-size: 12px; }
  .regime-chip { font-weight: 700; padding: 4px 11px; border-radius: 999px; font-size: 12px; }
  .regime-GREEN { background: var(--pass-bg); color: var(--pass-text); }
  .regime-YELLOW { background: var(--breakout-bg); color: var(--breakout-text); }
  .regime-RED { background: var(--na-bg); color: var(--na-text); }
  .regime-RECOVERY { background: var(--watch-bg); color: var(--watch-text); }
  .es { display: inline-block; padding: 2px 8px; border-radius: 6px; font-size: 11px; font-weight: 800; letter-spacing: .02em; white-space: nowrap; }
  .es-go { background: var(--pass-text); color: #fff; }
  .es-ready { background: var(--watch-bg); color: var(--watch-text); border: 1px solid var(--watch-border); }
  .es-setup { background: var(--watch-bg); color: var(--watch-text); }
  .es-warn { background: var(--breakout-bg); color: var(--breakout-text); border: 1px solid var(--breakout-border); }
  .es-bad { background: var(--na-bg); color: var(--na-text); }
  .es-neutral { background: var(--fail-bg); color: var(--text-dim); border: 1px solid var(--border); }
  .xs { display: inline-block; padding: 2px 7px; border-radius: 6px; font-size: 11px; font-weight: 700; white-space: nowrap; }
  .xs-hold { background: var(--fail-bg); color: var(--text-dim); border: 1px solid var(--border); }
  .xs-warn { background: var(--breakout-bg); color: var(--breakout-text); }
  .xs-bad { background: var(--na-bg); color: var(--na-text); }
  .cell-reason { color: var(--text-dim); font-size: 11px; max-width: 260px; white-space: normal; }
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
  tbody tr.go-row { background: var(--pass-bg); box-shadow: inset 3px 0 0 var(--pass-text); }
  .table-scroll { overflow-x: auto; max-height: 70vh; overflow-y: auto; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; font-weight: 700; }
  .badge.pass { background: var(--pass-bg); color: var(--pass-text); border: 1px solid var(--pass-border); }
  .badge.fail { background: var(--fail-bg); color: var(--text-dim); border: 1px solid var(--border); }
  .badge.na { background: var(--na-bg); color: var(--na-text); }
  .badge.watch { background: var(--watch-bg); color: var(--watch-text); border: 1px solid var(--watch-border); }
  .badge.breakout { background: var(--breakout-bg); color: var(--breakout-text); border: 1px solid var(--breakout-border); }
  .badge.hold { background: var(--na-bg); color: var(--na-text); border: 1px solid var(--na-text); margin-left: 4px; }
  .hold-note { margin: 0 0 14px; padding: 9px 12px; border-radius: 8px; font-size: 12px; font-weight: 600;
               background: var(--na-bg); color: var(--na-text); border: 1px solid var(--na-text); line-height: 1.5; }
  /* 매크로 카드(macro-card.js)를 기존 대시보드 팔레트·다크모드에 맞춤. 카드 내부 구조는 건드리지 않고 CSS 변수만 덮어씀 */
  #macro-card { display: block; margin-bottom: 18px; }
  #macro-card .mc {
    max-width: none;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Malgun Gothic", sans-serif;
    border-radius: 10px; box-shadow: var(--shadow);
    --mc-surface: var(--panel); --mc-plane: var(--bg);
    --mc-ink: var(--text); --mc-ink2: var(--text-dim); --mc-ink3: var(--text-dim);
    --mc-line: var(--border); --mc-series: var(--accent);
    --mc-good: var(--pass-text); --mc-warn: var(--breakout-text); --mc-crit: var(--na-text);
  }
  .change { font-weight: 600; }
  .change.up { color: var(--up); }
  .change.down { color: var(--down); }
  .metbar { display: inline-flex; gap: 2px; vertical-align: middle; }
  .metbar span { width: 6px; height: 12px; border-radius: 1px; background: var(--border); }
  .metbar span.on { background: var(--accent); }
  .code { color: var(--text-dim); font-size: 12px; }
  .empty-msg { text-align: center; color: var(--text-dim); padding: 30px; }
  footer { color: var(--text-dim); font-size: 12px; margin-top: 24px; line-height: 1.7; }
  svg.trend { width: 100%; height: 60px; display: block; }
  .chart-link { text-decoration: none; color: var(--text-dim); font-size: 13px; padding: 2px 4px; }
  .chart-link:hover { color: var(--accent); }
  .chart-btn { border: 1px solid var(--border); background: var(--bg); border-radius: 6px; padding: 2px 6px; cursor: pointer; font-size: 12px; margin-left: 4px; }
  .chart-btn:hover { border-color: var(--accent); }
  .modal-overlay { display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.5); z-index: 100; align-items: center; justify-content: center; padding: 16px; }
  .modal-overlay.open { display: flex; }
  .modal-box { background: var(--panel); border: 1px solid var(--border); border-radius: 12px; padding: 16px; max-width: 820px; width: 100%; max-height: 90vh; overflow-y: auto; box-shadow: var(--shadow); }
  .modal-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; font-weight: 700; }
  .modal-close { border: none; background: none; color: var(--text-dim); font-size: 16px; cursor: pointer; padding: 4px 8px; }
  .modal-close:hover { color: var(--text); }
  .modal-body canvas { width: 100%; display: block; margin-bottom: 6px; }
  .modal-note { color: var(--text-dim); font-size: 11px; margin-top: 4px; }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>SEPA 추세 템플릿 스크리너</h1>
    <div class="subtitle" id="subtitle">미너비니 추세 템플릿 8개 조건 1차 필터 + 타이밍 참고 지표 · 스테이지/베이스단계/펀더멘털/매매신호는 다루지 않음</div>
    <div class="nav">
      <a class="here" href="./">SEPA 추세템플릿</a>
      <a href="./range_vrebound/">RANGE-MR · V-REBOUND</a>
      <a href="./screener/">멀티팩터</a>
    </div>
  </header>

  <div id="macro-card" data-src="macro.json"></div><script src="macro-card.js"></script>

  <div class="tabs" id="tabs"></div>

  <div class="regime-bar" id="regimeBar" hidden></div>

  <div class="gate-row" id="gateRow"></div>

  <div class="cards" id="cards"></div>

  <div class="panel">
    <h2>일별 8개 조건 전부 통과 종목 수 추이</h2>
    <svg class="trend" id="trend"></svg>
  </div>

  <div class="panel">
    <div id="holdNote" class="hold-note" hidden>매크로 국면 <b>리스크오프</b> — 8/8 조건 전부 통과 종목이라도 신규 진입은 관망하세요. 통과 종목 판정 옆에 <span class="badge hold">관망</span> 표시가 붙습니다. (근거: 상단 매크로 브리핑 카드)</div>
    <div class="controls">
      <input type="text" id="search" placeholder="종목코드 또는 종목명 검색...">
      <button class="filter-btn active" data-filter="all">전체</button>
      <button class="filter-btn" data-filter="trend">TREND_OK</button>
      <button class="filter-btn" data-filter="setup">SETUP</button>
      <button class="filter-btn" data-filter="ready">READY</button>
      <button class="filter-btn" data-filter="go">GO</button>
      <button class="filter-btn" data-filter="go_breakout">GO_BREAKOUT</button>
      <button class="filter-btn" data-filter="go_pullback">GO_PULLBACK</button>
      <button class="filter-btn" data-filter="extended">LATE·EXTENDED</button>
      <button class="filter-btn" data-filter="exitwarn">매도경고</button>
      <button class="filter-btn" data-filter="pass">레거시 8/8</button>
      <button class="filter-btn" data-filter="na">확인불가</button>
      <select id="sortSelect">
        <option value="entryState">EntryState순</option>
        <option value="setupQuality">SetupQuality순</option>
        <option value="rsScore">RS Score순</option>
        <option value="rsChange20d">RS 20D변화순</option>
        <option value="pivotDist">피벗거리순</option>
        <option value="setupScore">셋업점수(레거시)순</option>
        <option value="metCount">충족조건수순</option>
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
    <b>SEPA Screener v2</b> — TREND(8조건) → SETUP(변동성·매물 수축) → READY(피벗 대기) → ENTRY(확인된 돌파/눌림목) → EXIT(실패·매도 경고)<br>
    ※ <b>Entry State</b>: GO_BREAKOUT(거래량·종가위치 확인된 돌파) · GO_PULLBACK(돌파 후 눌림목 반등) · READY(피벗 -2~0%) · SETUP/WATCH · BREAKOUT_UNCONFIRMED(돌파구간이나 미확인) · LATE(+3~5%)/EXTENDED(+5%↑, 추격 금지) · TREND_OK(셋업 전) · FAILED(돌파 빠른 실패). <b>단순히 올랐다고 매수 신호가 아닙니다.</b><br>
    ※ <b>Exit State</b>: HOLD · WATCH_EXIT(EMA10/20 이탈) · TREND_BREAK(SMA50 대량거래 이탈) · FAST_FAIL(돌파 직후 실패) · PROFIT_ALERT(클라이맥스 경고, 강제매도 아님). STOP/TIME_STOP 은 진입가·진입일(포지션)이 있어야 판정되며 스크리너 단독에선 표시되지 않습니다.<br>
    ※ <b>RS Score</b>(0~100) = 거래일 기준 초과수익 21·63·126·252일의 유니버스 내 percentile 가중합(0.10/0.40/0.30/0.20). ≥80 이면 TREND 통과, ≥90 강한 리더(★). RS Δ20d = 20거래일 전 대비 RS Score 변화. <b>레거시 "RS백분위"</b>(3·6·12개월 달력일 단순평균)도 별도 컬럼으로 비교 가능하게 남겨둡니다.<br>
    ※ <b>52W거리</b> = 종가/52주 고가 − 1. SUPER(≥90%) / LEADER(≥85%) / NORMAL(≥75%) / FAIL. <b>ATR수축</b> = ATR20/ATR60 (≤0.75 목표), <b>Dry-up</b> = 평균거래량10/50 (≤0.70 목표). VCP 는 "완전한 Minervini 재현" 이 아니라 스윙 기반 deterministic heuristic 입니다.<br>
    ※ 상단 <b>시장 국면</b>(GREEN/YELLOW/RED/RECOVERY) + breadth50 + 권장 진입비중은 신규진입 리스크 참고용이며 실제 주문 기능이 아닙니다. 상단 배지(우호적/중립/비우호적)는 기존 시장 게이팅(레거시)입니다.<br>
    ※ "레거시판정"·"충족(8)"·"셋업점수(레거시)"·"타이밍신호"는 기존 화면과 비교하기 위해 유지합니다. 스테이지(와인스타인 4단계)·베이스 단계·펀더멘털·촉매는 여전히 자동 판정하지 않습니다. 모든 임계값은 <code>sepa/config.py</code> 에서 조정됩니다.<br>
    ※ "↗" 는 외부 차트 사이트 링크, "📈" 미니차트는 레거시 8/8 통과 + v2 진입 후보(GO/READY)에 제공됩니다. 종가/SMA/거래량/RSI(14)·매물대·변곡점 전부 참고용입니다.
  </footer>
</div>

<div class="modal-overlay" id="chartModal">
  <div class="modal-box">
    <div class="modal-header">
      <span id="modalTitle"></span>
      <button class="modal-close" id="modalClose">✕</button>
    </div>
    <div class="modal-body">
      <canvas id="priceCanvas" width="760" height="300"></canvas>
      <canvas id="volumeCanvas" width="760" height="80"></canvas>
      <canvas id="rsiCanvas" width="760" height="80"></canvas>
      <div class="modal-note">종가/SMA50·150·200/거래량/RSI(14) · 오른쪽 축=가격, 옅은 가로막대=가격대별 거래량(매물대), 점선=최대 거래량대(POC)·밸류에어리어, ▲▼=스윙 고점/저점(변곡점) — 전부 참고용, 매수 신호 아님</div>
    </div>
  </div>
</div>

<script>
const DATA = __DATA_JSON__;
const marketKeys = Object.keys(DATA);
let currentMarket = marketKeys[0];
let currentFilter = "all";
let sortKey = "entryState";
let sortDir = -1;
let macroRegime = null;  // "risk_on" | "neutral" | "risk_off" — macro.json 에서 읽음

// EntryState 매력도 순위 (작을수록 진입 매력 높음). sepa/states.py 와 동일.
const ENTRY_RANK = {
  GO_BREAKOUT: 0, GO_PULLBACK: 1, READY: 2, BREAKOUT_UNCONFIRMED: 3, WATCH: 4,
  SETUP: 5, LATE: 6, EXTENDED: 7, TREND_OK: 8, FAILED: 9, TREND_FAIL: 10,
};
const GO_SET = new Set(["GO_BREAKOUT", "GO_PULLBACK"]);

function fmtNum(n, digits) {
  if (n === null || n === undefined || n === "") return "-";
  return Number(n).toLocaleString("ko-KR", { maximumFractionDigits: digits ?? 0, minimumFractionDigits: 0 });
}
function toNum(v) { return (v === null || v === undefined || v === "") ? null : Number(v); }
function toBool(v) { return v === true || v === "True" || v === "true"; }
function fmtSigned(v, digits) {
  const n = toNum(v);
  if (n === null || Number.isNaN(n)) return "-";
  return (n > 0 ? "+" : "") + n.toFixed(digits ?? 1);
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
      if (sortKey === "marcap" && !hasMarcap()) { sortKey = "entryState"; document.getElementById("sortSelect").value = "entryState"; }
      renderAll();
    });
  });
}

function renderGateRow() {
  const rows = DATA[currentMarket].rows;
  const segments = [...new Set(rows.map(r => r.market).filter(Boolean))];
  const tierClass = g => g === "우호적" ? "tier-good" : (g === "중립" ? "tier-mid" : "tier-bad");
  const chips = segments.map(seg => {
    const gate = (rows.find(r => r.market === seg && r.marketGate) || {}).marketGate || "판정불가";
    return `<span class="gate-chip ${tierClass(gate)}">${seg}: ${gate}</span>`;
  });
  document.getElementById("gateRow").innerHTML = chips.join("");
}

function renderRegimeBar() {
  const rows = DATA[currentMarket].rows;
  const el = document.getElementById("regimeBar");
  const segments = [...new Set(rows.map(r => r.market).filter(Boolean))];
  const parts = [];
  segments.forEach(seg => {
    const s = rows.find(r => r.market === seg && r.regime);
    if (!s) return;
    const bd = toNum(s.breadth), sf = toNum(s.sizeFactor);
    parts.push(
      `<span class="lbl">${seg}</span>` +
      `<span class="regime-chip regime-${s.regime}">${s.regime}</span>` +
      (bd != null ? `<span class="lbl">breadth50 ${(bd * 100).toFixed(0)}%</span>` : "") +
      (sf != null ? `<span class="lbl">권장 진입비중 <b style="color:var(--text)">${sf.toFixed(2)}</b></span>` : "")
    );
  });
  if (!parts.length) { el.hidden = true; return; }
  el.hidden = false;
  el.innerHTML = `<span class="lbl">시장 국면(v2)</span>` + parts.join(`<span style="opacity:.4">|</span>`) +
    `<span class="lbl" style="margin-left:auto">GREEN 정상 · YELLOW 축소 · RED 방어 · RECOVERY 회복초기 · 참고용, 주문기능 아님</span>`;
}

function renderCards() {
  const market = DATA[currentMarket];
  const rows = market.rows;
  const hist = market.history;
  const latest = hist[hist.length - 1] || {};
  const nTrend = rows.filter(r => toBool(r.trendOk)).length;
  const nGo = rows.filter(r => GO_SET.has(r.entryState)).length;
  const nReady = rows.filter(r => r.entryState === "READY").length;
  const hasV2 = rows.some(r => r.entryState);
  const cards = [
    ["기준일", market.asOf || "-"],
    ["스크리닝종목수", latest.total ?? rows.length],
    ["정상판정", latest.ok ?? rows.filter(r => r.status === "OK").length],
    ["레거시 8/8 통과", latest.pass ?? rows.filter(r => r.passAll === true).length],
  ];
  if (hasV2) {
    cards.push(["TREND_OK (v2)", nTrend], ["READY", nReady], ["GO 후보", nGo]);
  } else {
    cards.push(["확인불가/제외", rows.filter(r => r.status !== "OK").length],
               ["평균 RS백분위", latest.avgRs != null ? fmtNum(latest.avgRs, 1) : "-"]);
  }
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

function hasV2() {
  return DATA[currentMarket].rows.some(r => r.entryState);
}

function getCols() {
  const cols = [
    { key: "rank", label: "#", left: true },
    { key: "code", label: "코드", left: true },
    { key: "name", label: "종목명", left: true },
    { key: "chart", label: "차트", left: true, fmt: (v, r) => chartCell(r) },
    { key: "close", label: "종가", fmt: v => fmtNum(v) },
    { key: "changePct", label: "등락률", fmt: v => changeBadge(v) },
  ];
  if (hasV2()) {
    cols.push(
      { key: "entryState", label: "Entry State", left: true, fmt: (v, r) => entryStateBadge(v, r) },
      { key: "exitState", label: "Exit State", left: true, fmt: (v, r) => exitStateBadge(v, r) },
      { key: "trendOk", label: "Trend", fmt: v => trendBadge(v) },
      { key: "rsScore", label: "RS Score", fmt: v => rsScoreBadge(v) },
      { key: "rsChange20d", label: "RS Δ20d", fmt: v => fmtSigned(v, 1) },
      { key: "highProximity", label: "52W거리", fmt: (v, r) => w52DistCell(r) },
      { key: "setupQuality", label: "Setup Q", fmt: v => setupQualityBadge(v) },
      { key: "atrContraction", label: "ATR수축", fmt: v => ratioCell(v, 0.75) },
      { key: "volDryup", label: "Dry-up", fmt: v => ratioCell(v, 0.70) },
      { key: "pivotDist", label: "피벗거리", fmt: v => { const n = toNum(v); return n == null ? "-" : (n > 0 ? "+" : "") + n.toFixed(1) + "%"; } },
      { key: "setupReady", label: "Setup", fmt: v => toBool(v) ? `<span class="es es-setup">READY</span>` : "-" },
    );
  }
  cols.push({ key: "metCount", label: "충족(8)", fmt: (v) => metBar(v) });
  if (hasMarcap()) {
    cols.push({ key: "marcap", label: "시가총액", fmt: v => v ? fmtNum(v / 1e8, 0) + "억" : "-" });
  }
  cols.push(
    { key: "setupScore", label: "셋업점수(레거시)", fmt: v => setupScoreBadge(v) },
    { key: "signals", label: "타이밍신호", fmt: (v, r) => timingSignals(r) },
    { key: "passAll", label: "레거시판정", fmt: (v, r) => statusBadge(r) },
  );
  return cols;
}

function trendBadge(v) {
  if (v === null || v === undefined || v === "") return "-";
  return toBool(v) ? `<span class="es es-go" title="TREND TEMPLATE 8조건(RS v2 포함) 전부 충족">OK</span>`
                   : `<span class="es es-bad">X</span>`;
}

function entryStateBadge(v, r) {
  if (!v) return "-";
  const cls = GO_SET.has(v) ? "es-go" : v === "READY" ? "es-ready"
    : (v === "SETUP" || v === "WATCH" || v === "BREAKOUT_UNCONFIRMED") ? "es-setup"
    : (v === "LATE" || v === "EXTENDED") ? "es-warn"
    : (v === "FAILED" || v === "TREND_FAIL") ? "es-bad" : "es-neutral";
  const rsn = (r && r.entryReason) ? String(r.entryReason).replace(/"/g, "&quot;") : "";
  return `<span class="es ${cls}" title="${rsn}">${v}</span>`;
}

function exitStateBadge(v, r) {
  if (!v) return "-";
  if (v === "HOLD") return `<span class="xs xs-hold">HOLD</span>`;
  const cls = (v === "FAST_FAIL" || v === "STOP" || v === "TREND_BREAK") ? "xs-bad" : "xs-warn";
  const rsn = (r && r.exitReason) ? String(r.exitReason).replace(/"/g, "&quot;") : "";
  return `<span class="xs ${cls}" title="${rsn}">${v}</span>`;
}

function rsScoreBadge(v) {
  const n = toNum(v);
  if (n === null) return "-";
  const hue = 4 + Math.max(0, Math.min(100, n)) / 100 * 146;
  const strong = n >= 90 ? " ★" : "";
  return `<span style="display:inline-block;min-width:30px;padding:2px 6px;border-radius:6px;font-weight:700;background:hsl(${hue},70%,92%);color:hsl(${hue},60%,30%)">${n.toFixed(0)}${strong}</span>`;
}

function setupQualityBadge(v) {
  const n = toNum(v);
  if (n === null) return "-";
  const hue = 4 + Math.max(0, Math.min(100, n)) / 100 * 146;
  return `<span style="display:inline-block;min-width:30px;padding:2px 6px;border-radius:6px;font-weight:700;background:hsl(${hue},60%,93%);color:hsl(${hue},55%,32%)">${n.toFixed(0)}</span>`;
}

function ratioCell(v, threshold) {
  const n = toNum(v);
  if (n === null) return "-";
  const ok = n <= threshold;
  return `<span style="color:${ok ? "var(--pass-text)" : "var(--text-dim)"};font-weight:${ok ? 700 : 400}">${n.toFixed(2)}</span>`;
}

function w52DistCell(r) {
  const hp = toNum(r.highProximity);
  if (hp === null) return "-";
  const dist = (hp - 1) * 100;
  const tier = r.highTier || "";
  const tcls = (tier === "SUPER_LEADER" || tier === "LEADER") ? "tier-good"
    : tier === "NORMAL" ? "tier-mid" : "tier-bad";
  const tlabel = tier === "SUPER_LEADER" ? "SUPER" : tier;
  return `${dist.toFixed(1)}% ${tier ? `<span class="badge ${tcls}" style="font-size:10px">${tlabel}</span>` : ""}`;
}

function externalChartUrl(r) {
  if (r.market === "KOSPI" || r.market === "KOSDAQ") {
    return `https://finance.naver.com/item/main.naver?code=${encodeURIComponent(r.code)}`;
  }
  return `https://finance.yahoo.com/quote/${encodeURIComponent(r.code)}`;
}

function chartCell(r) {
  const url = externalChartUrl(r);
  const ext = `<a class="chart-link" href="${url}" target="_blank" rel="noopener noreferrer" title="외부 차트 사이트에서 보기 (SEPA 스크리너와 무관)">↗</a>`;
  const hasChart = !!((DATA[currentMarket].charts || {})[r.code]);
  const mini = hasChart
    ? `<button class="chart-btn" data-code="${r.code}" title="미니차트 보기 (종가/SMA/거래량/RSI, 참고용)">📈</button>`
    : "";
  return ext + mini;
}

function fmtPct(v) {
  return (v >= 0 ? "+" : "") + (v * 100).toFixed(1) + "%";
}

function changeBadge(v) {
  if (v === null || v === undefined) return "-";
  const cls = v > 0 ? "up" : (v < 0 ? "down" : "");
  return `<span class="change ${cls}">${fmtPct(v)}</span>`;
}

function setupScoreBadge(v) {
  if (v === null || v === undefined) return "-";
  const pct = Math.max(0, Math.min(100, v * 10));
  const hue = 4 + (pct / 100) * 146; // 낮으면 빨강 계열, 높으면 초록 계열
  return `<span style="display:inline-block;min-width:34px;padding:2px 6px;border-radius:6px;font-weight:700;background:hsl(${hue},70%,92%);color:hsl(${hue},60%,32%);">${fmtNum(v, 1)}</span>`;
}

function timingSignals(r) {
  const badges = [];
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
  if (!r.passAll) return `<span class="badge fail">미통과</span>`;
  const hold = macroRegime === "risk_off"
    ? `<span class="badge hold" title="매크로 국면 리스크오프 — 8/8 통과라도 신규 진입 관망 (상단 매크로 브리핑 참조)">관망</span>`
    : "";
  return `<span class="badge pass">전체통과</span>${hold}`;
}

function renderTable() {
  const q = document.getElementById("search").value.trim().toLowerCase();
  let rows = DATA[currentMarket].rows.slice();

  const F = {
    pass: r => r.passAll === true,
    na: r => r.status !== "OK",
    trend: r => toBool(r.trendOk),
    setup: r => r.entryState === "SETUP" || toBool(r.setupReady),
    ready: r => r.entryState === "READY",
    go: r => GO_SET.has(r.entryState),
    go_breakout: r => r.entryState === "GO_BREAKOUT",
    go_pullback: r => r.entryState === "GO_PULLBACK",
    extended: r => r.entryState === "LATE" || r.entryState === "EXTENDED",
    exitwarn: r => r.exitState && r.exitState !== "HOLD",
  };
  if (F[currentFilter]) rows = rows.filter(F[currentFilter]);

  if (q) rows = rows.filter(r =>
    (r.code || "").toLowerCase().includes(q) || (r.name || "").toLowerCase().includes(q)
  );

  rows.sort((a, b) => {
    if (sortKey === "entryState") {
      const ar = ENTRY_RANK[a.entryState] ?? 99, br = ENTRY_RANK[b.entryState] ?? 99;
      return sortDir * -1 * (ar - br);   // 기본(sortDir=-1)에서 GO 가 위로
    }
    let av = a[sortKey], bv = b[sortKey];
    if (av === "" ) av = null;
    if (bv === "" ) bv = null;
    if (av === null || av === undefined) return 1;
    if (bv === null || bv === undefined) return -1;
    if (typeof av === "string" && isNaN(Number(av))) return sortDir * av.localeCompare(bv);
    return sortDir * (Number(av) - Number(bv));
  });

  const cols = getCols();
  const thead = document.getElementById("thead-row");
  thead.innerHTML = cols.map(c =>
    `<th class="${c.left ? "left" : ""}${c.key===sortKey?" sorted"+(sortDir===1?" asc":""):""}" data-key="${c.key}">${c.label}</th>`
  ).join("");
  thead.querySelectorAll("th").forEach(th => {
    th.addEventListener("click", () => {
      const key = th.dataset.key;
      if (key === "rank" || key === "signals" || key === "chart") return;
      if (sortKey === key) sortDir *= -1; else { sortKey = key; sortDir = -1; }
      const sel = document.getElementById("sortSelect");
      if ([...sel.options].some(o => o.value === key)) sel.value = key;
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
    const cls = GO_SET.has(r.entryState) ? "go-row" : (r.passAll ? "pass-row" : "");
    return `<tr class="${cls}">${cells}</tr>`;
  }).join("");
}

function syncFilterButtons() {
  const v2 = hasV2();
  const v2only = new Set(["trend", "setup", "ready", "go", "go_breakout", "go_pullback", "extended", "exitwarn"]);
  document.querySelectorAll(".filter-btn").forEach(b => {
    if (v2only.has(b.dataset.filter)) b.hidden = !v2;
  });
  document.querySelectorAll("#sortSelect option").forEach(o => {
    if (["entryState", "setupQuality", "rsScore", "rsChange20d", "pivotDist"].includes(o.value)) o.hidden = !v2;
  });
  if (!v2 && ["trend","setup","ready","go","go_breakout","go_pullback","extended","exitwarn"].includes(currentFilter)) {
    currentFilter = "all";
    document.querySelectorAll(".filter-btn").forEach(b => b.classList.toggle("active", b.dataset.filter === "all"));
  }
  if (!v2 && ["entryState","setupQuality","rsScore","rsChange20d","pivotDist"].includes(sortKey)) {
    sortKey = "metCount";
  }
}

function renderAll() {
  renderTabs();
  syncFilterButtons();
  renderRegimeBar();
  renderGateRow();
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

document.getElementById("tbody").addEventListener("click", (e) => {
  const btn = e.target.closest(".chart-btn");
  if (btn) openChartModal(btn.dataset.code);
});
document.getElementById("modalClose").addEventListener("click", closeChartModal);
document.getElementById("chartModal").addEventListener("click", (e) => {
  if (e.target.id === "chartModal") closeChartModal();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeChartModal();
});

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function openChartModal(code) {
  const chart = (DATA[currentMarket].charts || {})[code];
  const row = DATA[currentMarket].rows.find(r => r.code === code);
  if (!chart || !row) return;
  document.getElementById("modalTitle").textContent = `${row.name} (${code})`;
  drawPriceChart(chart);
  drawVolumeChart(chart);
  drawRsiChart(chart);
  document.getElementById("chartModal").classList.add("open");
}

function closeChartModal() {
  document.getElementById("chartModal").classList.remove("open");
}

function plotLine(ctx, values, x, y, color, width) {
  ctx.beginPath();
  let started = false;
  values.forEach((v, i) => {
    if (v === null || v === undefined) { started = false; return; }
    if (!started) { ctx.moveTo(x(i), y(v)); started = true; }
    else ctx.lineTo(x(i), y(v));
  });
  ctx.strokeStyle = color;
  ctx.lineWidth = width;
  ctx.stroke();
}

function priceFmt(v) {
  if (v === null || v === undefined || !isFinite(v)) return "-";
  if (Math.abs(v) >= 1000) return Math.round(v).toLocaleString("ko-KR");
  if (Math.abs(v) >= 100) return v.toFixed(1);
  return v.toFixed(2);
}

// 스윙 고점/저점(변곡점): ±win 거래일 안에서 고가가 최대(H)이거나 저가가 최소(L)인 지점
function computeSwings(high, low, win) {
  const n = high.length, out = [];
  for (let i = win; i < n - win; i++) {
    if (high[i] === null || high[i] === undefined || low[i] === null || low[i] === undefined) continue;
    let isH = true, isL = true;
    for (let j = i - win; j <= i + win && (isH || isL); j++) {
      if (j === i) continue;
      if (high[j] !== null && high[j] !== undefined && high[j] > high[i]) isH = false;
      if (low[j] !== null && low[j] !== undefined && low[j] < low[i]) isL = false;
    }
    if (isH) out.push({ i, price: high[i], type: "H" });
    else if (isL) out.push({ i, price: low[i], type: "L" });
  }
  return out;
}

// 가격대별 거래량(매물대): 각 날의 거래량을 그날 고가~저가 구간 버킷에 고르게 배분
function volumeProfile(high, low, vol, lo, hi, bins) {
  const buckets = new Array(bins).fill(0);
  const step = (hi - lo) / bins || 1;
  for (let i = 0; i < vol.length; i++) {
    const v = vol[i], H = high[i], L = low[i];
    if (v === null || v === undefined || v <= 0 || H === null || H === undefined || L === null || L === undefined) continue;
    let b0 = Math.floor((L - lo) / step), b1 = Math.floor((H - lo) / step);
    b0 = Math.max(0, Math.min(bins - 1, b0));
    b1 = Math.max(0, Math.min(bins - 1, b1));
    const span = Math.max(1, b1 - b0 + 1);
    for (let b = b0; b <= b1; b++) buckets[b] += v / span;
  }
  // POC(최대 거래량대) 기준으로 좌우로 확장해 총거래량의 pct(=70%)를 담는 밸류에어리어
  const total = buckets.reduce((a, b) => a + b, 0);
  let va = null;
  if (total > 0) {
    let poc = 0;
    buckets.forEach((v, i) => { if (v > buckets[poc]) poc = i; });
    let vaLow = poc, vaHigh = poc, acc = buckets[poc];
    while (acc < total * 0.7 && (vaLow > 0 || vaHigh < bins - 1)) {
      const dn = vaLow > 0 ? buckets[vaLow - 1] : -1;
      const up = vaHigh < bins - 1 ? buckets[vaHigh + 1] : -1;
      if (up >= dn) { vaHigh++; acc += Math.max(0, up); }
      else { vaLow--; acc += Math.max(0, dn); }
    }
    va = { poc, vaLow, vaHigh };
  }
  return { buckets, step, va };
}

function drawPriceChart(chart) {
  const canvas = document.getElementById("priceCanvas");
  const ctx = canvas.getContext("2d");
  const w = canvas.width, h = canvas.height;
  const padL = 6, padR = 62, padT = 18, padB = 18;
  ctx.clearRect(0, 0, w, h);

  const close = chart.close || [];
  const n = close.length;
  if (!n) return;
  // 구버전 스냅샷(고저가 없음) 호환: 없으면 종가로 대체
  const high = (Array.isArray(chart.high) && chart.high.length === n) ? chart.high : close;
  const low = (Array.isArray(chart.low) && chart.low.length === n) ? chart.low : close;

  const series = [
    { data: close, label: "종가", color: cssVar("--text"), width: 1.6 },
    { data: chart.sma50, label: "SMA50", color: "#e0555a", width: 1.1 },
    { data: chart.sma150, label: "SMA150", color: "#d4a017", width: 1.1 },
    { data: chart.sma200, label: "SMA200", color: "#3b7ddb", width: 1.1 },
  ];

  // y 범위: 고저가 + 이동평균 전부 포함
  let lo = Infinity, hi = -Infinity;
  const consider = v => { if (v !== null && v !== undefined) { if (v < lo) lo = v; if (v > hi) hi = v; } };
  for (let i = 0; i < n; i++) { consider(high[i]); consider(low[i]); }
  series.slice(1).forEach(s => (s.data || []).forEach(consider));
  if (!isFinite(lo) || !isFinite(hi) || lo >= hi) {
    const c = close.filter(v => v !== null && v !== undefined);
    lo = Math.min(...c); hi = Math.max(...c);
  }
  const padv = (hi - lo) * 0.04 || 1;
  lo -= padv; hi += padv;

  const plotW = w - padL - padR, plotH = h - padT - padB;
  const x = i => padL + (i / Math.max(n - 1, 1)) * plotW;
  const y = v => padT + (1 - (v - lo) / ((hi - lo) || 1)) * plotH;

  ctx.font = "10px sans-serif";
  ctx.textAlign = "left";

  // --- 매물대(가격대별 거래량) : 오른쪽 끝에서 왼쪽으로 자라는 옅은 가로막대 ---
  const bins = 26;
  const { buckets, step, va } = volumeProfile(high, low, chart.volume || [], lo, hi, bins);
  const maxB = Math.max(...buckets, 1);
  const binH = plotH / bins, PROF_W = 108;
  for (let b = 0; b < bins; b++) {
    if (buckets[b] <= 0) continue;
    const bw = (buckets[b] / maxB) * PROF_W;
    const yy = padT + (bins - 1 - b) * binH;
    const isPoc = va && b === va.poc;
    const inVA = va && b >= va.vaLow && b <= va.vaHigh;
    ctx.fillStyle = cssVar(isPoc ? "--breakout-text" : "--accent");
    ctx.globalAlpha = isPoc ? 0.34 : (inVA ? 0.17 : 0.09);
    ctx.fillRect(w - padR - bw, yy + 1, bw, Math.max(binH - 1.5, 1));
  }
  ctx.globalAlpha = 1;

  // --- 가로 눈금 + 오른쪽 가격축 ---
  const TICKS = 5;
  for (let t = 0; t <= TICKS; t++) {
    const val = lo + (hi - lo) * t / TICKS, yy = y(val);
    ctx.strokeStyle = cssVar("--border"); ctx.globalAlpha = 0.5;
    ctx.beginPath(); ctx.moveTo(padL, yy); ctx.lineTo(w - padR, yy); ctx.stroke();
    ctx.globalAlpha = 1;
    ctx.fillStyle = cssVar("--text-dim");
    ctx.fillText(priceFmt(val), w - padR + 4, yy + 3);
  }

  // --- POC / 밸류에어리어 점선 + 가격 라벨 (왼쪽) ---
  if (va) {
    ctx.setLineDash([4, 3]);
    [["POC", va.poc, cssVar("--breakout-text"), 0.9],
     ["VAH", va.vaHigh, cssVar("--text-dim"), 0.5],
     ["VAL", va.vaLow, cssVar("--text-dim"), 0.5]].forEach(([label, b, color, a]) => {
      const price = lo + (b + 0.5) * step, yy = y(price);
      ctx.strokeStyle = color; ctx.globalAlpha = a;
      ctx.beginPath(); ctx.moveTo(padL, yy); ctx.lineTo(w - padR, yy); ctx.stroke();
      ctx.globalAlpha = 1; ctx.fillStyle = color;
      ctx.fillText(label + " " + priceFmt(price), padL + 2, yy - 2);
    });
    ctx.setLineDash([]);
  }

  // --- 가격 라인 ---
  series.forEach(s => plotLine(ctx, s.data, x, y, s.color, s.width));

  // --- 스윙 고점/저점(변곡점) : 전체 최고/최저 + 최근 스윙 최대 6개 ---
  let gmax = 0, gmin = 0;
  for (let i = 0; i < n; i++) {
    if (high[i] !== null && high[i] !== undefined && !(high[gmax] > high[i])) gmax = i;
    if (low[i] !== null && low[i] !== undefined && !(low[gmin] < low[i])) gmin = i;
  }
  const cand = [{ i: gmax, price: high[gmax], type: "H" }, { i: gmin, price: low[gmin], type: "L" }]
    .concat(computeSwings(high, low, 12).sort((a, b) => b.i - a.i));
  const shown = [];
  for (const sw of cand) {
    if (shown.length >= 6) break;
    if (sw.price === null || sw.price === undefined) continue;
    if (shown.some(p => p.i === sw.i ||
        (Math.abs(y(p.price) - y(sw.price)) < 13 && Math.abs(x(p.i) - x(sw.i)) < 70))) continue;
    shown.push(sw);
  }
  shown.forEach(sw => {
    const px = x(sw.i), py = y(sw.price);
    ctx.fillStyle = cssVar(sw.type === "H" ? "--up" : "--down");
    ctx.beginPath(); ctx.arc(px, py, 2.6, 0, Math.PI * 2); ctx.fill();
    const label = (sw.type === "H" ? "▲ " : "▼ ") + priceFmt(sw.price);
    const tw = ctx.measureText(label).width;
    let tx = Math.max(padL, Math.min(px - tw / 2, w - padR - tw));
    const ty = sw.type === "H" ? Math.max(padT + 8, py - 5) : Math.min(h - padB - 2, py + 11);
    ctx.fillText(label, tx, ty);
  });

  // --- 현재가 태그 (오른쪽 축) ---
  const lastClose = close[n - 1];
  if (lastClose !== null && lastClose !== undefined) {
    const yy = y(lastClose);
    ctx.fillStyle = cssVar("--accent");
    ctx.fillRect(w - padR, yy - 7, padR, 14);
    ctx.fillStyle = "#fff";
    ctx.fillText(priceFmt(lastClose), w - padR + 4, yy + 3);
  }

  // --- 범례 + 날짜 ---
  ctx.font = "11px sans-serif";
  series.forEach((s, i) => {
    ctx.fillStyle = s.color;
    ctx.fillRect(padL + i * 74, 3, 9, 9);
    ctx.fillStyle = cssVar("--text-dim");
    ctx.fillText(s.label, padL + i * 74 + 12, 11);
  });
  ctx.fillStyle = cssVar("--text-dim");
  ctx.font = "10px sans-serif";
  ctx.textAlign = "left";
  ctx.fillText(chart.dates[0], padL, h - 5);
  ctx.textAlign = "right";
  ctx.fillText(chart.dates[chart.dates.length - 1], w - padR, h - 5);
  ctx.textAlign = "left";
}

function drawVolumeChart(chart) {
  const canvas = document.getElementById("volumeCanvas");
  const ctx = canvas.getContext("2d");
  const w = canvas.width, h = canvas.height, pad = 6, bottomPad = 4;
  ctx.clearRect(0, 0, w, h);
  const vals = chart.volume.filter(v => v !== null && v !== undefined);
  if (!vals.length) return;
  const max = Math.max(...vals, 1);
  const n = chart.volume.length;
  const barW = (w - pad * 2) / n;
  ctx.fillStyle = cssVar("--accent");
  chart.volume.forEach((v, i) => {
    if (v === null || v === undefined) return;
    const bh = (v / max) * (h - pad - bottomPad - 12);
    ctx.fillRect(pad + i * barW, h - bottomPad - bh, Math.max(barW - 1, 1), bh);
  });
  ctx.fillStyle = cssVar("--text-dim");
  ctx.font = "11px sans-serif";
  ctx.fillText("거래량", pad, 12);
}

function drawRsiChart(chart) {
  const canvas = document.getElementById("rsiCanvas");
  const ctx = canvas.getContext("2d");
  const w = canvas.width, h = canvas.height, pad = 22, topPad = 14, bottomPad = 6;
  ctx.clearRect(0, 0, w, h);
  const n = chart.rsi.length;
  const x = i => pad + (i / Math.max(n - 1, 1)) * (w - pad - 6);
  const y = v => topPad + (1 - v / 100) * (h - topPad - bottomPad);

  ctx.strokeStyle = cssVar("--border");
  ctx.setLineDash([4, 3]);
  [30, 70].forEach(level => {
    ctx.beginPath();
    ctx.moveTo(pad, y(level));
    ctx.lineTo(w - 6, y(level));
    ctx.stroke();
  });
  ctx.setLineDash([]);

  plotLine(ctx, chart.rsi, x, y, cssVar("--accent"), 1.4);

  ctx.fillStyle = cssVar("--text-dim");
  ctx.font = "11px sans-serif";
  ctx.fillText("RSI(14)", pad, 12);
  ctx.fillText("70", 2, y(70) + 3);
  ctx.fillText("30", 2, y(30) + 3);
}

renderAll();

// --- 매크로 국면 연동 -----------------------------------------------------
// macro-card.js 와 독립적으로 macro.json 을 직접 읽는다(로드 순서에 의존하지 않음).
// 실패하면 macro-card.js 가 노출하는 window.MACRO_REGIME 로 폴백.
function applyMacroRegime(regime) {
  if (!regime || regime === macroRegime) return;
  macroRegime = regime;
  document.documentElement.dataset.macroRegime = regime;
  const note = document.getElementById("holdNote");
  if (note) note.hidden = (regime !== "risk_off");
  renderTable();
}
fetch("macro.json?t=" + Date.now())
  .then(r => r.ok ? r.json() : Promise.reject(r.status))
  .then(d => applyMacroRegime(d.regime))
  .catch(() => { if (window.MACRO_REGIME) applyMacroRegime(window.MACRO_REGIME); });
// macro-card.js 가 이 스크립트보다 늦게 fetch 를 끝내는 경우 대비
setTimeout(() => { if (!macroRegime && window.MACRO_REGIME) applyMacroRegime(window.MACRO_REGIME); }, 3000);
</script>
</body>
</html>
"""

if __name__ == "__main__":
    build()
