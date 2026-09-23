/* Public evidence only. Personal choices never leave this browser. */
"use strict";
const LIMITS = {holdings: 3, manual: 2};
function cleanChoices(raw, available) {
  const result = {holdings: [], manual: []}, seen = new Set();
  for (const group of Object.keys(LIMITS)) {
    for (const id of Array.isArray(raw?.[group]) ? raw[group] : []) {
      if (typeof id === "string" && available.has(id) && !seen.has(id) && result[group].length < LIMITS[group]) {
        result[group].push(id); seen.add(id);
      }
    }
  }
  return result;
}
function activeIds(automatic, choices) {
  return [...new Set([...automatic.slice(0, 5), ...choices.holdings.slice(0, 3), ...choices.manual.slice(0, 2)])];
}
function addChoice(choices, group, id, automatic, available) {
  if (!Object.hasOwn(LIMITS, group) || !available.has(id)) return "목록에서 정상 데이터가 있는 종목을 선택하세요.";
  if (activeIds(automatic, choices).includes(id)) return "이미 분석 대상에 포함된 종목입니다.";
  if (choices[group].length >= LIMITS[group]) return `이 목록은 최대 ${LIMITS[group]}개입니다. 기존 선택을 제거한 뒤 추가하세요.`;
  choices[group].push(id);
  return null;
}
if (typeof module !== "undefined") module.exports = {cleanChoices, activeIds, addChoice};
if (typeof document !== "undefined") startReview();

