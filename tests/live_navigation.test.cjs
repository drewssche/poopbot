const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const settle = () => new Promise(resolve => setImmediate(resolve));
const initial = () => ({today:'2026-10-20',month:'2026-10',scope:'group',
  events:[{id:'1',version:'original',date:'2026-10-20',time:'12:00',kind:1,feeling:0}],
  counts:{'2026-10-20':1},stats:{total:1,best:1,current:1}});
async function boot() {
  const handlers={}, lifecycle={}, reads=[], writes=[], notices=[];
  const state={data:initial(),fail:false,hold:null};
  const element={innerHTML:'',addEventListener:(name,fn)=>handlers[name]=fn,
    setAttribute(){},querySelectorAll:()=>[],querySelector:()=>null};
  const live={pendingIds:()=>[],recoverPending:async()=>[],remember(){},forget(){},
    async read(month,signal){
      reads.push(month);
      if(state.hold) await state.hold;
      if(state.fail) throw new Error('offline');
      return {...structuredClone(state.data),month:month||state.data.today.slice(0,7)};
    },
    async create(body){writes.push(structuredClone(body));return {}},
  };
  const document={documentElement:{dataset:{mode:'live'}},visibilityState:'visible',
    getElementById:id=>id==='app'?element:{textContent:''},addEventListener:(name,fn)=>lifecycle[name]=fn};
  const window={PoopLive:live,matchMedia:()=>({matches:true}),scrollTo(){},alert:t=>notices.push(t),
    addEventListener:(name,fn)=>lifecycle[name]=fn,
    Telegram:{WebApp:{platform:'tdesktop',onEvent:(name,fn)=>lifecycle[name]=fn,
      BackButton:{onClick:fn=>lifecycle.back=fn,show(){},hide(){}}}}};
  const context=vm.createContext({window,document,AbortController,setTimeout,clearTimeout,
    crypto:{randomUUID:()=> '11111111-2222-4333-8444-555555555555'}});
  vm.runInContext(fs.readFileSync('web/demo/model.js','utf8'),context);
  vm.runInContext(fs.readFileSync('web/demo/app.js','utf8'),context);
  await settle();
  return {state,reads,writes,notices,element,lifecycle,document,
    click:async dataset=>{await handlers.click({target:{closest:()=>({dataset})}});await settle()},
  };
}

test('bot changes appear on calendar, day, statistics and back; no idle polling',async()=>{
  const a=await boot();assert.equal(a.reads.length,1);
  a.state.data.events.push({...a.state.data.events[0],id:'2',kind:3});
  a.state.data.counts['2026-10-20']=2;a.state.data.stats.total=2;
  await a.click({action:'calendar'});
  assert.match(a.element.innerHTML,/class="badge">2/);
  assert.match(a.element.innerHTML,/data-event="2"/);
  a.state.data.events=[];a.state.data.counts={};a.state.data.stats.total=0;
  await a.click({date:'2026-10-20'});
  assert.match(a.element.innerHTML,/Нет отметок/);
  await a.click({action:'stats'});
  assert.match(a.element.innerHTML,/<strong>0<\/strong><span>всего кала/);
  await a.click({action:'back'});
  assert.equal(a.reads.length,5);
  await new Promise(r=>setTimeout(r,30));
  assert.equal(a.reads.length,5);
});

test('edit fetches newest revision and detects a removed record',async()=>{
  const a=await boot();await a.click({action:'calendar'});
  a.state.data.events[0].version='latest';a.state.data.events[0].kind=2;
  await a.click({edit:'1'});
  assert.match(a.element.innerHTML,/choice selected" data-choice="2"/);
  await a.click({choice:'3'});await a.click({choice:'1'});
  assert.equal(a.writes[0].version,'latest');
  assert.equal(a.writes[0].kind,3);
  a.state.data.events=[];
  await a.click({edit:'1'});
  assert.match(a.notices.at(-1),/уже удалена/);
});

test('return/reconnect and read-error retry preserve draft and its conflict revision',async()=>{
  const a=await boot();await a.click({action:'calendar'});await a.click({edit:'1'});
  await a.click({choice:'3'});
  a.state.data.events[0].version='changed-in-bot';
  a.state.data.events[0].kind=0;
  a.document.visibilityState='hidden';a.lifecycle.visibilitychange();
  const before=a.reads.length;
  assert.equal(a.reads.length,before);
  a.document.visibilityState='visible';a.lifecycle.visibilitychange();await settle();
  assert.equal(a.reads.length,before+1); // Even a quick return revalidates.
  assert.match(a.element.innerHTML,/Мои ощущения/);
  a.state.fail=true;a.lifecycle.online();await settle();
  assert.match(a.element.innerHTML,/offline/);
  a.state.fail=false;await a.click({action:'retry'});
  assert.match(a.element.innerHTML,/Мои ощущения/);
  await a.click({choice:'2'});
  assert.equal(a.writes[0].kind,3);
  assert.equal(a.writes[0].version,'original'); // Server rejects stale edits instead of overwriting.
});

test('navigation guards concurrent activation and back while reading',async()=>{
  const a=await boot();let release;
  a.state.hold=new Promise(r=>release=r);
  const click=a.click({action:'calendar'});
  a.lifecycle.activated();a.lifecycle.online();a.lifecycle.back();
  await a.click({choice:'2'});
  assert.equal(a.reads.length,2);assert.equal(a.writes.length,0);
  release();await click;
  assert.match(a.element.innerHTML,/Итоги дня/);
});

test('today follows month rollover; unfinished forms remain bound to their original day',async()=>{
  const a=await boot();
  a.state.data.today='2026-11-01';a.lifecycle.activated();await settle();
  assert.match(a.element.innerHTML,/ноябрь/);
  assert.equal(a.reads.at(-1),null);
  await a.click({choice:'1'});
  a.state.data.today='2026-11-02';a.lifecycle.activated();await settle();
  await a.click({choice:'0'});
  // The date changed while a new mark was being filled out: don't silently move it.
  assert.equal(a.writes.length,0);
  assert.match(a.notices.at(-1),/только за сегодня/);
});
