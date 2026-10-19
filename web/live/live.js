/* Credentials and record contents stay in memory. Only opaque request IDs persist. */
window.PoopLive = (() => {
  const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
  function prefix() {
    // Unverified fields only partition local markers; the server authorizes every call.
    const fields = new URLSearchParams(window.Telegram?.WebApp?.initData || '');
    const user = JSON.parse(fields.get('user') || '{}');
    if (!Number.isSafeInteger(user.id) || user.id <= 0) throw new Error('Открой приложение из Telegram.');
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
    if (!initData) throw new Error('Открой приложение из Telegram: кнопкой в личке или ссылкой /app из группы.');
    const response = await fetch('/poopcheck-api/' + path, {
      method: 'POST', cache: 'no-store', credentials: 'omit', signal,
      headers: {'Content-Type': 'application/json', Authorization: 'tma ' + initData},
      body: JSON.stringify(body),
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      const message = response.status === 401 ? 'Вход устарел. Закрой приложение и открой снова.'
        : response.status === 403 ? 'Нет доступа к этому чату. Открой свежую ссылку /app из группы, в которой состоишь.'
        : response.status === 429 ? 'Слишком много запросов. Попробуй чуть позже.'
        : response.status === 409 ? data.error
        : 'Не удалось подтвердить запрос. Попробуй ещё раз.';
      const error = new Error(message);
      error.status = response.status;
      error.definitive = response.status >= 400 && response.status < 500;
      throw error;
    }
    return response.json();
  }
  async function recoverOne(id) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);
    try {
      const result = await request('recover', {requestId:id}, controller.signal);
      if (!['completed','cancelled'].includes(result.status)) throw new Error('Не удалось проверить результат.');
      forget(id);
      return result.status;
    } finally { clearTimeout(timeout); }
  }
  return {
    read(month, signal) { return request('view', month ? {month} : {}, signal); },
    create(mark, signal) { return request('events', mark, signal); },
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
