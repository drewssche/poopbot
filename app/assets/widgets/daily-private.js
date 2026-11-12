let state,reading=0,flash;
const trail=[],nav=$('nav');
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
    if(key.startsWith('https://'))return w.openLink(key);
    if(key==='app')return w.openTelegramLink('https://t.me/poopcheck_bot?startapp');
    return visit(key)
  })
}
function calendar(v,month){
  const grid=month?panel:$('week');
  grid.textContent='';
  grid.className='grid';
  for(const name of 'пн вт ср чт пт сб вс'.split(' ')){
    const el=document.createElement('small');el.textContent=name;grid.appendChild(el)
  }
  for(let i=0;i<(v.offset||0);i++)grid.appendChild(document.createElement('span'));
  (v.cells||v.d).forEach((count,i)=>{
    const day=v.start+i, num=String(new Date(day*86400000).getUTCDate());
    const selected=day===(v.day??state.day);
    const el=link(grid,count>0?num+' · '+count:num,'t'+day,'');
    el.disabled=day>state.day;
    el.className='day'+(selected?' selected':'');
    el.innerHTML=(count>0&&!selected?icon('32 374 42 40','__DAY_PATH__','#934c21'):'')+
      '<b>'+num+'</b>'+(count>1?'<i>'+count+'</i>':'')
  })
}
function record([display,key]){
  const el=control(panel,'Редактировать '+display,display,()=>visit('u'+key));
  el.className='entry';
  if(key.split('.')[0]===String(flash))el.className+=' saved';
  const pencil=document.createElement('span');pencil.innerHTML=icon('326 584 24 24','__PENCIL__','white');el.appendChild(pencil)
}
function editor(v){
  const values=v.values;
  panel.innerHTML=v.form;
  Array.from(panel.querySelectorAll('.choices')).forEach((row,i)=>{
    function select(index){
      values[i]=index;
      Array.from(row.children).forEach((b,n)=>{
        b.className=n===index?'active':'';
        b.setAttribute('aria-pressed',String(n===index))
      })
    }
    Array.from(row.children).forEach((b,n)=>{b.onclick=()=>select(values[i]===n?-1:n)});
    select(values[i])
  });
  const save=control(nav,'Сохранить','✓',()=>{
    if(pending)return;
    save.disabled=true;save.textContent='…';
    return act(v.op+values.map(n=>n<0?'_':n).join(''))
  });
  save.className='active';
  if(v.remove)link(nav,'Удалить',v.remove,'🗑')
}
function show(){
  const v=trail.at(-1)||state,home=v===state;
  const menu=$('menu');
  menu.textContent=home?'☰':'←';
  menu.setAttribute('aria-label',home?'Меню':'Назад');
  $('calendar').style.display=v.d?'block':'none';
  const plus=$('plus');
  plus.className=closed?'restore':'';
  plus.textContent=state.restore||'+';
  plus.setAttribute('aria-label',state.restore||'Добавить отметку');
  $('controls').style.display=home&&(!closed||state.restore&&Date.now()/1000<state.x)?'flex':'none';
  setQuestion(home&&closed?'Сессия закрыта':v.title||'');
  if(v.d){
    $('month').textContent=v.label+' ⌄';
    calendar(v,false)
  }
  if(v.body){panel.className=v.d?'empty':'note';button(v.body,null)}
  if(v.month)calendar(v,true);
  if(v.op)editor(v);
  (v.items||[]).forEach(record);
  for(const [label,key,glyph] of v.nav||v.more||[]){
    const el=link(nav,label,key,glyph);
    if(key===v.active){el.className='active';el.disabled=true}
  }
}
async function visit(key){
  if(pending||!state)return;
  const seq=++reading;
  if(key==='home')trail.length=0;
  if(key==='back'){
    const child=trail.pop(),parent=trail.at(-1);
    if(child?.d&&parent?.month&&'m'+parent.month!==child.m)return visit(child.m)
  }
  if(key==='home'||key==='back'){show();message('');return}
  message('…');
  function failed(){if(seq===reading){reading++;message('Попробуй ещё раз')}}
  const timer=setTimeout(failed,12000);
  try{
    const v=JSON.parse((await w.request('q:j:'+key)).result);
    if(seq!==reading||pending)return;
    message(v.t||v.msg||'');
    if(v.t)return;
    const previous=trail.at(-1);
    if(v.home)trail.length=0;
    else{
      if(v.month){v.day=previous?.day??state.day;trail.length=0}
      else if(previous&&(v.d&&previous.d||v.active&&previous.active))trail.pop();
      trail.push(v)
    }
    show()
  }catch(e){failed()}
  finally{clearTimeout(timer)}
}
function paint(s){
  if(w.isForward||!s||s.r<revision)return;
  state=s;revision=s.r;closed=!!s.c;
  if(!pending&&!trail.length)show();
  if(s.ack&&pending&&pending.split(':')[2]===s.ack.n)finish(pending,s.ack)
}
function done(a){
  reading++;
  if(a.ok||a.t.startsWith('Твоих отметок:')){
    while(trail.length&&!trail.at(-1).d&&!trail.at(-1).month)trail.pop();
    if(a.goto){show();return visit(a.goto)}
    if(a.page){
      if(trail.at(-1)?.d)trail.pop();
      const month=trail.at(-1);
      if(month?.cells){
        const offset=a.page.day-month.start;
        if(offset>=0&&offset<month.cells.length)month.cells[offset]=a.page.d[a.page.day-a.page.start]
      }
      trail.push(a.page)
    }
    else trail.length=0;
    flash=a.e;setTimeout(()=>{flash=null},1500);
    message('')
  }else message(a.t);
  show()
}
function bind(){
  $('brand').setAttribute('src','data:image/webp;base64,'+w.publicData+'__LOGO_TAIL__');
  if(w.isForward){$('controls').style.display='none';return}
  $('plus').onclick=()=>closed?act('f'):visit('new');
  $('month').onclick=()=>{
    return visit((trail.at(-1)||state).m)
  };
  $('menu').onclick=()=>visit(trail.at(-1)?.up||(trail.length?'back':'menu'))
}
