function setQuestion(title){$('face').style.display=title==='Консистенция кала'?'block':'none';$('title').textContent=title;panel.textContent=''}
function choices(prefix){
  setQuestion('Консистенция кала');
  ['Жестко','Норма','Мягко','Водичка'].forEach((label,k)=>button(label,()=>{
    setQuestion('Мои ощущения');
    ['Прекрасно','Сойдёт','Ужасно'].forEach((name,f)=>button(name,()=>act(prefix+k+f)))
  }))
}
function home(){
  if(w.isForward)setQuestion('Копия · только просмотр');
  else setQuestion(closed?'Сессия закрыта':'Кто сегодня какал?')
}
function paint(state){
  if(!state||state.r<revision)return;
  const first=revision<0,wasClosed=closed;
  revision=state.r;closed=!!state.c;
  state.d.forEach((n,i)=>$('d'+i).className=n?'day marked':'day');
  $('count').textContent=state.d[__INDEX__];
  $('minus').style.display=closed||w.isForward?'none':'flex';
  $('plus').style.display=w.isForward||(closed&&Date.now()/1000>=state.x)?'none':'flex';
  if(closed){$('plus').textContent='Забыл отметиться';$('controls').className='controls closed';$('plus').setAttribute('aria-label','Забыл отметиться')}
  if(first||wasClosed!==closed)home();
  if(state.ack&&pending&&pending.split(':')[2]===state.ack.n)finish(pending,state.ack)
}
function done(a){
  message(a.t);
  if(a.e&&!closed){
    const edit=()=>choices('e'+a.e+'.'+a.v+'.');
    home();button('Уточнить',edit)
  }else home()
}
function bind(){
  $('plus').onclick=()=>act(closed?'f':'+');$('minus').onclick=()=>act('-');
  if(w.isForward){home();$('controls').style.display='none'}
}
