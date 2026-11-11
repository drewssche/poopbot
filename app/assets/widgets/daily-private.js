let state,route='home',reading=0;
function setQuestion(title){
  $('title').textContent=title;panel.className='';panel.textContent='';$('nav').textContent=''
}
function action(label,key,parent=panel){
  const el=document.createElement('button');el.textContent=label;parent.appendChild(el);
  el.onclick=()=>key[0]==='!'?act(key.slice(1)):visit(key);
  return el
}
function show(view){
  const home=!view||view.home;
  if(home)route='home';
  $('calendar').style.display=view?.month?'none':'block';
  $('controls').style.display=home?'flex':'none';
  setQuestion(home?(closed?'Сессия закрыта':''):view.title);
  if(home){
    (state.entries||[]).forEach(([label,key])=>action(label+'  ›',key));
    if(state.d[__INDEX__]>3)action('Ещё отметки','d__STAMP__.1')
  }else{
    if(view.month){
      panel.className='grid';
      for(const name of 'пн вт ср чт пт сб вс'.split(' '))button(name,null);
      for(let i=0;i<view.offset;i++)button('',null);
      for(let i=1;i<=view.days;i++){
        if(i>view.until)button(String(i),null);
        else action(String(i),'d'+view.month+String(i).padStart(2,'0')).className=view.marked.includes(i)?'marked':''
      }
    }else view.items.forEach(row=>action(...row));
    view.nav.forEach(([label,key])=>action(label,key,$('nav')))
  }
}
async function visit(key){
  if(pending||!state)return;
  const seq=++reading;
  message('');
  if(key==='home'){show();return}
  route=key;
  $('summary').style.display='none';$('form').style.display='block';
  $('controls').style.display='none';setQuestion('');message('Загрузка…');
  const timeout=setTimeout(()=>{if(reading===seq){reading++;show();message('Попробуй ещё раз')}},12000);
  try{
    const view=JSON.parse((await w.request('q:v:'+key)).result);
    if(seq!==reading)return;
    if(!view)throw Error('Попробуй ещё раз');
    if(view.t){show();message(view.t);return}
    message('');show(view)
  }catch(e){if(seq===reading){show();message('Не удалось открыть. Попробуй ещё раз')}}
  finally{clearTimeout(timeout)}
}
function paint(s){
  if(w.isForward||!s||s.r<revision)return;
  state=s;revision=s.r;closed=!!s.c;
  s.d.forEach((n,i)=>$('d'+i).className=n?'day marked':'day');
  $('count').textContent=s.d[__INDEX__];
  $('history').textContent=(s.h||'').split('\n\n')[0];
  $('restore').style.display=s.restore?'flex':'none';
  $('plus').style.display=closed&&Date.now()/1000>=s.x?'none':'flex';
  if(closed){$('plus').textContent='Забыл отметиться';$('controls').className='controls closed';$('plus').setAttribute('aria-label','Забыл отметиться')}
  if(!pending&&route==='home')show();
  if(s.ack&&pending&&pending.split(':')[2]===s.ack.n)finish(pending,s.ack)
}
function done(a){reading++;message(a.t.startsWith('Твоих отметок:')?'':a.t);show()}
function bind(){
  $('wordmark').setAttribute('d',w.publicData||'');
  if(w.isForward){$('controls').style.display=$('menu').style.display='none';setQuestion('Копия · только просмотр');return}
  $('plus').onclick=()=>act(closed?'f':'+');$('restore').onclick=()=>act('s');
  for(let i=0;i<=__INDEX__;i++)$('w'+i).onclick=()=>visit('w'+i);
  $('past').onclick=()=>w.openTelegramLink('https://t.me/poopcheck_bot?startapp');
  $('month').onclick=()=>visit('m');
  $('menu').onclick=()=>{
    const box=$('summary'),show=box.style.display==='none';
    box.style.display=show?'block':'none';$('form').style.display=show?'none':'block'
  }
}
