/* macro-card.js — macro.json 을 읽어 매크로 카드를 그림
 *
 * 사용법 (기존 SEPA 대시보드 index.html 에 두 줄 추가):
 *   <div id="macro-card" data-src="macro.json"></div>
 *   <script src="macro-card.js"></script>
 *
 * data-src 는 macro.json 경로. 생략하면 같은 폴더의 macro.json.
 * 국면 태그(regime)는 window.MACRO_REGIME 에도 넣어두므로,
 * 스크리너 테이블에서 리스크오프일 때 "관망" 배지를 붙이는 데 쓸 수 있음.
 */
(function () {
  const css = `
  .mc{--mc-surface:#fcfcfb;--mc-plane:#f4f4f2;--mc-ink:#0b0b0b;--mc-ink2:#52514e;--mc-ink3:#8a8985;--mc-line:#e6e5e1;
      --mc-series:#2a78d6;--mc-good:#0ca30c;--mc-warn:#fab219;--mc-crit:#d03b3b;
      font:14px/1.45 -apple-system,"Pretendard","Noto Sans KR",system-ui,sans-serif;color:var(--mc-ink);
      background:var(--mc-surface);border:1px solid var(--mc-line);border-radius:12px;padding:18px 20px;max-width:1100px}
  @media (prefers-color-scheme:dark){.mc{--mc-surface:#1a1a19;--mc-plane:#242422;--mc-ink:#fff;--mc-ink2:#c3c2b7;--mc-ink3:#8f8e88;--mc-line:#333330;--mc-series:#3987e5}}
  .mc *{box-sizing:border-box}
  .mc-head{display:flex;flex-wrap:wrap;align-items:center;gap:10px 16px;margin-bottom:12px}
  .mc-title{font-weight:700;font-size:16px;letter-spacing:-.01em}
  .mc-ts{color:var(--mc-ink3);font-size:12px;font-variant-numeric:tabular-nums}
  .mc-badge{display:inline-flex;align-items:center;gap:6px;padding:4px 10px;border-radius:999px;font-weight:600;font-size:13px;color:var(--mc-ink);background:var(--mc-plane);border:1px solid var(--mc-line)}
  .mc-badge .dot{width:9px;height:9px;border-radius:50%;background:var(--c)}
  .mc-score{color:var(--mc-ink2);font-weight:500;font-variant-numeric:tabular-nums}
  .mc-sepa{font-size:13px;color:var(--mc-ink2);padding:8px 12px;background:var(--mc-plane);border-radius:8px;margin-bottom:12px;border-left:3px solid var(--c)}
  .mc-summary{font-size:14px;line-height:1.6;margin:0 0 14px;padding:0 2px}
  .mc-signals{margin:0 0 16px;padding:0;list-style:none;display:grid;gap:6px}
  .mc-signals li{display:grid;grid-template-columns:52px 1fr;gap:10px;font-size:13px;line-height:1.4}
  .mc-signals .rid{color:var(--mc-ink3);font-variant-numeric:tabular-nums;font-size:12px;padding-top:1px}
  .mc-signals .sc{display:inline-block;min-width:22px;color:var(--mc-ink2);margin-right:6px;font-variant-numeric:tabular-nums}
  .mc-none{color:var(--mc-ink3);font-size:13px;margin-bottom:14px}
  .mc-group{margin-top:10px}
  .mc-group h4{margin:0 0 6px;font-size:12px;font-weight:600;color:var(--mc-ink3);letter-spacing:.04em;text-transform:uppercase}
  .mc-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:8px}
  .mc-tile{background:var(--mc-plane);border-radius:8px;padding:9px 11px;display:grid;grid-template-columns:minmax(0,1fr) 64px;grid-template-rows:auto auto auto auto;gap:3px 10px;align-items:center}
  .mc-tile .nm{font-size:12px;color:var(--mc-ink2);grid-column:1/3;grid-row:1}
  .mc-tile .v{grid-column:1;grid-row:2;font-size:17px;font-weight:600;font-variant-numeric:tabular-nums;letter-spacing:-.01em}
  .mc-tile .v small{font-size:11px;font-weight:400;color:var(--mc-ink3);margin-left:2px}
  .mc-tile svg{grid-column:2;grid-row:2/4;width:64px;height:26px;overflow:visible;justify-self:end}
  .mc-tile .d{grid-column:1;grid-row:3;font-size:11.5px;color:var(--mc-ink2);font-variant-numeric:tabular-nums;line-height:1.35}
  .mc-tile .d b{font-weight:500;color:var(--mc-ink);white-space:nowrap}
  .mc-tile .pct{grid-column:1/3;grid-row:4;font-size:11px;color:var(--mc-ink3)}
  .mc-foot{margin-top:14px;font-size:11.5px;color:var(--mc-ink3)}
  .mc-toggle{background:none;border:1px solid var(--mc-line);border-radius:6px;color:var(--mc-ink2);font-size:12px;padding:3px 9px;cursor:pointer;margin-left:auto}
  .mc-table{width:100%;border-collapse:collapse;font-size:12.5px;font-variant-numeric:tabular-nums;margin-top:8px}
  .mc-table th,.mc-table td{text-align:right;padding:5px 8px;border-bottom:1px solid var(--mc-line)}
  .mc-table th:first-child,.mc-table td:first-child{text-align:left}
  .mc-table th{color:var(--mc-ink3);font-weight:500}
  `;

  const REGIME = {
    risk_on: { c: "var(--mc-good)", icon: "▲" },
    neutral: { c: "var(--mc-warn)", icon: "●" },
    risk_off: { c: "var(--mc-crit)", icon: "▼" },
  };

  const fmtV = (d) => {
    const v = d.value;
    if (d.kind === "flow") return (d.sum_5d / 10000).toFixed(2) + "<small>조(5일)</small>";
    if (Math.abs(v) >= 1000) return Math.round(v).toLocaleString() + unit(d);
    if (Math.abs(v) >= 100) return v.toFixed(1) + unit(d);
    return v.toFixed(2) + unit(d);
  };
  const unit = (d) => (d.unit ? `<small>${d.unit}</small>` : "");
  const fmtChg = (d, k) => {
    const c = d[k];
    if (c == null) return "—";
    const isBp = d.kind === "rate" || d.kind === "rate_bp" || d.id === "HY_OAS";
    const s = (c > 0 ? "+" : "") + (isBp ? c.toFixed(0) + "bp" : c.toFixed(1) + "%");
    return s;
  };

  function spark(vals) {
    if (!vals || vals.length < 2) return "";
    const w = 64, h = 26, min = Math.min(...vals), max = Math.max(...vals), r = max - min || 1;
    const pts = vals.map((v, i) => [(i / (vals.length - 1)) * w, h - ((v - min) / r) * (h - 4) - 2]);
    const d = pts.map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" ");
    const last = pts[pts.length - 1];
    return `<svg viewBox="0 0 ${w} ${h}" aria-hidden="true"><path d="${d}" fill="none" stroke="var(--mc-series)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/><circle cx="${last[0]}" cy="${last[1]}" r="3" fill="var(--mc-series)" stroke="var(--mc-plane)" stroke-width="2"/></svg>`;
  }

  function tile(d) {
    const pct = d.pct_1y != null ? `1년 백분위 ${d.pct_1y.toFixed(0)}` : "";
    const chg = d.kind === "flow"
      ? `<span class="d">당일 <b>${(d.value / 10000).toFixed(2)}조</b></span>`
      : `<span class="d">1주 <b>${fmtChg(d, "chg_1w")}</b> · 1개월 <b>${fmtChg(d, "chg_1m")}</b></span>`;
    return `<div class="mc-tile" title="${d.name} ${d.date}">
      <div class="nm">${d.name}</div>
      <div class="v">${fmtV(d)}</div>
      ${spark(d.sparkline)}
      ${chg}
      <span class="pct">${pct}</span>
    </div>`;
  }

  function table(inds) {
    const rows = Object.values(inds).map((d) =>
      `<tr><td>${d.name}</td><td>${d.kind === "flow" ? (d.value / 10000).toFixed(2) + "조" : d.value.toLocaleString(undefined, { maximumFractionDigits: 3 })}</td><td>${fmtChg(d, "chg_1d")}</td><td>${fmtChg(d, "chg_1w")}</td><td>${fmtChg(d, "chg_1m")}</td><td>${d.pct_1y ?? "—"}</td><td>${d.date}</td></tr>`).join("");
    return `<table class="mc-table"><thead><tr><th>지표</th><th>현재</th><th>1일</th><th>1주</th><th>1개월</th><th>1년 백분위</th><th>기준일</th></tr></thead><tbody>${rows}</tbody></table>`;
  }

  function render(el, data) {
    const R = REGIME[data.regime] || REGIME.neutral;
    window.MACRO_REGIME = data.regime;
    window.MACRO_DATA = data;
    const signals = data.signals.length
      ? `<ul class="mc-signals">${data.signals.map((s) => `<li><span class="rid">${s.id}</span><span><span class="sc">${s.score > 0 ? "+" + s.score : s.score}</span>${s.message}</span></li>`).join("")}</ul>`
      : `<div class="mc-none">특이 신호 없음 — 모든 지표가 규칙 임계값 안쪽.</div>`;
    const groups = Object.entries(data.groups).map(([g, ids]) => {
      const tiles = ids.filter((i) => data.indicators[i]).map((i) => tile(data.indicators[i])).join("");
      return tiles ? `<div class="mc-group"><h4>${g}</h4><div class="mc-grid">${tiles}</div></div>` : "";
    }).join("");
    el.innerHTML = `<style>${css}</style><div class="mc" style="--c:${R.c}">
      <div class="mc-head">
        <span class="mc-title">매크로 브리핑</span>
        <span class="mc-badge" style="--c:${R.c}"><span class="dot"></span>${R.icon} ${data.regime_label} <span class="mc-score">(${data.score > 0 ? "+" : ""}${data.score})</span></span>
        <span class="mc-ts">${data.generated_at}${data.mock ? " · MOCK " + data.mock : ""}</span>
        <button class="mc-toggle" type="button">표로 보기</button>
      </div>
      <div class="mc-sepa">${data.sepa_note}</div>
      ${data.summary ? `<p class="mc-summary">${data.summary.replace(/\n/g, "<br>")}</p>` : ""}
      ${signals}
      <div class="mc-body">${groups}</div>
      <div class="mc-foot">변화: 금리류 bp, 그 외 %. 1년 백분위 = 현재값이 최근 1년 값 중 몇 %보다 높은지. 규칙은 rules.yaml 참조. 투자 판단의 참고 자료이며 매수·매도 신호가 아님.</div>
    </div>`;
    const body = el.querySelector(".mc-body"), btn = el.querySelector(".mc-toggle");
    let asTable = false;
    btn.addEventListener("click", () => {
      asTable = !asTable;
      body.innerHTML = asTable ? table(data.indicators) : groups;
      btn.textContent = asTable ? "카드로 보기" : "표로 보기";
    });
  }

  function init() {
    document.querySelectorAll("#macro-card,[data-macro-card]").forEach((el) => {
      const src = el.dataset.src || "macro.json";
      fetch(src + (src.includes("?") ? "&" : "?") + "t=" + Date.now())
        .then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); })
        .then((d) => render(el, d))
        .catch((e) => { el.innerHTML = `<style>${css}</style><div class="mc"><span class="mc-title">매크로 브리핑</span> <span class="mc-ts">macro.json 로드 실패 (${e.message})</span></div>`; });
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
