const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { webcrypto } = require('node:crypto');

const source = fs.readFileSync('app/assets/widgets/daily-live.html', 'utf8')
  .match(/<script>([\s\S]*)<\/script>/)[1].replace('__PRIVATE__', 'true').replace('__INDEX__', '0');
const state = (r, n, ack) => ({r, d: [n,0,0,0,0,0,0], ack});
function open(initial = state(0, 0), store = new Map()) {
  const elements = new Map(), requests = [];
  const element = () => ({ style: {}, appendChild(child){ this.lastChild = child; } });
  function get(id) {
    if (!elements.has(id)) elements.set(id, element());
    return elements.get(id);
  }
  let sync;
  const widget = {
    ready(){}, onEvent(_, cb){sync = cb;}, chatData: JSON.stringify(initial),
    request(key){return new Promise((resolve, reject) => requests.push({key,resolve,reject}));}
  };
  vm.runInNewContext(source, { TelegramWidget: widget, crypto: webcrypto,
    localStorage: {getItem:k=>store.get(k), setItem:(k,v)=>store.set(k,v), removeItem:k=>store.delete(k)},
    document: {getElementById:get, createElement:element} });
  return { get, requests, store, push(s){widget.chatData=JSON.stringify(s);sync();} };
}
const ack = request => ({n:request.key.split(':')[2],t:'Твоих отметок: 1',e:31,v:'cccccccccccccccc'});

test('a published receipt wins over a late failure, even during the next tap',async()=>{
  const w=open(), first=w.get('plus').onclick(), request=w.requests[0];
  w.push(state(1,1,ack(request)));
  assert.equal(w.get('hint').textContent,'Твоих отметок: 1');
  const second=w.get('minus').onclick();
  request.reject(new Error('TIMEOUT')); await first;
  assert.equal(w.store.get('pending'),w.requests[1].key);
  w.requests[1].resolve({result:JSON.stringify({s:state(2,0),t:'Удалено'})}); await second;
  assert.equal(w.get('hint').textContent,'Удалено');
  assert.equal(w.get('count').textContent,0);
});

test('unconfirmed tap survives reload and retries with the original key',async()=>{
  const w=open(), tap=w.get('plus').onclick(), request=w.requests[0];
  request.resolve({result:null}); await tap;
  const other=state(1,1,{n:'different',t:'Другая отметка'});
  w.push(other);
  assert.equal(w.get('hint').textContent,'Ожидаем подтверждения');
  const reopened=open(other,w.store);
  const retry=reopened.get('panel').lastChild.onclick();
  assert.equal(reopened.requests[0].key,request.key);
  reopened.push(state(2,2,{...ack(request),t:'Твоих отметок: 2'}));
  reopened.requests[0].reject(new Error('TIMEOUT')); await retry;
  assert.equal(w.store.size,0);
  assert.equal(reopened.get('count').textContent,2);
  assert.equal(reopened.get('hint').textContent,'Твоих отметок: 2');
});
