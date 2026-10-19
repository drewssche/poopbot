/* Credentials and record contents stay in memory. Only opaque request IDs persist. */
window.PoopLive = (() => {
  const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
  function telegramRequired() {
    const error = new Error('Открой приложение из Telegram: кнопкой в личке или ссылкой /app из группы.');
    error.status = 401;
    return error;
  }
  function prefix() {
    // Unverified fields only partition local markers; the server authorizes every call.
    const fields = new URLSearchParams(window.Telegram?.WebApp?.initData || '');
    let user;
    try { user = JSON.parse(fields.get('user') || '{}'); }
    catch { throw telegramRequired(); }
    if (!Number.isSafeInteger(user?.id) || user.id <= 0) throw telegramRequired();
    return `poopcheck.pending.v1:${user.id}:${encodeURIComponent(fields.get('start_param') || 'personal')}:`;
  }
  function pendingIds() {
    const key = prefix();
    try {
      const storage = window.localStorage, ids = [];
      for (let i = 0; i < storage.length; i++) {
        const name = storage.key(i);
        if (name?.startsWith(key) && uuid.test(name.slice(key.length))) ids.push(name.slice(key.length));
      }
      return ids;
    } catch { return []; }
  }
  function remember(id) {
    if (!uuid.test(id)) throw new Error('Некорректный номер запроса. Открой приложение заново.');
    try {
      const key = prefix() + id;
      window.localStorage.setItem(key, '1');
      if (window.localStorage.getItem(key) !== '1') throw new Error();
    } catch {
      throw new Error('Telegram не разрешил сохранить номер запроса на устройстве. Отметка не отправлена. Попробуй открыть приложение заново или отметься через бота.');
    }
  }
  function forget(id) {
    try { window.localStorage.removeItem(prefix() + id); } catch {}
  }
  async function request(path, body, signal) {
    const initData = window.Telegram?.WebApp?.initData;
    if (!initData) throw telegramRequired();
    let response;
    try {
      response = await fetch('/poopcheck-api/' + path, {
        method: 'POST', cache: 'no-store', credentials: 'omit', signal,
        headers: {'Content-Type': 'application/json', Authorization: 'tma ' + initData},
        body: JSON.stringify(body),
      });
    } catch (error) {
      if (error.name === 'AbortError') throw error;
      throw new Error('Нет связи с сервером. Проверь подключение и попробуй ещё раз.');
    }
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      const message = response.status === 401 ? 'Вход устарел. Закрой приложение и открой снова.'
        : response.status === 403 ? 'Нет доступа к этому чату. Закрой приложение и открой его через бота: в личке или по свежей ссылке /app из своей группы.'
        : response.status === 429 ? 'Слишком много запросов. Попробуй чуть позже.'
        : response.status === 409 && typeof data?.error === 'string' ? data.error
        : 'Не удалось подтвердить запрос. Попробуй ещё раз.';
      const error = new Error(message);
      error.status = response.status;
      error.definitive = response.status >= 400 && response.status < 500;
      throw error;
    }
    try { return await response.json(); }
    catch (error) {
      if (error.name === 'AbortError') throw error;
      throw new Error('Не удалось прочитать ответ сервера. Попробуй ещё раз.');
    }
  }
  async function recoverOne(id) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);
    try {
      const result = await request('recover', {requestId:id}, controller.signal);
      if (!['completed','cancelled'].includes(result?.status)) throw new Error('Не удалось проверить результат.');
      forget(id);
      return result.status;
    } finally { clearTimeout(timeout); }
  }
  return {
    async read(month, signal) {
      const data = await request('view', month ? {month} : {}, signal);
      const day = value => typeof value === 'string' && /^20\d{2}-\d{2}-\d{2}$/.test(value);
      if (!data || !day(data.today) || !/^20\d{2}-(0[1-9]|1[0-2])$/.test(data.month)
          || !['personal','group'].includes(data.scope)
          || !data.counts || Array.isArray(data.counts) || typeof data.counts !== 'object'
          || !Object.entries(data.counts).every(([date,n]) => day(date) && Number.isSafeInteger(n) && n >= 0)
          || !['total','best','current'].every(key => Number.isSafeInteger(data.stats?.[key]) && data.stats[key] >= 0)
          || !Array.isArray(data.events) || !data.events.every(e => e && typeof e.id === 'string'
            && typeof e.version === 'string' && day(e.date) && (e.time === null || typeof e.time === 'string')
            && (e.kind === null || Number.isInteger(e.kind) && e.kind >= 0 && e.kind <= 3)
            && (e.feeling === null || Number.isInteger(e.feeling) && e.feeling >= 0 && e.feeling <= 2))) {
        throw new Error('Не удалось прочитать данные календаря. Попробуй ещё раз.');
      }
      return data;
    },
    async create(mark, signal) {
      const result = await request('events', mark, signal);
      if (!result || typeof result.repeated !== 'boolean'
          || !(result.eventId === null || typeof result.eventId === 'string')) {
        throw new Error('Не удалось подтвердить сохранение. Проверяем результат.');
      }
      return result;
    },
    remember, forget, pendingIds, recoverOne,
    async recoverPending(inMemoryId) {
      const results = [];
      const ids = new Set(pendingIds());
      if (inMemoryId && uuid.test(inMemoryId)) ids.add(inMemoryId);
      for (const id of ids) results.push(await recoverOne(id));
      return results;
    },
  };
})();
