/* Local review drafts contain no orders and are never posted to the public repo. */
let active222 = null;
const e222 = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const n222 = n => n == null ? '미확인' : Number(n).toLocaleString('ko-KR', {maximumFractionDigits: 2});
const memory222 = new Map();
const object222 = value => value && typeof value==='object' && !Array.isArray(value) ? value : {};
function cleanDraft222(value) {
  const x=object222(value), groups={};
  for(const [key,codes] of Object.entries(object222(x.groups))) {
    if(['market','domestic','individual'].includes(key) && Array.isArray(codes))
      groups[key]=codes.slice(0,2).map(c=>typeof c==='string'?c:'');
  }
  const notes=Object.fromEntries(Object.entries(object222(x.notes)).filter(([,v])=>typeof v==='string'));
  const plans=Object.fromEntries(Object.entries(object222(x.plans)).map(([code,plan])=>[code,Object.fromEntries(Object.entries(object222(plan)).filter(([k,v])=>['entry','stop','target'].includes(k)&&['string','number'].includes(typeof v)))]));
  const confirmed=Object.fromEntries(Object.entries(object222(x.confirmed)).filter(([,v])=>typeof v==='boolean'));
  return {groups,notes,plans,confirmed,zero:typeof x.zero==='string'?x.zero:null};
}
function read222(date) {
  if(memory222.has(date))return cleanDraft222(memory222.get(date));
  try { return cleanDraft222(JSON.parse(localStorage.getItem('golden222:'+date)||'{}')); }
  catch { return cleanDraft222({}); }
}
function save222(date, draft) {
  const clean=cleanDraft222(draft);
  memory222.set(date,clean);
  try { localStorage.setItem('golden222:'+date,JSON.stringify(clean)); return ''; }
  catch { return '브라우저 저장이 차단되어 현재 화면의 임시 메모리로 유지합니다. 새로고침·종료 전에 따로 기록하세요.'; }
}
function choices222(w,draft,pool) {
  const reserved=new Set(), picks={};
  // Explicit choices take precedence over refreshed automatic recommendations.
  for(const g of w.groups)if(Array.isArray(draft.groups[g.id])) {
    picks[g.id]=draft.groups[g.id].slice(0,2).map(c=>{
      if(!pool.has(c)||reserved.has(c))return '';
      reserved.add(c);return c;
    });
  }
  return w.groups.map(g=>{
    if(!Object.prototype.hasOwnProperty.call(picks,g.id))picks[g.id]=g.items.map(s=>s.code).filter(c=>{
      if(!pool.has(c)||reserved.has(c))return false;
      reserved.add(c);return true;
    }).slice(0,2);
    return {...g,codes:picks[g.id]};
  });
}
function renderWatchlist222(report, message='') {
  const root = document.querySelector('#watch222');
  const w = report.watchlist222;
  if (!w || !Array.isArray(w.groups)) { root.innerHTML = '<h2>2-2-2 관심종목 준비</h2><p>이 날짜의 2-2-2 선별 자료가 아직 없습니다. 새 보고서에서는 자동 생성합니다.</p>'; return; }
  const opened = [...root.querySelectorAll('details[open][data-detail-code]')].map(d=>d.dataset.detailCode);
  const draft = read222(report.date);
  const pool = new Map([...(w.reviewPool||[]), ...w.groups.flatMap(g=>g.items)].map(s=>[s.code,s]));
  const groups = choices222(w,draft,pool);
  const chosen = groups.flatMap(g=>g.codes).map(c=>pool.get(c)).filter(Boolean);
  const best = pool.get(draft.zero) && chosen.some(s=>s.code===draft.zero) ? pool.get(draft.zero) : [...chosen].sort((a,b)=>b.score-a.score)[0];
  active222 = {report, draft, groups, pool};
  function card(g, index) {
    const code = g.codes[index] || '', s = pool.get(code), auto = g.items.find(x=>x.code===code);
    const options = [...pool.values()].map(p=>'<option value="'+e222(p.code)+'"'+(p.code===code?' selected':'')+'>'+e222(p.name)+' · '+n222(p.changePct)+'% · '+e222(p.sector)+'</option>').join('');
    let content = '<label class="pick-label">'+(index+1)+'번 종목<select data-action="pick" data-group="'+g.id+'" data-index="'+index+'" aria-label="'+e222(g.title)+' '+(index+1)+'번 종목"><option value="">선정 보류</option>'+options+'</select></label>';
    if (!s) return '<div class="pick-card">'+content+'<p class="dim">'+e222(g.missingReason)+'</p></div>';
    const plan = draft.plans[code] || {};
    const entry = Number(plan.entry), stop = Number(plan.stop), target = Number(plan.target);
    const valid = [entry,stop,target].every(Number.isFinite) && stop > 0 && entry > stop && target > entry;
    const rr = valid ? ((target-entry)/(entry-stop)).toFixed(2) : null;
    const evidence = s.evidence || [];
    content += '<p><a href="'+e222(s.sourceUrl)+'" target="_blank" rel="noopener noreferrer"><b>'+e222(s.name)+'</b></a> · 관찰점수 '+n222(s.score)+'</p><p class="pick-metrics">등락 '+n222(s.changePct)+'% · 거래대금 '+n222(s.turnoverKRW/1e8)+'억<br>20일 평균 대비 '+n222(s.turnoverVs20d)+'배 · 뉴스 '+n222(s.providerNewsCount)+'건</p>';
    content += '<p>'+e222(auto ? auto.reason : '직접 선택한 검토 후보 · 자동 선정 조건 충족 여부와 이 범주의 섹터·재료·국내 고유성을 직접 확인하세요.')+'</p>';
    if (auto?.risks?.length) content += '<p class="up">보도에서 확인한 주의 표현: '+e222(auto.risks.join(', '))+'</p>';
    content += evidence.length ? '<ul class="pick-news">'+evidence.slice(0,2).map(a=>'<li><a href="'+e222(a.url)+'" target="_blank" rel="noopener noreferrer">'+e222(a.title)+'</a></li>').join('')+'</ul>' : '<p class="dim">당일 재료 근거 미확인</p>';
    content += '<details data-detail-code="'+e222(code)+'"><summary>가격대·진입 시나리오 기록</summary><p class="dim">기준일 종가 '+n222(s.close)+' / 고가 '+n222(s.high)+' / 저가 '+n222(s.low)+'<br>직전 250봉 고가 '+n222(s.high250)+'<br>다음 장 고가 돌파·유지 또는 눌림 지지와 거래대금 재유입을 확인합니다. 저가 이탈은 시나리오 재검토 기준입니다.</p><div class="plan-grid">'+[['entry','진입가'],['stop','손절가'],['target','목표가']].map(([key,label])=>'<label>'+label+'<input type="number" min="0" step="any" inputmode="decimal" data-action="plan" data-code="'+e222(code)+'" data-field="'+key+'" value="'+e222(plan[key]||'')+'" aria-label="'+e222(s.name)+' '+label+'"></label>').join('')+'</div><p>'+(rr?'계획 손익비 '+rr+' : 1 · 손절폭 '+((entry-stop)/entry*100).toFixed(2)+'% (비용·갭 미반영)':'진입가 > 손절가 > 0, 목표가 > 진입가로 입력하면 손익비를 계산합니다.')+'</p></details>';
    content += '<label class="pick-label">검토 메모<textarea rows="2" data-action="note" data-code="'+e222(code)+'" placeholder="재료 연속성·후속 일정·관망 조건">'+e222(draft.notes[code]||'')+'</textarea></label><div class="pick-actions"><label><input type="checkbox" data-action="confirm" data-code="'+e222(code)+'"'+(draft.confirmed[code]?' checked':'')+'> 관심종목 확정</label><button type="button" data-action="zero" data-code="'+e222(code)+'">0순위 지정</button></div>';
    return '<div class="pick-card">'+content+'</div>';
  }
  root.innerHTML = '<div class="watch-head"><div><p class="tag">2 · 2 · 2 WATCHLIST</p><h2>다음 장, 여섯 종목만 집중해서 보기</h2></div><b>자동 '+w.selectedCount+'/6 · 현재 선택 '+chosen.length+'/6</b></div><p class="dim">기준일 '+e222(report.date)+' · '+e222(w.purpose)+'<br>가격 집계 '+w.coverage.marketCollected+'종목 / 후보 평가 '+w.coverage.evaluated+'종목 ('+(w.coverage.scope==='full_universe'?'전체 수집 종목':'기존 저장분의 업종 대표 종목만')+')</p><div class="zero-banner"><b>0순위 관찰 후보: '+e222(best ? best.name : '선정 보류')+'</b><br><small>선택한 후보 중 관찰 점수 최상위 또는 직접 지정. 매수·진입 지시가 아닙니다.</small></div>'+(message?'<p role="status" class="up">'+e222(message)+'</p>':'')+'<div class="watch-grid">'+groups.map(g=>'<section class="watch-group"><h3>'+e222(g.title)+' <span class="dim">2</span></h3>'+[0,1].map(i=>card(g,i)).join('')+'</section>').join('')+'</div><details data-detail-code="rules"><summary>선정 기준과 아직 직접 확인할 항목</summary><p>거래대금 100억원 이상 · 상승률 2% 이상 · 거래대금 20일 평균의 1.5배 이상. 초기 연구 기준이며 수익성 검증 결과가 아닙니다. 직접 교체용 검토 후보는 거래대금 50억원 이상인 상승 종목까지 넓혀 보여줍니다.</p><p>'+e222(w.rules.score)+'</p><p>시장 주도 섹터: 3종목 이상 업종, 평균 상승, 상승 종목 비율 50% 이상에서 보도 재료를 확인합니다. 국내 이슈는 한국 기관·정책 맥락과 관련 2종목 강세를 확인합니다. 같은 종목을 여러 범주에 넣지 않습니다.</p><ul>'+w.limitations.map(x=>'<li>'+e222(x)+'</li>').join('')+'</ul></details><p class="dim">종목 교체·확정·메모·가격 계획은 이 브라우저에만 저장됩니다. 기기 간 동기화는 지원하지 않습니다.</p>';
  root.querySelectorAll('details[data-detail-code]').forEach(d=>{if(opened.includes(d.dataset.detailCode))d.open=true});
}
function event222(event) {
  const target = event.target, action = target.dataset.action;
  if (!active222 || !action) return;
  const {report,draft,groups,pool} = active222;
  let message = '';
  if (action==='pick') {
    const group = target.dataset.group, index = Number(target.dataset.index), code=target.value;
    const duplicate = groups.some(g=>g.codes.some((c,i)=>c===code && code && (g.id!==group||i!==index)));
    if (duplicate) { renderWatchlist222(report,'같은 종목을 두 자리에 중복 선정할 수 없습니다.'); return; }
    const codes=[...(groups.find(g=>g.id===group)?.codes||[])]; codes[index]=code;
    draft.groups[group]=codes;
  } else if (action==='note') draft.notes[target.dataset.code]=target.value;
  else if (action==='plan') {
    const code=target.dataset.code;
    draft.plans[code]={...(draft.plans[code]||{}),[target.dataset.field]:target.value};
  } else if (action==='confirm') draft.confirmed[target.dataset.code]=target.checked;
  else if (action==='zero' && pool.has(target.dataset.code)) draft.zero=target.dataset.code;
  message=save222(report.date,draft);
  renderWatchlist222(report,message);
}
document.addEventListener('DOMContentLoaded',()=>{
  const root=document.querySelector('#watch222');
  if(!root || root.dataset.events222Bound)return;
  root.dataset.events222Bound='true';
  root.addEventListener('change',event222);
  root.addEventListener('click',e=>{if(e.target.dataset.action==='zero')event222(e)});
});
