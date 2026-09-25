"""momentum_signals/generate_dashboard.py — docs/momentum/index.html 생성.

다른 서브시스템과 달리 이 스크립트는 output/ 스크래치를 거치지 않고
../docs/momentum/data/*.json(그 자체가 누적 상태)을 직접 읽는다 —
run_daily_screen.py가 이미 그 경로에 최신 값을 써 둔다."""
from __future__ import annotations

import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parent
DOCS_DIR = REPO_ROOT / "docs" / "momentum"
DATA_DIR = DOCS_DIR / "data"


def _load(name: str, default):
    p = DATA_DIR / name
    if not p.exists():
        return default
    return json.loads(p.read_text(encoding="utf-8"))


def render_html(latest: dict | None, positions: list, history: list) -> str:
    payload = {"latest": latest, "positions": positions, "history": history}
    data_json = json.dumps(payload, ensure_ascii=False)
    return HTML_TEMPLATE.replace("__DATA_JSON__", data_json)


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>모멘텀 전략 — RS 강도 스코어링 자동 페이퍼 트레이딩</title>
<style>
  :root { --bg:#0b0e14; --panel:#131720; --border:#242b3a; --text:#e6e9ef; --muted:#8b93a7;
          --accent:#4f8cff; --good:#3ecf8e; --warn:#f2b84b; --bad:#f2545b; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--text); font-family:-apple-system,"Segoe UI",Pretendard,sans-serif; }
  header { padding:20px 24px; border-bottom:1px solid var(--border); }
  header h1 { margin:0 0 4px; font-size:20px; }
  header p { margin:0; color:var(--muted); font-size:13px; }
  nav.top { display:flex; gap:10px; padding:12px 24px; border-bottom:1px solid var(--border); flex-wrap:wrap; }
  nav.top a { color:var(--muted); text-decoration:none; font-size:13px; padding:6px 10px; border-radius:6px; }
  nav.top a.here, nav.top a:hover { color:var(--text); background:var(--panel); }
  main { padding:20px 24px 60px; max-width:1200px; margin:0 auto; }
  .banner { background:#2a1f10; border:1px solid #5a3d12; color:#f2b84b; padding:10px 14px;
            border-radius:8px; font-size:13px; margin-bottom:16px; }
  .cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:10px; margin-bottom:20px; }
  .card { background:var(--panel); border:1px solid var(--border); border-radius:10px; padding:12px 14px; }
  .card .lbl { color:var(--muted); font-size:12px; margin-bottom:4px; }
  .card .val { font-size:18px; font-weight:600; }
  section { margin-bottom:28px; }
  section h2 { font-size:15px; margin:0 0 4px; }
  section .desc { color:var(--muted); font-size:12px; margin:0 0 10px; }
  table { width:100%; border-collapse:collapse; font-size:12.5px; background:var(--panel);
          border:1px solid var(--border); border-radius:8px; overflow:hidden; }
  th, td { padding:7px 9px; text-align:right; border-bottom:1px solid var(--border); white-space:nowrap; }
  th { color:var(--muted); font-weight:600; background:#0f131c; position:sticky; top:0; }
  td.left, th.left { text-align:left; }
  tbody tr:last-child td { border-bottom:none; }
  .badge { display:inline-block; padding:2px 8px; border-radius:999px; font-size:11.5px; }
  .badge.on { background:rgba(62,207,142,.15); color:var(--good); }
  .badge.off { background:rgba(242,84,91,.15); color:var(--bad); }
  .badge.warn { background:rgba(242,184,75,.15); color:var(--warn); }
  .badge.na { background:rgba(139,147,167,.15); color:var(--muted); }
  .scroll-x { overflow-x:auto; }
  .table-wrap { max-height:420px; overflow-y:auto; }
  footer { border-top:1px solid var(--border); padding:18px 24px 40px; color:var(--muted); font-size:12px; line-height:1.7; }
  footer b { color:var(--text); }
  .empty { color:var(--muted); font-size:13px; padding:14px; text-align:center; }
</style>
</head>
<body>
<header>
  <h1>모멘텀 전략 — RS 강도 스코어링 자동 페이퍼 트레이딩</h1>
  <p>국내 유동성 상위 200 + 미국 S&amp;P500 · 스캔: 국내 전일 종가 · 미국 당일 종가 · 실제 주문 절대 실행 안 함(전량 시뮬레이션, 참고용)</p>
</header>
<nav class="top">
  <a href="../index.html">SEPA</a>
  <a href="../screener/index.html">멀티팩터</a>
  <a href="../range_vrebound/index.html">RANGE-MR</a>
  <a href="../technical/index.html">기술적 신호</a>
  <a class="here" href="../momentum/index.html">모멘텀 전략</a>
  <a href="../recovery/index.html">계좌복구 공격매매</a>
  <a href="../fpd/index.html">FPD 연구</a>
</nav>
<main>
  <div class="banner" id="equityBanner" style="display:none">
    ⚠ 계좌 총자산이 momentum_signals/config.py의 자리표시자 값입니다. 실제 계좌 리스크 사이징과 다릅니다 —
    config.py의 TOTAL_EQUITY_KRW / TOTAL_EQUITY_USD를 실제 값으로 직접 수정하세요.
  </div>
  <div class="cards" id="cards"></div>

  <section>
    <h2>1~3. 통합 상위 5종목 · 하드게이트 · 강도점수 산출내역</h2>
    <p class="desc">RS 상대강도 기준 국내/미국 시장별 상위를 병합해 통합 상위 5종목을 먼저 확정한 뒤, 그 5종목에만 하드게이트(거래량·리스크)와 100점 만점 강도점수를 적용합니다. 78점 이상 &amp; 게이트 통과만 후보로 남습니다.</p>
    <div class="scroll-x"><table id="top5Table"></table></div>
  </section>

  <section>
    <h2>4. 최종후보 3개 · 신규진입 대상 2개</h2>
    <p class="desc">상위 5종목 중 게이트 통과 + 78점 이상인 종목의 점수 상위 3개가 최종후보, 그중 상위 2개가 신규진입 대상입니다(동점시 RS 랭킹 우선). 시장 국면이 보류거나 동시보유 6개를 초과하면 진입하지 않습니다.</p>
    <div class="scroll-x"><table id="finalistTable"></table></div>
  </section>

  <section>
    <h2>5. 오늘 신규진입 종목 사이징</h2>
    <p class="desc">진입가·손절가(ATR14×2 손절가와 최근 20일 스윙로우 중 더 타이트한 쪽)·실투입액·계좌 리스크%입니다.</p>
    <div class="scroll-x"><table id="entryTable"></table></div>
  </section>

  <section>
    <h2>6. 시장 국면</h2>
    <p class="desc">코스피 / S&amp;P500이 각각 20일 이평 아래면 그 시장은 신규진입을 보류합니다(기존 보유 포지션 관리는 계속됩니다).</p>
    <div class="cards" id="regimeCards"></div>
  </section>

  <section>
    <h2>보유 포지션 현황</h2>
    <p class="desc">스펙 7개 출력 항목 외에 추가한 섹션입니다 — 자동 페이퍼 진입 방식이라 보유 중인 포지션의 현재 손절가·R배수를 보여주지 않으면 시스템 상태를 확인할 방법이 없습니다.</p>
    <div class="scroll-x"><table id="positionsTable"></table></div>
  </section>

  <section>
    <h2>7. 최근 180세션 후보/진입 기록</h2>
    <p class="desc">그날의 통합 상위 5종목 전부를 기록합니다(진입 여부와 무관). 진입한 종목은 보유 포지션과 연결해 현재 R배수를 함께 보여줍니다.</p>
    <div class="table-wrap scroll-x"><table id="historyTable"></table></div>
  </section>
</main>
<footer id="footer"></footer>

<script id="data" type="application/json">__DATA_JSON__</script>
<script>
const DATA = JSON.parse(document.getElementById("data").textContent);
const fmt = (v, d=2) => (v === null || v === undefined || Number.isNaN(v)) ? "-" : Number(v).toFixed(d);
const fmtInt = v => (v === null || v === undefined) ? "-" : Math.round(v).toLocaleString();
const marketLabel = m => m === "KR" ? "국내" : "미국";

function badge(ok, onText, offText, naText) {
  if (ok === null || ok === undefined) return `<span class="badge na">${naText || "확인불가"}</span>`;
  return ok ? `<span class="badge on">${onText}</span>` : `<span class="badge off">${offText}</span>`;
}

function renderCards() {
  const latest = DATA.latest;
  const openCount = DATA.positions.filter(p => p.status === "OPEN").length;
  const cards = [
    ["기준일", latest ? latest.date : "-"],
    ["보유 포지션", openCount],
    ["오늘 신규진입", latest ? latest.openedToday.length : 0],
    ["최종후보", latest ? latest.finalists.length : 0],
  ];
  document.getElementById("cards").innerHTML = cards.map(([l, v]) =>
    `<div class="card"><div class="lbl">${l}</div><div class="val">${v}</div></div>`).join("");
}

function rowCommon(row) {
  return `<td class="left">${marketLabel(row.market)}</td><td class="left">${row.name}(${row.code})</td>`;
}

function renderTop5() {
  const el = document.getElementById("top5Table");
  const rows = (DATA.latest && DATA.latest.top5) || [];
  if (!rows.length) { el.innerHTML = `<tr><td class="empty">데이터 없음</td></tr>`; return; }
  el.innerHTML = `<thead><tr>
      <th class="left">시장</th><th class="left">종목</th><th>RS%</th>
      <th>거래량게이트</th><th>리스크게이트</th>
      <th>RS(25)</th><th>거래량(15)</th><th>피벗(10)</th><th>CLV(15)</th><th>RSI(15)</th><th>이격도(10)</th><th>리스크효율(10)</th>
      <th>총점</th><th>통과</th>
    </tr></thead><tbody>` + rows.map(r => `<tr>
      ${rowCommon(r)}
      <td>${fmt(r.rs_score, 1)}</td>
      <td>${badge(r.gate.volumeOk, "OK", "미달")}</td>
      <td>${badge(r.gate.riskOk, "OK", "초과")}</td>
      <td>${fmt(r.scoreBreakdown.rs)}</td>
      <td>${fmt(r.scoreBreakdown.volume)}</td>
      <td>${fmt(r.scoreBreakdown.pivot)}</td>
      <td>${fmt(r.scoreBreakdown.clv)}</td>
      <td>${fmt(r.scoreBreakdown.rsi)}</td>
      <td>${fmt(r.scoreBreakdown.disparity)}</td>
      <td>${fmt(r.scoreBreakdown.riskEfficiency)}</td>
      <td><b>${fmt(r.scoreBreakdown.total)}</b></td>
      <td>${badge(r.gate.passed, "통과", "미달")}</td>
    </tr>`).join("") + `</tbody>`;
}

function renderFinalists() {
  const el = document.getElementById("finalistTable");
  const rows = (DATA.latest && DATA.latest.finalists) || [];
  const entryCodes = new Set((DATA.latest ? DATA.latest.entries : []).map(r => r.market + r.code));
  if (!rows.length) { el.innerHTML = `<tr><td class="empty">78점 이상 &amp; 게이트 통과 후보 없음</td></tr>`; return; }
  el.innerHTML = `<thead><tr><th class="left">시장</th><th class="left">종목</th><th>총점</th><th>신규진입대상</th></tr></thead><tbody>` +
    rows.map(r => `<tr>${rowCommon(r)}<td><b>${fmt(r.scoreBreakdown.total)}</b></td>
      <td>${badge(entryCodes.has(r.market + r.code), "진입대상", "후보만")}</td></tr>`).join("") + `</tbody>`;
}

function renderEntries() {
  const el = document.getElementById("entryTable");
  const latest = DATA.latest;
  const opened = latest ? latest.openedToday : [];
  const positionsById = Object.fromEntries(DATA.positions.map(p => [p.id, p]));
  if (!opened.length) { el.innerHTML = `<tr><td class="empty">오늘 신규진입 없음(시장국면 보류·동시보유 한도·게이트 미달 등)</td></tr>`; return; }
  el.innerHTML = `<thead><tr><th class="left">시장</th><th class="left">종목</th><th>진입가</th><th>손절가</th>
      <th>수량</th><th>실투입액</th><th>계좌리스크%</th></tr></thead><tbody>` +
    opened.map(id => { const p = positionsById[id]; if (!p) return ""; return `<tr>
      <td class="left">${marketLabel(p.market)}</td><td class="left">${p.name}(${p.code})</td>
      <td>${fmt(p.entry_price)}</td><td>${fmt(p.initial_stop)}</td>
      <td>${fmtInt(p.shares_original)}</td><td>${fmtInt(p.invested_amount)}</td>
      <td>${fmt(p.account_risk_pct, 3)}%</td></tr>`; }).join("") + `</tbody>`;
}

function renderRegime() {
  const el = document.getElementById("regimeCards");
  const regime = DATA.latest ? DATA.latest.regime : null;
  if (!regime) { el.innerHTML = `<div class="empty">데이터 없음</div>`; return; }
  el.innerHTML = ["KR", "US"].map(mk => {
    const r = regime[mk];
    const allow = r ? r.allowNewEntry : null;
    return `<div class="card"><div class="lbl">${marketLabel(mk)} 시장</div>
      <div class="val">${badge(allow, "신규진입 허용", "신규진입 보류", "판정불가")}</div>
      <div class="lbl" style="margin-top:6px">지수 ${fmt(r && r.indexClose)} / 20일선 ${fmt(r && r.indexSma)}</div></div>`;
  }).join("");
}

function renderPositions() {
  const el = document.getElementById("positionsTable");
  const rows = DATA.positions.filter(p => p.status === "OPEN")
    .sort((a, b) => (b.r_multiple_display ?? -999) - (a.r_multiple_display ?? -999));
  if (!rows.length) { el.innerHTML = `<tr><td class="empty">보유 포지션 없음</td></tr>`; return; }
  el.innerHTML = `<thead><tr><th class="left">시장</th><th class="left">종목</th><th>진입일</th><th>진입가</th>
      <th>현재손절가</th><th>잔여수량</th><th>1R</th><th>2R</th><th>트레일링</th><th>보유거래일</th><th>R배수(미실현)</th></tr></thead><tbody>` +
    rows.map(p => `<tr>
      <td class="left">${marketLabel(p.market)}</td><td class="left">${p.name}(${p.code})</td>
      <td>${p.entry_date}</td><td>${fmt(p.entry_price)}</td><td>${fmt(p.current_stop)}</td>
      <td>${fmtInt(p.shares_remaining)}</td>
      <td>${badge(p.r1_hit, "도달", "대기")}</td><td>${badge(p.r2_hit, "도달", "대기")}</td>
      <td>${badge(p.trailing_active, "가동", "미가동")}</td>
      <td>${fmtInt(p.trading_days_held)}</td>
      <td>${fmt(p.r_multiple_display, 2)}R</td>
    </tr>`).join("") + `</tbody>`;
}

function renderHistory() {
  const el = document.getElementById("historyTable");
  const positionsById = Object.fromEntries(DATA.positions.map(p => [p.id, p]));
  const rows = [...DATA.history].sort((a, b) => b.date.localeCompare(a.date));
  if (!rows.length) { el.innerHTML = `<tr><td class="empty">기록 없음</td></tr>`; return; }
  el.innerHTML = `<thead><tr><th>날짜</th><th class="left">시장</th><th class="left">종목</th>
      <th>RS%</th><th>강도점수</th><th>게이트</th><th>진입여부</th><th>사이징</th><th>손절가</th><th>R배수/결과</th></tr></thead><tbody>` +
    rows.map(r => { const p = r.positionId ? positionsById[r.positionId] : null;
      const result = !p ? (r.entered ? "-" : "") :
        (p.status === "OPEN" ? `진행중 ${fmt(p.r_multiple_display, 2)}R` : `종료 ${fmt(p.r_multiple_display, 2)}R`);
      return `<tr><td>${r.date}</td><td class="left">${marketLabel(r.market)}</td>
        <td class="left">${r.name}(${r.code})</td><td>${fmt(r.rsScore, 1)}</td><td>${fmt(r.scoreTotal)}</td>
        <td>${badge(r.gatePass, "통과", "미달")}</td><td>${badge(r.entered, "진입", "미진입")}</td>
        <td>${fmtInt(r.sizingAmount)}</td><td>${fmt(r.stopPrice)}</td><td>${result}</td></tr>`; }).join("") + `</tbody>`;
}

function computeRMultipleDisplay() {
  // r_multiple은 서버(Python)에서 계산해 넣지 않고, 대시보드가 저장된 필드로 근사 표시한다.
  DATA.positions.forEach(p => {
    const r = p.entry_price - p.initial_stop;
    if (r <= 0) { p.r_multiple_display = null; return; }
    if (p.status === "CLOSED") {
      const totalShares = p.exits.reduce((s, e) => s + e.shares, 0);
      const pnl = p.exits.reduce((s, e) => s + (e.price - p.entry_price) * e.shares, 0);
      p.r_multiple_display = totalShares > 0 ? pnl / totalShares / r : null;
    } else {
      p.r_multiple_display = (p.last_close != null) ? (p.last_close - p.entry_price) / r : null;
    }
  });
}

function renderFooter() {
  document.getElementById("footer").innerHTML = `
    <b>설계상 근사·한계 (정확히 읽고 판단에 참고하세요)</b><br>
    · <b>국내 유니버스</b>: FinanceDataReader가 시가총액(Marcap)을 항상 비워서 내려줘(다른 서브시스템에서도 동일하게 확인된 한계) "시총 상위 200" 대신 20일 평균거래대금 상위 200을 씁니다.<br>
    · <b>강도점수 손절폭 대비 변동성 효율(10점)</b>: 목표가·기대수익 모델이 없어 "리스크 상한(5.5%) 대비 실제 리스크가 작을수록 가점"하는 근사치입니다. 실제 손익비가 아닙니다.<br>
    · <b>US 포지션 상한액</b>: 스펙의 상한액 3단계가 원화로만 주어져 있어, config.py의 자리표시자 환율로 달러 환산합니다.<br>
    · <b>섹터·테마 동시보유 제한</b>: 업종 분류 데이터가 없어 이번 구현에서 생략했습니다(총 보유종목 6개 제한만 적용, 국내·미국 합산).<br>
    · <b>자동 페이퍼 진입</b>: "신규진입 대상"은 익일 시가에 실제로 매수했다고 자동 가정하고 이후 손절·+1R/+2R·트레일링·시간손절까지 전부 시스템이 자동 관리합니다. 실제 계좌 체결과 100% 일치하지 않을 수 있습니다.<br>
    · <b>+2R 부분익절 체결가</b>: 실제 시세가 아니라 +2R 목표가(진입가+2R) 자체를 체결가로 가정합니다.<br>
    · <b>손절가</b>: ATR14×2 손절가와 최근 20일 스윙로우 중 진입가에 더 가까운(타이트한) 쪽을 씁니다.<br>
    · 모든 산출물은 판단 참고용이며 실제 주문은 절대 실행하지 않습니다. 모든 임계값은 momentum_signals/config.py에서 조정 가능합니다.
  `;
}

function checkEquityPlaceholder() {
  const eq = DATA.latest && DATA.latest.totalEquity;
  const isPlaceholder = !eq || eq.KR === 10000000 || eq.US === 10000;
  document.getElementById("equityBanner").style.display = isPlaceholder ? "block" : "none";
}

computeRMultipleDisplay();
renderCards();
renderTop5();
renderFinalists();
renderEntries();
renderRegime();
renderPositions();
renderHistory();
renderFooter();
checkEquityPlaceholder();
</script>
</body>
</html>
"""


def build() -> None:
    latest = _load("latest.json", None)
    positions = _load("positions.json", [])
    history = _load("history.json", [])
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    (DOCS_DIR / "index.html").write_text(render_html(latest, positions, history), encoding="utf-8")
    print(f"대시보드 생성 완료: {DOCS_DIR / 'index.html'}")


if __name__ == "__main__":
    build()
