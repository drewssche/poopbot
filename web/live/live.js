/* Real records and Telegram credentials stay in memory. */
window.PoopLive = (() => {
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
      error.definitive = response.status >= 400 && response.status < 500;
      throw error;
    }
    return response.json();
  }
  return {
    read(month, signal) { return request('view', month ? {month} : {}, signal); },
    create(mark, signal) { return request('events', mark, signal); },
  };
})();
