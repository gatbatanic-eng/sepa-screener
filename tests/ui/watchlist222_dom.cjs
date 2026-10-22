const fs=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');
const {JSDOM}=require('jsdom');
const rootPath=path.resolve(__dirname,'../..');
const html=fs.readFileSync(path.join(rootPath,'docs/market/index.html'),'utf8');
const component=fs.readFileSync(path.join(rootPath,'docs/market/watchlist222.js'),'utf8');
const stock=code=>({code,name:'종목'+code,sector:'검증',changePct:5,turnoverKRW:2e10,turnoverVs20d:2,providerNewsCount:5,score:50,sourceUrl:'https://example.com',evidence:[]});
const a=stock('A'),b=stock('B'),c=stock('C'),d=stock('D');
const fixture=date=>({date,session:'검증',status:'ok',generatedAt:'2026-10-22T07:00:00Z',coverage:{valid:700,expected:700},sectors:[],moneyRanking:[],attentionRanking:[],method:{},watchlist222:{selectedCount:2,purpose:'검토용',coverage:{evaluated:700,marketCollected:700,scope:'full_universe'},groups:[{id:'market',title:'주도',items:[],missingReason:'미확인'},{id:'domestic',title:'국내',items:[],missingReason:'미확인'},{id:'individual',title:'개별',items:[a,b],missingReason:'미확인'}],reviewPool:[a,b,c,d],limitations:[],rules:{score:'검증'}}});
function setup(){const dom=new JSDOM(html,{url:'https://gatbatanic-eng.github.io/sepa-screener/market/',runScripts:'outside-only'});dom.window.eval(component);dom.window.document.dispatchEvent(new dom.window.Event('DOMContentLoaded'));return dom;}
function pick(w,group,slot,code){const el=w.document.querySelector(`select[data-group="${group}"][data-index="${slot}"]`);assert(el);el.value=code;el.dispatchEvent(new w.Event('change',{bubbles:true}));}
function change(w,selector,value){const el=w.document.querySelector(selector);assert(el,selector);el.value=value;el.dispatchEvent(new w.Event('change',{bubbles:true}));}
async function tick(){await new Promise(r=>setImmediate(r));}

(async()=>{
  const dom=setup(),w=dom.window;
  const r=fixture('20261022');w.renderWatchlist222(r);
  pick(w,'market',0,'A');
  assert(w.document.querySelector('#watch222').textContent.includes('중복 선정할 수 없습니다'));
  pick(w,'market',0,'C');
  assert.equal(JSON.parse(w.localStorage.getItem('golden222:20261022')).groups.market[0],'C');
  w.document.querySelector('details[data-detail-code="C"]').open=true;
  for(const [field,value]of[['entry','100'],['stop','95'],['target','110']]){
    change(w,`input[data-code="C"][data-field="${field}"]`,value);
    assert(w.document.querySelector('details[data-detail-code="C"]').open,'price editor must stay open across changes');
  }
  assert(w.document.querySelector('#watch222').textContent.includes('계획 손익비 2.00 : 1'));
  change(w,'textarea[data-code="C"]','<img src=x onerror=alert(1)>');
  assert.equal(w.document.querySelector('#watch222 img'),null);
  assert.equal(w.document.querySelector('textarea[data-code="C"]').value,'<img src=x onerror=alert(1)>');
  w.document.querySelector('input[data-action="confirm"][data-code="C"]').click();
  assert.equal(JSON.parse(w.localStorage.getItem('golden222:20261022')).confirmed.C,true);
  w.document.querySelector('button[data-action="zero"][data-code="C"]').click();
  assert(w.document.querySelector('.zero-banner').textContent.includes('종목C'));
  const refreshed=fixture('20261022');refreshed.watchlist222.groups[2].items=[c,d];w.renderWatchlist222(refreshed);
  const selected=[...w.document.querySelectorAll('#watch222 select')].map(el=>el.value).filter(Boolean);
  assert.equal(new Set(selected).size,selected.length,'refreshed automatic picks cannot duplicate manual choices');
  assert.equal(w.document.querySelector('select[data-group="market"][data-index="0"]').value,'C');
  w.localStorage.setItem('golden222:20261023',JSON.stringify({groups:{market:'broken',domestic:{bad:1}},notes:42,plans:{A:'bad'},confirmed:[],zero:{bad:1}}));
  assert.doesNotThrow(()=>w.renderWatchlist222(fixture('20261023')));
  Object.defineProperty(w.Storage.prototype,'setItem',{configurable:true,value(){throw Error('storage blocked')}});
  pick(w,'market',0,'C');
  change(w,'textarea[data-code="C"]','저장 차단 시에도 유지');
  assert.equal(w.document.querySelector('textarea[data-code="C"]').value,'저장 차단 시에도 유지');
  assert(w.document.querySelector('#watch222').textContent.includes('임시 메모리'));
  dom.window.close();
  console.log('DOM interactions: choices, duplicates, editable plans, notes, confirmation, priority, malformed storage and blocked storage OK');

  const races=setup(),v=races.window,requests=[];
  v.fetch=url=>new Promise(resolve=>requests.push({url,resolve}));
  const inline=[...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].at(-1)[1];
  v.eval(inline);
  const respond=(item,data)=>item.resolve({ok:true,json:async()=>data});
  respond(requests.shift(),['20261022','20261021']);await tick();
  respond(requests.shift(),fixture('20261022'));await tick();
  const dates=v.document.querySelector('#dates');
  dates.value='20261021';dates.dispatchEvent(new v.Event('change'));
  assert.equal(v.document.querySelectorAll('#watch222 select').length,0,'old-date controls must disappear immediately while the new report loads');
  dates.value='20261022';dates.dispatchEvent(new v.Event('change'));
  const earlier=requests.shift(),later=requests.shift();
  respond(later,fixture('20261022'));await tick();
  respond(earlier,fixture('20261021'));await tick();
  assert(v.document.querySelector('#status').textContent.includes('20261022'));
  assert(!v.document.querySelector('#status').textContent.includes('20261021'));
  dates.value='20261021';dates.dispatchEvent(new v.Event('change'));
  respond(requests.shift(),fixture('20261022'));await tick();
  assert(v.document.querySelector('#status').textContent.includes('기준일이 다릅니다'));
  assert.equal(v.document.querySelectorAll('#watch222 select').length,0);
  races.window.close();
  console.log('DOM report loading: out-of-order responses ignored and mismatched date rejected OK');
})().catch(error=>{console.error(error);process.exitCode=1});