async function startReview() {
  const $ = id => document.getElementById(id);
  const KEY = "sepa-review-choices-v1", cache = new Map();
  let selected = null, request = 0, choices, catalog, stocks, automatic;
  const node = (tag, text, cls) => {
    const el = document.createElement(tag);
    if (text != null) el.textContent = text;
    if (cls) el.className = cls;
    return el;
  };
  function save() {
    try { localStorage.setItem(KEY, JSON.stringify(choices)); }
    catch { $("status").textContent = "이 브라우저에서는 선택을 저장할 수 없습니다. 현재 화면에서는 계속 사용할 수 있습니다."; }
  }
  function renderChoices() {
    $("automatic").replaceChildren(); $("personal").replaceChildren();
    for (const id of automatic) $("automatic").append(chip(id, "자동"));
    for (const [group, ids] of Object.entries(choices)) for (const id of ids) {
      const wrap = node("span", null, "personal-chip");
      wrap.append(chip(id, group === "holdings" ? "보유 점검" : "직접 선택"));
      const remove = node("button", "×"); remove.type = "button";
      remove.setAttribute("aria-label", `${stocks.get(id).name} 제거`);
      remove.onclick = () => {
        choices[group] = choices[group].filter(x => x !== id); save(); renderChoices();
        if (!activeIds(automatic, choices).includes(selected)) {
          const next = activeIds(automatic, choices)[0];
          if (next) show(next); else {selected = null; request++; $("detail").replaceChildren(node("p", "분석할 종목을 추가하세요.", "panel"));}
        }
      };
      wrap.append(remove); $("personal").append(wrap);
    }
    $("count").textContent = `${activeIds(automatic, choices).length} / 10`;
  }
  function chip(id, label) {
    const stock = stocks.get(id), button = node("button", `${label} · ${stock.name} (${stock.market}:${stock.code})`);
    button.type = "button"; button.dataset.stock = id;
    button.setAttribute("aria-pressed", String(selected === id));
    button.onclick = () => show(id); return button;
  }
  function findingBlock(parent, label, items, fallback) {
    parent.append(node("p", label, "label"));
    parent.append(node("p", items.slice(0, 2).map(f => f.text).join(" / ") || fallback));
  }
  function renderReview(data) {
    const fragment = document.createDocumentFragment(), intro = node("section", null, "panel summary");
    intro.append(node("h2", `${data.name} · ${data.market}:${data.code}`));
    intro.append(node("p", `가격 자료 ${data.asOf || "기준일 없음"} · 재무 ${data.fundamentalPeriod || "자료 없음"} · 규칙 기반`, "muted"));
    if (data.valuationAsOf) intro.append(node("p", `외부 밸류에이션 ${data.valuationAsOf.slice(0,10)}${data.valuationStale ? " · 갱신 실패, 이전 자료" : ""}`, "muted"));
    const recovery = data.recovery;
    intro.append(node("p", recovery ? `복구 모드: ${recovery.entryPass ? "조건 통과" : "관찰"} · ${recovery.reason || "사유 없음"} (자료 ${recovery.asOf || "날짜 없음"})` : "복구 모드 오늘 상위 5개에 포함되지 않았습니다. 아래 의견은 매수 판정이 아닙니다."));
    if (recovery?.executionPriority) intro.append(node("p", `오늘 실행 ${recovery.executionPriority}순위 · 제안금액 ${Number(recovery.suggestedAmountKRW).toLocaleString("ko-KR")}원. 진입 범위·손절·목표가는 복구 모드 실행 카드를 확인하세요.`));
    const concerns = data.cards.flatMap(c => c.concerns.map(f => ({...f, lens:c.name}))).sort((a,b) => b.severity-a.severity);
    intro.append(node("p", "먼저 확인할 우려", "label"));
    intro.append(node("p", concerns.length ? `${concerns[0].lens}: ${concerns[0].text}` : "현재 자료에서 규칙에 걸린 우려가 없습니다. 데이터 누락과 다음 확인 조건을 함께 확인하세요."));
    const trend = data.cards.find(c => c.id === "trend");
    const other = data.cards.find(c => ["value", "growth"].includes(c.id) && c.concerns.length);
    if (trend?.supports.length && other) {
      intro.append(node("p", "관점이 달라지는 지점", "label"));
      intro.append(node("p", `단기 추세 근거: ${trend.supports[0].text} / ${other.name}의 우려: ${other.concerns[0].text}. 서로 다른 검토 기간의 의견이므로 하나의 찬반 점수로 합치지 않습니다.`));
    }
    intro.append(node("p", data.limits.join(" "), "muted")); fragment.append(intro);
    const grid = node("div", null, "grid");
    for (const card of data.cards) {
      const article = node("article", null, "lens"), top = node("div", null, "lens-top");
      top.append(node("h3", card.name), node("span", card.state, `badge ${card.state === "주의" ? "warning" : ""}`));
      article.append(top, node("p", card.horizon, "muted"));
      findingBlock(article, "근거", card.supports, "확인된 긍정 근거가 부족합니다.");
      findingBlock(article, "우려", card.concerns, "현재 자료에서 확인된 우려 없음 · 안전을 보장하는 의미는 아닙니다.");
      findingBlock(article, "다음 확인 조건", card.checks, "새로운 가격·실적 자료가 들어오면 재검토합니다.");
      if (card.dataGaps.length) article.append(node("p", card.dataGaps.join(" / "), "gaps"));
      const details = node("details"), list = node("ul");
      details.append(node("summary", "전체 근거와 수치 보기"));
      for (const [label, items] of [["근거",card.supports],["우려",card.concerns],["확인",card.checks]]) {
        for (const item of items) list.append(node("li", `${label}: ${item.text}`));
      }
      details.append(list); article.append(details); grid.append(article);
    }
    fragment.append(grid, node("p", data.source, "muted")); $("detail").replaceChildren(fragment);
  }
  async function show(id) {
    if (!stocks.has(id)) return;
    const ticket = ++request; selected = id; renderChoices();
    $("detail").replaceChildren(node("p", "근거를 불러오는 중…", "panel"));
    try {
      if (!cache.has(id)) {
        const stock = stocks.get(id);
        const response = await fetch(`${encodeURIComponent(id)}.json?v=${encodeURIComponent(stock.revision)}`);
        if (!response.ok) throw new Error("load");
        const data = await response.json();
        if (!Array.isArray(data.cards) || data.cards.length !== 6 || data.code !== stock.code || data.market !== stock.market) throw new Error("schema");
        cache.set(id, data);
      }
      if (ticket === request) renderReview(cache.get(id));
    } catch {
      if (ticket !== request) return;
      const error = node("div", null, "panel"); error.append(node("p", "이 종목의 분석 자료를 불러오지 못했습니다. 잠시 후 다시 시도하세요."));
      const retry = node("button", "다시 불러오기"); retry.onclick = () => show(id); error.append(retry); $("detail").replaceChildren(error);
    }
  }
  try {
    const response = await fetch("catalog.json", {cache:"no-cache"}); if (!response.ok) throw new Error("catalog");
    catalog = await response.json();
    stocks = new Map(catalog.stocks.filter(s => /^(kr|us)-[A-Z0-9][A-Z0-9.-]{0,19}$/.test(s.id)).map(s => [s.id,s]));
    automatic = [...new Set(catalog.automatic)].filter(id => stocks.has(id)).slice(0,5);
    let raw = {}; try {raw = JSON.parse(localStorage.getItem(KEY) || "{}");} catch {$("status").textContent = "저장된 선택을 읽을 수 없어 빈 목록으로 시작합니다.";}
    choices = cleanChoices(raw, stocks);
    $("dates").textContent = `복구 모드 기준일 · KR ${catalog.sessions.kr || "없음"} · US ${catalog.sessions.us || "없음"}`;
    for (const stock of stocks.values()) {
      const option = node("option"); option.value = `${stock.market}:${stock.code}`; option.label = stock.name; $("stock-list").append(option);
    }
    if (catalog.dataGaps.length) $("status").textContent = catalog.dataGaps.join(" / ");
    $("add-form").onsubmit = event => {
      event.preventDefault(); const value = $("stock").value.trim().toUpperCase();
      const matches = [...stocks.values()].filter(s => `${s.market}:${s.code}` === value || s.code.toUpperCase() === value || s.name.toUpperCase() === value);
      if (matches.length !== 1) {$("status").textContent = "종목을 정확히 선택하세요. 같은 코드가 있으면 KR: 또는 US:를 붙여주세요."; return;}
      const id = matches[0].id, error = addChoice(choices, $("group").value, id, automatic, stocks);
      $("status").textContent = error || "선택 목록에 추가했습니다.";
      if (!error) {save(); $("stock").value = ""; show(id);}
    };
    $("clear").onclick = () => {choices = {holdings:[],manual:[]}; save(); renderChoices(); if(automatic.length) show(automatic[0]); else {selected=null;request++;$("detail").replaceChildren(node("p","분석할 종목을 추가하세요.","panel"));}};
    renderChoices(); const first = activeIds(automatic, choices)[0]; if (first) await show(first);
  } catch {$("status").textContent = "분석 목록을 불러오지 못했습니다. 새로고침해 다시 시도하세요."; $("add-form").querySelector("button").disabled = true;}
}
