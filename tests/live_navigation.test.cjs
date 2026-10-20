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
  const attributes={}, captured=new Set();
  const neighbors=[], animations=[];
  const visualElement=()=>({style:{},attributes:{},innerHTML:'',removed:false,
    setAttribute(name,value){this.attributes[name]=value},remove(){this.removed=true},
    animate(frames,options){animations.push({frames,options});return {finished:Promise.resolve(),cancel(){}}}});
  const dragCard=visualElement();
  const viewport={querySelector:()=>dragCard,getBoundingClientRect:()=>({width:320}),
    appendChild:e=>neighbors.push(e)};
  const element={innerHTML:'',addEventListener:(name,fn)=>handlers[name]=fn,
    setAttribute:(name,value)=>attributes[name]=value,
    setPointerCapture:id=>captured.add(id),hasPointerCapture:id=>captured.has(id),
    releasePointerCapture:id=>captured.delete(id),querySelectorAll:()=>[],
    querySelector:selector=>state.visual&&selector==='.month-viewport'?viewport:null};
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
    createElement:visualElement,getElementById:id=>id==='app'?element:{textContent:''},addEventListener:(name,fn)=>lifecycle[name]=fn};
  const window={PoopLive:live,matchMedia:q=>({matches:q.includes('reduced-motion')?Boolean(state.reducedMotion):true}),scrollTo(){},alert:t=>notices.push(t),
    addEventListener:(name,fn)=>lifecycle[name]=fn,confirm:()=>state.confirm??false,
    Telegram:{WebApp:{platform:'tdesktop',onEvent:(name,fn)=>lifecycle[name]=fn,
      BackButton:{onClick:fn=>lifecycle.back=fn,show(){state.backVisible=true},hide(){state.backVisible=false}}}}};
  const context=vm.createContext({window,document,AbortController,setTimeout,clearTimeout,
    crypto:{randomUUID:()=> '11111111-2222-4333-8444-555555555555'}});
  vm.runInContext(fs.readFileSync('web/demo/model.js','utf8'),context);
  vm.runInContext(fs.readFileSync('web/demo/app.js','utf8'),context);
  await settle();
  return {state,reads,writes,notices,element,lifecycle,document,markers,recoveries,attributes,captured,dragCard,neighbors,animations,
    reveal:id=>handlers.keydown({key:"ArrowLeft",target:{closest:()=>({dataset:{event:id}})}}),
    click:async (dataset,detail=0)=>{await handlers.click({detail,preventDefault(){},target:{closest:()=>({dataset})}});await settle()},
    pointer:async (type,values={},row=null)=>{await handlers[type]({pointerId:1,isPrimary:true,button:0,
      clientX:100,clientY:200,target:{closest:selector=>selector==='[data-event]'&&row?{dataset:{event:row}}:null},...values});await settle()},
  };
}

async function swipe(a,dx,dy=0,row=null) {
  await a.pointer('pointerdown',{},row);
  await a.pointer('pointermove',{clientX:100+dx,clientY:200+dy},row);
  await a.pointer('pointerup',{clientX:100+dx,clientY:200+dy},row);
}

const weekTarget={closest:selector=>selector==='[data-week-draggable]'?{}:null};
async function weekSwipe(a,dx,dy=0) {
  await a.pointer('pointerdown',{target:weekTarget});
  await a.pointer('pointermove',{clientX:100+dx,clientY:200+dy});
  await a.pointer('pointerup',{clientX:100+dx,clientY:200+dy});
}

