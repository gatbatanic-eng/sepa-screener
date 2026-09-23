const test = require('node:test');
const assert = require('node:assert/strict');
const {cleanChoices, activeIds, addChoice} = require('../personas/review.js');
const ids = Array.from({length:20},(_,i)=>`us-${i}`), available = new Set(ids);
test('restore validates type, removes unavailable/duplicate IDs and enforces both limits',()=>{
  assert.deepEqual(cleanChoices({holdings:[null,'missing',...ids],manual:['us-0',...ids]},available),
    {holdings:ids.slice(0,3),manual:ids.slice(3,5)});
  assert.deepEqual(cleanChoices(null,available),{holdings:[],manual:[]});
  assert.deepEqual(cleanChoices({holdings:'us-0',manual:{}},available),{holdings:[],manual:[]});
});
test('five automatic, three holdings and two manual produce exactly ten unique active stocks',()=>{
  const choices={holdings:[],manual:[]}, automatic=ids.slice(0,5);
  for(const i of [5,6,7]) assert.equal(addChoice(choices,'holdings',ids[i],automatic,available),null);
  for(const i of [8,9]) assert.equal(addChoice(choices,'manual',ids[i],automatic,available),null);
  assert.equal(activeIds(automatic,choices).length,10);
  assert.match(addChoice(choices,'holdings',ids[10],automatic,available),/최대 3/);
  assert.match(addChoice(choices,'manual',ids[10],automatic,available),/최대 2/);
  assert.equal(activeIds(automatic,choices).length,10);
});
test('duplicates, unknown stocks and invalid groups never change choices',()=>{
  const choices={holdings:['us-5'],manual:[]}, automatic=ids.slice(0,5), before=JSON.stringify(choices);
  for(const id of ['us-0','us-5','invalid']) assert.ok(addChoice(choices,'manual',id,automatic,available));
  assert.ok(addChoice(choices,'__proto__','us-6',automatic,available));
  assert.equal(JSON.stringify(choices),before);
  assert.equal(activeIds(['us-5','us-5'],choices).length,1);
});
