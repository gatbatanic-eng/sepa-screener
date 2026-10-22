/* Execute the deployed page's actual HTML/JS/data in a DOM test environment.
   This verifies DOM interactions, not browser-engine rendering or pixel layout. */
const assert=require('node:assert/strict');
const {JSDOM,VirtualConsole}=require('jsdom');
const base='https://gatbatanic-eng.github.io/sepa-screener/market/';
async function get(url){const r=await fetch(url,{signal:AbortSignal.timeout(20000)});assert(r.ok,`${url}: HTTP ${r.status}`);return r;}
(async()=>{
  const html=await (await get(base)).text();
  const errors=[],console=new VirtualConsole();console.on('jsdomError',e=>errors.push(e.message));
  const dom=new JSDOM(html,{url:base,runScripts:'outside-only',virtualConsole:console});
  const w=dom.window;
  const source=w.document.querySelector('script[src*="watchlist222.js"]').getAttribute('src');
  const script=await(await get(new URL(source,base))).text();
  assert(script.includes('function choices222'),'deployed fix is not yet available');
  w.eval(script);w.document.dispatchEvent(new w.Event('DOMContentLoaded'));
  let report;
  w.fetch=async(url)=>{
    const res=await fetch(new URL(url,base),{signal:AbortSignal.timeout(20000)});
    return {ok:res.ok,json:async()=>{const data=await res.json();if(data.watchlist222)report=data;return data;}};
  };
  w.eval([...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].at(-1)[1]);
  for(let i=0;i<300 && !w.document.querySelector('#watch222 select');i++)await new Promise(r=>setTimeout(r,100));
  assert(report,'published data did not load');
  assert.equal(w.document.querySelectorAll('.watch-group').length,3);
  assert.equal(w.document.querySelectorAll('#watch222 select').length,6);
  const selected=[...w.document.querySelectorAll('#watch222 select')].map(x=>x.value).filter(Boolean);
  assert.equal(selected.length,new Set(selected).size);
  const stock=report.watchlist222.reviewPool.find(s=>!selected.includes(s.code));
  assert(stock,'need one replacement candidate for interaction test');
  const select=w.document.querySelector('#watch222 select');select.value=stock.code;select.dispatchEvent(new w.Event('change',{bubbles:true}));
  const details=w.document.querySelector(`details[data-detail-code="${stock.code}"]`);details.open=true;
  for(const [field,value]of[['entry','100'],['stop','95'],['target','110']]){
    const el=w.document.querySelector(`input[data-code="${stock.code}"][data-field="${field}"]`);assert(el);el.value=value;el.dispatchEvent(new w.Event('change',{bubbles:true}));
  }
  assert(w.document.querySelector('#watch222').textContent.includes('계획 손익비 2.00 : 1'));
  w.document.querySelector(`input[data-action="confirm"][data-code="${stock.code}"]`).click();
  w.document.querySelector(`button[data-action="zero"][data-code="${stock.code}"]`).click();
  const draft=JSON.parse(w.localStorage.getItem('golden222:'+report.date));
  assert(draft.confirmed[stock.code]);assert.equal(draft.zero,stock.code);
  assert.equal(errors.length,0,errors.join('\n'));
  global.console.log(JSON.stringify({status:'ok',date:report.date,coverage:report.watchlist222.coverage,autoSelected:report.watchlist222.selectedCount,reviewCandidates:report.watchlist222.reviewPool.length,groups:report.watchlist222.groups.map(g=>({title:g.title,names:g.items.map(x=>x.name)})),checks:['live HTML/JS/JSON','six selectors','unique picks','replacement','price entry','risk/reward','confirmation click','priority click','storage','no DOM runtime errors']},null,2));
  dom.window.close();
})().catch(e=>{console.error(e);process.exitCode=1});