test('week follows the pointer, previews correct selection/counts, reuses one read and suppresses click',async()=>{
  const a=await boot({visual:true});a.state.data.counts['2026-10-13']=3;
  const reads=a.reads.length;
  await a.pointer('pointerdown',{target:weekTarget});
  await a.pointer('pointermove',{clientX:160});
  assert.equal(a.dragCard.style.transform,'translateX(60px)');
  assert.match(a.neighbors[0].innerHTML,/2026-10-13, отметок: 3" aria-pressed="true"/);
  assert.match(a.neighbors[0].innerHTML,/aria-label="Неделя"/);
  await a.pointer('pointermove',{clientX:220});await a.pointer('pointerup',{clientX:220});
  assert.equal(a.reads.length,reads+1);
  assert.match(a.element.innerHTML,/2026-10-13, отметок: 3" aria-pressed="true"/);
  assert.equal(a.attributes['data-screen'],'kind');
  await a.click({choice:'1'},1);assert.equal(a.attributes['data-screen'],'kind');
  await a.click({action:'calendar'});assert.match(a.element.innerHTML,/2026-10-13, отметок: 3" aria-pressed="true"/);
  assert.equal(a.writes.length,0);
});

test('week navigation crosses months and years, selects the same weekday and feeds statistics',async()=>{
  const a=await boot({visual:true});await a.click({date:'2026-01-02'});
  await weekSwipe(a,120);assert.equal(a.reads.at(-1),'2025-12');
  assert.match(a.element.innerHTML,/2025-12-26[^>]*aria-pressed="true"/);
  await weekSwipe(a,-120);assert.equal(a.reads.at(-1),'2026-01');
  assert.match(a.element.innerHTML,/2026-01-02[^>]*aria-pressed="true"/);
  assert.match(a.element.innerHTML,/data-date="2025-12-29"/);
  await a.click({action:'stats'});assert.match(a.element.innerHTML,/2025-12-29: 0/);
});

test('week bounds clamp selection to today and minimum date, with no future-week requests',async()=>{
  const a=await boot({visual:true});await a.click({date:'2026-10-18'});
  await weekSwipe(a,-120);assert.match(a.element.innerHTML,/2026-10-20[^>]*aria-pressed="true"/);
  let reads=a.reads.length;await weekSwipe(a,-120);assert.equal(a.reads.length,reads);
  await a.click({date:'2000-01-03'});await weekSwipe(a,120);
  assert.match(a.element.innerHTML,/2000-01-01[^>]*aria-pressed="true"/);
  assert.match(a.element.innerHTML,/data-date="1999-12-31"[^>]*disabled/);
  reads=a.reads.length;await weekSwipe(a,120);assert.equal(a.reads.length,reads);
});

test('short/vertical/cancelled week drags do not move; drafts and form area cannot be swiped',async()=>{
  const a=await boot({visual:true});await weekSwipe(a,30);await weekSwipe(a,120,150);
  await a.pointer('pointerdown',{target:weekTarget});await a.pointer('pointermove',{clientX:220});
  await a.pointer('pointercancel');await settle();
  assert.match(a.element.innerHTML,/2026-10-20[^>]*aria-pressed="true"/);
  let reads=a.reads.length;await swipe(a,120);assert.equal(a.reads.length,reads);
  await a.click({choice:'1'});reads=a.reads.length;await weekSwipe(a,120);
  assert.equal(a.reads.length,reads);assert.equal(a.attributes['data-screen'],'feeling');
  await a.lifecycle.back();await weekSwipe(a,120);assert.equal(a.reads.length,reads);
  assert.match(a.element.innerHTML,/choice selected/);
  await a.click({action:'home'});await a.click({action:'calendar'});await a.click({edit:'1'});
  reads=a.reads.length;await weekSwipe(a,120);assert.equal(a.reads.length,reads);
  assert.match(a.element.innerHTML,/2026-10-20, отметок: 1" aria-pressed="true"/);
  assert.equal(a.writes.length,0);
});

test('failed week preview preserves the target date on retry without empty data or duplicate requests',async()=>{
  const a=await boot({visual:true});a.state.fail=true;const reads=a.reads.length;
  await weekSwipe(a,120);assert.equal(a.reads.length,reads+1);assert.match(a.element.innerHTML,/offline/);
  a.state.fail=false;await a.click({action:'retry'});
  assert.match(a.element.innerHTML,/2026-10-13[^>]*aria-pressed="true"/);
});

test('touch capture transfer from a child keeps month/week/row gestures alive until release',async()=>{
  for(const surface of ['month','week','row']) {
    const a=await boot({visual:true});
    if(surface!=='week') await a.click({action:'calendar'});
    const row=surface==='row'?'1':null;
    const target=surface==='week'?weekTarget:{closest:selector=>selector==='[data-event]'&&row?{dataset:{event:row}}:null};
    await a.pointer('pointerdown',{pointerType:'touch',target},row);
    await a.pointer('pointermove',{pointerType:'touch',clientX:130},row);
    assert.equal(a.captured.has(1),true);
    // Touch starts with implicit capture on the child. Moving capture to app
    // bubbles this loss from the child before the next pointermove arrives.
    await a.pointer('lostpointercapture',{pointerType:'touch',target},row);
    await a.pointer('pointermove',{pointerType:'touch',clientX:170},row);
    assert.equal(a.captured.has(1),true);
    if(!row) assert.equal(a.dragCard.style.transform,'translateX(70px)');
    await a.pointer('pointermove',{pointerType:'touch',clientX:row?-20:220},row);
    await a.pointer('pointerup',{pointerType:'touch',clientX:row?-20:220},row);
    if(surface==='week')assert.match(a.element.innerHTML,/2026-10-13[^>]*aria-pressed="true"/);
    if(surface==='month')assert.match(a.element.innerHTML,/сентябрь/);
    if(row)assert.match(a.element.innerHTML,/event revealed/);
    assert.equal(a.captured.size,0);assert.equal(a.writes.length,0);
  }
});

test('actual loss of app capture still cancels a held gesture without navigating',async()=>{
  const a=await boot({visual:true});await a.click({action:'calendar'});
  await a.pointer('pointerdown',{pointerType:'touch'});
  await a.pointer('pointermove',{pointerType:'touch',clientX:220});
  a.captured.clear();
  await a.pointer('lostpointercapture',{pointerType:'touch',target:a.element});await settle();
  await a.pointer('pointerup',{pointerType:'touch',clientX:220});
  assert.match(a.element.innerHTML,/октябрь/);assert.equal(a.dragCard.style.transform,'');
  assert.ok(a.neighbors.every(n=>n.removed));
});

test('existing home and add controls return from distant dates to the current week without saving',async()=>{
  for(const action of ['home','add']) {
    const a=await boot();await a.click({date:'2024-02-29'});
    await a.click({action});assert.equal(a.attributes['data-screen'],'kind');
    assert.match(a.element.innerHTML,/2026-10-20[^>]*aria-pressed="true"/);
    assert.equal(a.reads.at(-1),null);assert.equal(a.writes.length,0);
  }
});

test('calendar follows a held pointer and a short slow drag returns without changing selection',async()=>{
  const a=await boot({visual:true});await a.click({action:'calendar'});const reads=a.reads.length;
  await a.pointer('pointerdown',{timeStamp:1000});
  await a.pointer('pointermove',{clientX:140,timeStamp:1200});
  assert.equal(a.dragCard.style.transform,'translateX(40px)');
  assert.equal(a.neighbors[0].style.transform,'translateX(-280px)');
  await a.pointer('pointermove',{clientX:165,timeStamp:1400});
  assert.equal(a.dragCard.style.transform,'translateX(65px)');
  assert.equal(a.reads.length,reads+1); // One preview, not one request per movement.
  assert.match(a.element.innerHTML,/2026-10-20[^>]*aria-pressed="true"/);
  await a.pointer('pointerup',{clientX:165,timeStamp:1600});
  assert.equal(a.dragCard.style.transform,'');assert.equal(a.neighbors[0].removed,true);
  assert.equal(a.animations[0].frames.at(-1).transform,'translateX(0px)');
  assert.match(a.element.innerHTML,/октябрь/);assert.equal(a.reads.length,reads+1);
  assert.equal(a.writes.length,0);
});

test('a committed drag reuses the preview response and slides both cards to their destination',async()=>{
  const a=await boot({visual:true});await a.click({action:'calendar'});const reads=a.reads.length;
  await swipe(a,120);
  assert.equal(a.reads.length,reads+1);assert.match(a.element.innerHTML,/сентябрь/);
  assert.equal(a.animations[0].frames.at(-1).transform,'translateX(320px)');
  assert.equal(a.animations[1].frames.at(-1).transform,'translateX(0px)');
  assert.equal(a.attributes['data-month-motion'],''); // No second entry animation.
  assert.equal(a.captured.size,0);assert.equal(a.attributes['data-month-drag'],'');
});

test('fast flick commits, but holding a short drag or reversing it returns to the current month',async()=>{
  for(const [releaseTime,expected] of [[1020,'сентябрь'],[1300,'октябрь']]){
    const a=await boot({visual:true});await a.click({action:'calendar'});
    await a.pointer('pointerdown',{timeStamp:1000});
    await a.pointer('pointermove',{clientX:135,timeStamp:1010});
    await a.pointer('pointerup',{clientX:135,timeStamp:releaseTime});
    assert.match(a.element.innerHTML,new RegExp(expected));
  }
  const a=await boot({visual:true});await a.click({action:'calendar'});
  await a.pointer('pointerdown',{timeStamp:1000});
  await a.pointer('pointermove',{clientX:220,timeStamp:1100});
  await a.pointer('pointermove',{clientX:130,timeStamp:1120});
  await a.pointer('pointerup',{clientX:130,timeStamp:1130});
  assert.match(a.element.innerHTML,/октябрь/);
});

test('calendar boundary resists dragging without requesting a future month',async()=>{
  const a=await boot({visual:true,reducedMotion:true});await a.click({action:'calendar'});
  const reads=a.reads.length;await a.pointer('pointerdown');
  await a.pointer('pointermove',{clientX:0});
  assert.equal(a.dragCard.style.transform,'translateX(-22px)');
  assert.equal(a.reads.length,reads);assert.equal(a.neighbors.length,0);
  await a.pointer('pointerup',{clientX:0});
  assert.match(a.element.innerHTML,/октябрь/);assert.equal(a.animations[0].options.duration,0);
});

test('changing drag direction loads each neighbor once; cancellation restores the current card',async()=>{
  const a=await boot({visual:true});await a.click({action:'calendar'});await a.click({date:'2026-09-01'});
  const reads=a.reads.length;await a.pointer('pointerdown');
  for(const x of [200,0,200,0])await a.pointer('pointermove',{clientX:x});
  assert.equal(a.reads.length,reads+2);assert.equal(a.neighbors.length,2);
  await a.pointer('pointercancel');await settle();
  assert.match(a.element.innerHTML,/сентябрь/);assert.equal(a.dragCard.style.transform,'');
  assert.ok(a.neighbors.every(n=>n.removed));assert.equal(a.captured.size,0);
});

test('a failed preview shows an error on commit rather than an empty history or another request',async()=>{
  const a=await boot({visual:true});await a.click({action:'calendar'});a.state.fail=true;
  const reads=a.reads.length;await swipe(a,120);
  assert.equal(a.reads.length,reads+1);assert.match(a.element.innerHTML,/offline/);
  assert.doesNotMatch(a.element.innerHTML,/Нет отметок/);
  a.state.fail=false;await a.click({action:'retry'});assert.equal(a.reads.at(-1),'2026-09');
});

test('a late preview cannot replace the visible month after the app is hidden',async()=>{
  const a=await boot({visual:true});await a.click({action:'calendar'});let release;
  a.state.hold=new Promise(r=>release=r);
  await a.pointer('pointerdown');await a.pointer('pointermove',{clientX:220});
  assert.equal(a.attributes['data-month-drag'],'true');
  a.document.visibilityState='hidden';a.lifecycle.visibilitychange();
  assert.equal(a.attributes['data-month-drag'],'');assert.equal(a.captured.size,0);
  release();await settle();await settle();
  assert.match(a.element.innerHTML,/октябрь/);assert.ok(a.neighbors.every(n=>n.removed));
});

test('calendar swipes navigate in both directions with bounds and no accidental date click',async()=>{
  const a=await boot();await a.click({action:'calendar'});
  await swipe(a,100);assert.equal(a.reads.at(-1),'2026-09');
  assert.match(a.element.innerHTML,/2026-09-01[^>]*aria-pressed="true"/);
  assert.equal(a.attributes['data-month-motion'],'previous');
  const reads=a.reads.length;await a.click({date:'2026-09-10'},1);
  assert.equal(a.reads.length,reads); // Generated click after dragging is consumed.
  await swipe(a,-100);assert.equal(a.reads.at(-1),'2026-10');
  assert.equal(a.attributes['data-month-motion'],'next');
  const upper=a.reads.length;await swipe(a,-100);assert.equal(a.reads.length,upper);
  await a.click({date:'2000-01-01'});const lower=a.reads.length;
  await swipe(a,100);assert.equal(a.reads.length,lower);
  assert.equal(a.writes.length,0);assert.equal(a.captured.size,0);
});

test('short, vertical, cancelled and secondary gestures do not change the month',async()=>{
  const a=await boot();await a.click({action:'calendar'});const reads=a.reads.length;
  await swipe(a,20);await swipe(a,80,100);
  await a.pointer('pointerdown');await a.pointer('pointermove',{clientX:102,clientY:225});
  await a.pointer('pointerup',{clientX:220,clientY:230}); // Vertical intent stays locked.
  await a.pointer('pointerdown');await a.pointer('pointermove',{clientX:200});
  await a.pointer('pointercancel');await a.pointer('pointerup',{clientX:200});
  await a.pointer('pointerdown',{button:2});await a.pointer('pointerup',{clientX:200});
  await a.pointer('pointerdown');await a.pointer('pointerdown',{pointerId:2,isPrimary:false});
  await a.pointer('pointerup',{clientX:200});
  assert.equal(a.reads.length,reads);assert.equal(a.captured.size,0);
  await a.click({action:'home'});const formReads=a.reads.length;
  await swipe(a,100);assert.equal(a.reads.length,formReads);
});

test('row drag reveals deletion without paging or opening the edit form',async()=>{
  const a=await boot();await a.click({action:'calendar'});const reads=a.reads.length;
  await swipe(a,-100,0,'1');
  assert.match(a.element.innerHTML,/event revealed/);assert.equal(a.reads.length,reads);
  await a.click({edit:'1'},1);assert.match(a.element.innerHTML,/Итоги дня/);
  await swipe(a,100,0,'1');assert.doesNotMatch(a.element.innerHTML,/event revealed/);
  await a.pointer('pointerdown',{},'1');await a.pointer('pointerup',{},'1');
  await a.click({edit:'1'},1);assert.match(a.element.innerHTML,/choice selected/);
  assert.equal(a.writes.length,0);
});

test('swipe cannot queue requests while reading; failed month stays selected on retry',async()=>{
  const a=await boot();await a.click({action:'calendar'});let release;
  a.state.hold=new Promise(r=>release=r);
  const first=swipe(a,100);await settle();await settle();await settle();
  const reads=a.reads.length;await swipe(a,100);assert.equal(a.reads.length,reads);
  release();await first;a.state.hold=null;a.state.fail=true;
  await swipe(a,100);assert.equal(a.reads.at(-1),'2026-08');
  assert.match(a.element.innerHTML,/offline/);a.state.fail=false;
  await a.click({action:'retry'});assert.equal(a.reads.at(-1),'2026-08');
});

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
