let state, reading=0;
const trail=['home'];
const nav=$('nav'), art=JSON.parse(w.publicData||'[]');
function icon(box,path,fill){
  return '<svg width="20" height="20" viewBox="'+box+'"><path fill="'+fill+'" d="'+path+'"/></svg>'
}
function setQuestion(title){
  $('title').textContent=title;panel.className='';panel.textContent='';nav.textContent=''
}
function control(parent,label,draw,click){
  const el=document.createElement('button');
  el.textContent=draw;el.setAttribute('aria-label',label);
  el.onclick=click;parent.appendChild(el);return el
}
function link(parent,label,key,glyph=label){
  return control(parent,label,glyph,()=>{
    if(key[0]==='!')return act(key.slice(1));
    if(key==='app')return w.openTelegramLink('https://t.me/poopcheck_bot?startapp');
    return visit(key)
  })
}
function mark(el,n){
  el.className='day';
  if(n)el.innerHTML=icon('32 374 42 40',art[1],'#934c21')+'<b>'+el.textContent+'</b>'+(n>1?'<i>'+n+'</i>':'')
}
function record([label,key]){
  const [number,time,...details]=label.split(' · ');
  const el=control(panel,label,'',()=>visit('u'+key.slice(1)));
  const caption=document.createElement('div');
  const meta=document.createElement('small');
  meta.textContent=[number,time].filter(Boolean).join(' · ');
  caption.appendChild(meta);
  caption.appendChild(document.createTextNode(details.join(' · ')));
  el.appendChild(caption);
  el.className='entry';
  const pencil=document.createElement('span');pencil.innerHTML=icon('326 584 24 24',art[2],'white');el.appendChild(pencil)
}
function editor(v){
  panel.innerHTML=v.form;
  const values=v.values;
  const save=control(nav,'Сохранить','✓',()=>{if(!save.disabled)return act(values.every(n=>n<0)?'+':v.op+values.join(''))});
  const rows=Array.from(panel.querySelectorAll('.choices'));
  function update(){
    save.disabled=values.some(n=>n<0)&&!(v.op==='a'&&values.every(n=>n<0));
    save.className=save.disabled?'':'active';
    rows.forEach((row,i)=>Array.from(row.children).forEach((b,n)=>{
      b.className=values[i]===n?'active':'';
      b.setAttribute('aria-pressed',String(values[i]===n));
      if(values[i]===n)row.previousElementSibling.textContent=b.getAttribute('aria-label');
      b.onclick=()=>{values[i]=n;update()}
    }))
  }
  update();
  if(v.remove)control(nav,'Удалить','',()=>visit(v.remove)).innerHTML=v.trash

}
function show(v){
  const home=!v||v.home;
  if(home)trail.length=1;
  const menu=$('menu');
  menu.textContent=home?'☰':'←';
  menu.setAttribute('aria-label',home?'Меню':'Назад');
  $('calendar').style.display=home?'block':'none';
  $('controls').style.display=home?'flex':'none';
  setQuestion(home?(closed?'Сессия закрыта':''):v.title||'');
  if(home){
    (state.entries||[]).forEach(record);
    if(state.d[__INDEX__]>3)link(nav,'Ещё отметки','d__STAMP__.1','…');
    return
  }
  if(v.body){panel.className='note';button(v.body,null)}
  else if(v.month){
    panel.className='grid';
    for(const name of 'пн вт ср чт пт сб вс'.split(' '))button(name,null);
    for(let i=0;i<v.offset;i++)button('',null);
    v.cells.forEach((n,index)=>{
      const day=String(index+1);
      if(n<0)button(day,null);
      else mark(link(panel,day,'d'+v.month+day.padStart(2,'0')),n)
    })
  }else if(v.form)editor(v);
  else (v.items||[]).forEach(record);
  v.nav.forEach(row=>link(nav,...row))
}
async function visit(key,back=false){
  if(pending||!state)return;
  const seq=++reading;
  if(key==='home'){message('');show();return}
  message('…');
  function failed(){if(seq===reading){reading++;message('Попробуй ещё раз')}}
  const timer=setTimeout(failed,12000);
  try{
    const v=JSON.parse((await w.request('q:n:'+key)).result);
    if(seq!==reading||pending)return;
    message(v.t||'');
    if(v.t)return;
    if(back)trail.pop();
    else if(key!==trail.at(-1))trail.push(key);
    show(v)
  }catch(e){failed()}
  finally{clearTimeout(timer)}
}
function paint(s){
  if(w.isForward||!s||s.r<revision)return;
  state=s;revision=s.r;closed=!!s.c;
  s.d.forEach((n,i)=>{
    const el=$('d'+i);el.textContent=el.getAttribute('data-day');mark(el,n)
  });
  $('plus').style.display=closed&&Date.now()/1000>=s.x?'none':'flex';
  if(!pending&&trail.length===1)show();
  if(s.ack&&pending&&pending.split(':')[2]===s.ack.n)finish(pending,s.ack)
}
function done(a){reading++;show();message(a.ok||a.t.startsWith('Твоих отметок:')?'':a.t)}
function bind(){
  $('wordmark').setAttribute('d',art[0]||'');
  if(w.isForward){$('controls').style.display='none';return}
  $('plus').onclick=()=>closed?act('f'):visit('new');
  for(let i=0;i<=__INDEX__;i++)$('w'+i).onclick=()=>visit('w'+i);
  $('month').onclick=()=>visit('m');
  $('menu').onclick=()=>trail.length===1?visit('menu'):visit(trail.at(-2),true)
}
