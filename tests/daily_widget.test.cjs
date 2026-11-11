const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {webcrypto} = require('node:crypto');

const source=fs.readFileSync('app/assets/widgets/daily-live.html','utf8').match(/<script>([\s\S]*)<\/script>/)[1]
  .replace('__VIEW__',fs.readFileSync('app/assets/widgets/daily-private.js','utf8'))
  .replace('__RETRY__',"control(panel,'Проверить отметку','↻',()=>act('',true))")
  .replaceAll('__INDEX__','0').replaceAll('__STAMP__','20261109');
const state=(r,n,ack)=>({r,d:[n,0,0,0,0,0,0],ack});
function element(){
  return {style:{},children:[],getAttribute(k){return this[k]},setAttribute(k,v){this[k]=v},
    set textContent(value){this.text=value;this.children=[]},get textContent(){return this.text},
    appendChild(child){this.children.push(child)},querySelectorAll(){return this.rows||[]}};
}
function open(initial=state(0,0), store=new Map(), timers={setTimeout,clearTimeout}){
  const nodes=new Map(), requests=[];
  function get(id){if(!nodes.has(id))nodes.set(id,element());return nodes.get(id)}
  const panel=get('panel');
  panel.rows=[4,3].map(n=>({previousElementSibling:element(),children:Array.from({length:n},element)}));
  let sync;
  const widget={ready(){},onEvent(_,cb){sync=cb}, chatData:JSON.stringify(initial),
    request(key){return new Promise((resolve,reject)=>requests.push({key,resolve,reject}))}};
  vm.runInNewContext(source,{TelegramWidget:widget,crypto:webcrypto,...timers,
    localStorage:{getItem:k=>store.get(k),setItem:(k,v)=>store.set(k,v),removeItem:k=>store.delete(k)},
    document:{getElementById:get,createElement:element}});
  const nav=()=>get('nav').children.find(b=>b['aria-label']==='Сохранить');
  async function draft(){
    const opened=get('plus').onclick();
    const view=requests.pop();
    assert.equal(view.key,'q:p:new');
    view.resolve({result:JSON.stringify({form:'choices',values:[-1,-1],op:'a',nav:[]})});
    await opened;
    return nav()
  }
  return {get,draft,nav,requests,store,push(s){widget.chatData=JSON.stringify(s);sync()}};
}
const ack=req=>({n:req.key.split(':')[2],t:'Твоих отметок: 1'});
test('receipt wins over a late failure, including after the next save',async()=>{
  const w=open(), first=(await w.draft()).onclick(), req=w.requests[0];
  w.push(state(1,1,ack(req)));
  const second=(await w.draft()).onclick();
  req.reject(Error('TIMEOUT'));await first;
  assert.equal(w.store.get('pending'),w.requests[1].key);
  w.requests[1].resolve({result:JSON.stringify({s:state(2,2),t:'Отметка сохранена',ok:true})});
  await second;assert.equal(w.store.size,0);assert.equal(w.get('hint').textContent,'');
});
test('unconfirmed save survives reload and retries with its original key',async()=>{
  const w=open(), tap=(await w.draft()).onclick(), req=w.requests[0];
  req.resolve({result:null});await tap;
  const other=state(1,1,{n:'different',t:'Другая отметка'});
  w.push(other);assert.equal(w.get('hint').textContent,'Ожидаем подтверждения');
  const reopened=open(other,w.store), retry=reopened.get('panel').children.at(-1).onclick();
  assert.equal(reopened.requests[0].key,req.key);
  reopened.push(state(2,2,ack(req)));reopened.requests[0].reject(Error('TIMEOUT'));await retry;
  assert.equal(w.store.size,0);
});
test('both rows belong to one draft, and a versioned edit uses the selected record',async()=>{
  const w=open(), save=await w.draft(), rows=w.get('panel').rows;
  rows[0].children[1].onclick();assert.equal(save.disabled,true);
  rows[1].children[2].onclick();assert.equal(save.disabled,false);
  const adding=save.onclick(), add=w.requests[0], key='31.cccccccccccccccc';
  assert.match(add.key,/^q:a12:/);
  add.resolve({result:JSON.stringify({ok:true,t:'Отметка сохранена',s:{...state(1,1),entries:[['№1','i'+key]]}})});
  await adding;assert.match(w.get('d0').innerHTML,/<svg/);
  const opening=w.get('panel').children[0].onclick(), read=w.requests[1];
  assert.equal(read.key,'q:p:u'+key);
  read.resolve({result:JSON.stringify({form:'choices',values:[1,2],op:'E'+key+'.',nav:[]})});await opening;
  assert.equal(rows[0].children[1]['aria-pressed'],'true');
  rows[1].children[0].onclick();
  const edit=w.nav().onclick();assert.ok(w.requests[2].key.startsWith('q:E'+key+'.10:'));
  w.requests[2].resolve({result:JSON.stringify({s:state(2,1),ok:true,t:'Уточнения сохранены'})});await edit;
  assert.equal(w.store.size,0)
});
test('a stalled save exposes retry without creating a new nonce',async()=>{
  let fire;
  const w=open(state(0,0),new Map(),{setTimeout(cb){fire=cb;return cb},clearTimeout(){}});
  const first=(await w.draft()).onclick(), req=w.requests[0];fire();
  const retry=w.get('panel').children.at(-1).onclick(), second=w.requests[1];
  assert.equal(req.key,second.key);
  req.reject(Error('TIMEOUT'));await first;
  second.resolve({result:JSON.stringify({...ack(req),s:state(1,1)})});await retry;
  assert.equal(w.store.size,0);
});
