const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const code = fs.readFileSync('web/live/live.js','utf8');
const ID = '11111111-2222-4333-8444-555555555555';
function store() {
  const values = new Map();
  return {values, get length(){return values.size}, key(i){return [...values.keys()][i]},
    setItem(k,v){values.set(k,v)},getItem(k){return values.get(k)},removeItem(k){values.delete(k)}};
}
function app(storage, fetcher, user=10, scope='') {
  const fields = new URLSearchParams({user:JSON.stringify({id:user}),hash:'NEVER_STORE_CREDENTIALS'});
  if(scope) fields.set('start_param',scope);
  const window = {localStorage:storage,Telegram:{WebApp:{initData:fields.toString()}}};
  vm.runInNewContext(code,{window,fetch:fetcher,URLSearchParams,AbortController,setTimeout,clearTimeout});
  return window.PoopLive;
}
const response = (data,status=200) => ({ok:status===200,status,json:async()=>data});

test('reload retains only scoped opaque IDs and checks server before forgetting',async()=>{
  const storage=store(), calls=[];
  let first=app(storage,async()=>{throw new Error('connection lost')});
  first.remember(ID);
  await assert.rejects(first.create({requestId:ID,kind:2,feeling:0,date:'2026-10-20'}));
  assert.deepEqual([...storage.values.values()],['1']);
  assert(!JSON.stringify([...storage.values]).includes('NEVER_STORE_CREDENTIALS'));
  assert(!JSON.stringify([...storage.values]).includes('feeling'));
  const reopened=app(storage,async(path,options)=>{calls.push([path,JSON.parse(options.body)]);return response({status:'completed'})});
  assert.deepEqual(Array.from(await reopened.recoverPending()),['completed']);
  assert.deepEqual(calls,[['/poopcheck-api/recover',{requestId:ID}]]);
  assert.equal(storage.length,0);
});

test('different accounts and chat links do not recover each other',async()=>{
  const storage=store();
  app(storage,null,10,'group-A').remember(ID);
  for (const [user,scope] of [[20,'group-A'],[10,'group-B'],[10,'']]) {
    const client=app(storage,()=>{throw new Error('must not fetch')},user,scope);
    assert.equal((await client.recoverPending()).length,0);
  }
  assert.equal(storage.length,1);
});

test('network, expired auth, forbidden and unknown outcomes retain marker',async()=>{
  const storage=store();app(storage,null).remember(ID);
  for(const fetcher of [async()=>{throw new Error('offline')},async()=>response({},401),
      async()=>response({},403),async()=>response({status:'unknown'})]) {
    await assert.rejects(app(storage,fetcher).recoverPending());
    assert.equal(storage.length,1);
  }
  assert.deepEqual(Array.from(await app(storage,async()=>response({status:'cancelled'})).recoverPending()),['cancelled']);
  assert.equal(storage.length,0);
});

test('storage refusal prevents starting a reliable mutation',()=>{
  const storage=store();storage.setItem=()=>{throw new Error('quota')};
  assert.throws(()=>app(storage,null).remember(ID),/не отправлена/);
  assert.equal(storage.length,0);
});
