/* Real records stay in memory. Telegram credentials never go into URLs or storage. */
window.PoopLive = {
  async read(month, signal) {
    const initData = window.Telegram?.WebApp?.initData;
    if (!initData)
      throw new Error("Открой приложение через кнопку в личке бота.");
    const response = await fetch("/poopcheck-api/view", {
      method: "POST",
      cache: "no-store",
      credentials: "omit",
      signal,
      headers: {
        "Content-Type": "application/json",
        Authorization: "tma " + initData,
      },
      body: JSON.stringify(month ? { month } : {}),
    });
    if (response.status === 401)
      throw new Error(
        "Вход устарел. Закрой приложение и открой его снова из бота.",
      );
    if (response.status === 403)
      throw new Error(
        "Эта версия пока доступна только владельцу бота для выбранного чата.",
      );
    if (response.status === 429)
      throw new Error("Слишком много запросов. Попробуй чуть позже.");
    if (!response.ok)
      throw new Error("Не удалось загрузить данные. Нажми, чтобы повторить.");
    return response.json();
  },
};
