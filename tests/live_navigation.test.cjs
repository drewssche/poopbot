const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const settle = () => new Promise(resolve => setImmediate(resolve));
const initial = () => ({today:'2026-10-20',month:'2026-10',scope:'group',
  events:[{id:'1',version:'original',date:'2026-10-20',time:'12:00',kind:1,feeling:0}],
  counts:{'2026-10-20':1},stats:{total:1,best:1,current:1}});
async function boot(options = {}) {
  const handlers={}, lifecycle={}, reads=[], writes=[], notices=[];
  const state={data:initial(),fail:false,hold:null,backVisible:false,...options};
  const markers=new Set(), recoveries=[];
  const element={innerHTML:'',addEventListener:(name,fn)=>handlers[name]=fn,
    setAttribute(){},querySelectorAll:()=>[],querySelector:()=>null};
  const live={pendingIds:()=>[...markers],recoverPending:async()=>state.pendingResults||[],
    remember:id=>markers.add(id),forget:id=>markers.delete(id),
    async recoverOne(id){recoveries.push(id);if(state.recoveryError)throw state.recoveryError;
      markers.delete(id);return 'completed'},
    async read(month,signal){
      reads.push(month);
      if(state.hold) await state.hold;
      if(state.fail) throw state.fail === true ? new Error('offline') : state.fail;
      return {...structuredClone(state.data),month:month||state.data.today.slice(0,7)};
    },
    async create(body){writes.push(structuredClone(body));state.onCreate?.();if(state.writeError)throw state.writeError;return {}},
  };
  const document={documentElement:{dataset:{mode:'live'}},visibilityState:'visible',
    getElementById:id=>id==='app'?element:{textContent:''},addEventListener:(name,fn)=>lifecycle[name]=fn};
  const window={PoopLive:live,matchMedia:()=>({matches:true}),scrollTo(){},alert:t=>notices.push(t),
    addEventListener:(name,fn)=>lifecycle[name]=fn,confirm:()=>state.confirm??false,
    Telegram:{WebApp:{platform:'tdesktop',onEvent:(name,fn)=>lifecycle[name]=fn,
      BackButton:{onClick:fn=>lifecycle.back=fn,show(){state.backVisible=true},hide(){state.backVisible=false}}}}};
  const context=vm.createContext({window,document,AbortController,setTimeout,clearTimeout,
    crypto:{randomUUID:()=> '11111111-2222-4333-8444-555555555555'}});
  vm.runInContext(fs.readFileSync('web/demo/model.js','utf8'),context);
  vm.runInContext(fs.readFileSync('web/demo/app.js','utf8'),context);
  await settle();
  return {state,reads,writes,notices,element,lifecycle,document,markers,recoveries,
    reveal:id=>handlers.keydown({key:"ArrowLeft",target:{closest:()=>({dataset:{event:id}})}}),
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

test('Back cancels editing from its first step and cannot leak an old draft into a new mark',async()=>{
  const a=await boot();assert.equal(a.state.backVisible,false);
  await a.click({action:'calendar'});await a.click({edit:'1'});
  assert.equal(a.state.backVisible,true);
  await a.click({choice:'3'});
  await a.lifecycle.back();await settle();
  assert.match(a.element.innerHTML,/choice selected" data-choice="3"/);
  assert.equal(a.state.backVisible,true);
  await a.lifecycle.back();await settle();
  assert.match(a.element.innerHTML,/Итоги дня/);
  assert.match(a.element.innerHTML,/Норма, прекрасно/);
  assert.equal(a.writes.length,0);
  await a.click({action:'week'});await a.click({choice:'0'});await a.click({choice:'1'});
  assert.equal(a.writes[0].action,undefined);
  assert.equal(a.writes[0].eventId,undefined);
});

test('statistics is a temporary detour; Back restores the form before cancellation',async()=>{
  const a=await boot();await a.click({action:'calendar'});await a.click({edit:'1'});
  await a.click({choice:'2'});await a.click({action:'stats'});await a.click({action:'back'});
  assert.match(a.element.innerHTML,/Мои ощущения/);
  await a.lifecycle.back();await a.lifecycle.back();await settle();
  assert.match(a.element.innerHTML,/Итоги дня/);assert.equal(a.writes.length,0);
});

test('cancel a new draft without saving and hide Back on the clean home screen',async()=>{
  const a=await boot();await a.click({choice:'2'});
  await a.lifecycle.back();await a.lifecycle.back();await settle();
  assert.equal(a.state.backVisible,false);
  assert.doesNotMatch(a.element.innerHTML,/choice selected/);
  assert.equal(a.writes.length,0);
});

for(const status of [401,403]) test(`access error ${status} asks to reopen without repeated requests`,async()=>{
  const a=await boot();a.state.fail=Object.assign(new Error('Закрой приложение и открой снова.'),{status});
  await a.click({action:'calendar'});
  assert.match(a.element.innerHTML,/role="alert"/);
  assert.doesNotMatch(a.element.innerHTML,/data-action="retry"|Нет отметок|data-event=/);
  assert.equal(a.state.backVisible,false);
  const calls=a.reads.length;
  a.lifecycle.online();a.lifecycle.activated();await a.click({action:'retry'});
  await a.lifecycle.back();await settle();
  assert.equal(a.reads.length,calls);assert.equal(a.writes.length,0);
});

test('expired login during mutation keeps its marker for reopening and blocks writes',async()=>{
  const a=await boot();a.state.writeError=Object.assign(new Error('Вход устарел. Закрой приложение.'),{status:401});
  await a.click({choice:'2'});await a.click({choice:'0'});
  assert.match(a.element.innerHTML,/Вход устарел/);
  assert.equal(a.markers.size,1);assert.equal(a.recoveries.length,0);
  await a.click({choice:'0'});a.lifecycle.online();await settle();
  assert.equal(a.writes.length,1);assert.equal(a.markers.size,1);
});

test('failed read is not an empty history; successful retry displays actual zero statistics',async()=>{
  const a=await boot();a.state.fail=true;await a.click({action:'calendar'});
  assert.doesNotMatch(a.element.innerHTML,/Нет отметок|data-event=/);
  assert.match(a.element.innerHTML,/data-action="retry"/);
  a.state.fail=false;a.state.data.events=[];a.state.data.counts={};a.state.data.stats={total:0,best:0,current:0};
  await a.click({action:'retry'});assert.match(a.element.innerHTML,/Нет отметок/);
  await a.click({action:'stats'});
  assert.match(a.element.innerHTML,/<strong>0<\/strong>/);
  assert.doesNotMatch(a.element.innerHTML,/NaN|undefined|Infinity/);
});

test('recovery does not promise a refreshed calendar when the subsequent read fails',async()=>{
  const a=await boot({pendingResults:['completed'],fail:true});
  assert.match(a.notices.at(-1),/сохранено, но календарь не загрузился/);
  assert.doesNotMatch(a.notices.at(-1),/Календарь обновлён/);
});

test('saved mark remains on its date across month rollover, including a failed read retry',async()=>{
  const a=await boot();a.state.data.today='2026-10-31';a.lifecycle.activated();await settle();
  await a.click({choice:'1'});
  a.state.onCreate=()=>{a.state.data.today='2026-11-01';a.state.fail=true;
    a.state.data.events=[{...a.state.data.events[0],date:'2026-10-31'}]};
  await a.click({choice:'0'});
  assert.equal(a.writes[0].date,'2026-10-31');assert.equal(a.markers.size,0);
  a.state.fail=false;await a.click({action:'retry'});
  assert.equal(a.reads.at(-1),'2026-10');
  assert.match(a.element.innerHTML,/31\.10\.26/);
});

test('opening and cancelling an edit at midnight keep the selected old month',async()=>{
  const a=await boot();a.state.data.today='2026-10-31';
  a.state.data.events[0].date='2026-10-31';a.lifecycle.activated();await settle();
  await a.click({action:'calendar'});a.state.data.today='2026-11-01';
  await a.click({edit:'1'});assert.match(a.element.innerHTML,/choice selected/);
  await a.lifecycle.back();await settle();
  assert.equal(a.reads.at(-1),'2026-10');assert.match(a.element.innerHTML,/31\.10\.26/);
  assert.equal(a.writes.length,0);
});

test('delete confirmation freezes navigation; cancel leaves data intact',async()=>{
  const a=await boot();await a.click({action:'calendar'});a.reveal('1');
  let finish;a.state.confirm=new Promise(r=>finish=r);
  const deletion=a.click({delete:'1'});
  const reads=a.reads.length;
  await a.click({action:'home'});await a.lifecycle.back();a.lifecycle.online();
  assert.equal(a.state.backVisible,false);assert.equal(a.reads.length,reads);
  finish(false);await deletion;
  assert.equal(a.writes.length,0);assert.match(a.element.innerHTML,/data-event="1"/);
  assert.equal(a.state.backVisible,true);
});

test('deleting the last mark renders empty history and zero counters',async()=>{
  const a=await boot();await a.click({action:'calendar'});a.reveal('1');a.state.confirm=true;
  a.state.onCreate=()=>{a.state.data.events=[];a.state.data.counts={};a.state.data.stats={total:0,best:0,current:0}};
  await a.click({delete:'1'});assert.equal(a.writes[0].action,'delete');
  assert.match(a.element.innerHTML,/Нет отметок/);
  await a.click({action:'stats'});assert.match(a.element.innerHTML,/<strong>0<\/strong>/);
});

test('calendar cannot navigate before the API lower bound or beyond the current month',async()=>{
  const a=await boot();await a.click({action:'calendar'});
  const reads=a.reads.length;await a.click({action:'next-month'});assert.equal(a.reads.length,reads);
  await a.click({date:'2000-01-01'});
  assert.match(a.element.innerHTML,/aria-label="Предыдущий месяц" disabled/);
  const lowerReads=a.reads.length;await a.click({action:'previous-month'});assert.equal(a.reads.length,lowerReads);
});
