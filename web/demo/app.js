(function () {
  "use strict";
  const live =
    document.documentElement.dataset.mode === "live"
      ? window.PoopLive || {
          async read() {
            throw new Error(
              "Не удалось загрузить приложение. Закрой и открой его снова.",
            );
          },
        }
      : null;
  const assetRoot = live ? "/poopcheck-demo/" : "./";
  const M = window.PoopDemo,
    app = document.getElementById("app"),
    announcer = document.getElementById("announcer");
  let storage;
  try {
    storage = live ? null : window.localStorage;
  } catch {
    storage = null;
  }
  let today = M.TODAY;
  let snapshot = null,
    loading = Boolean(live),
    loadError = "",
    requestNumber = 0,
    activeRequest = null,
    lastRead = 0,
    saving = false,
    pendingMark = null;
  let events = live ? [] : M.load(storage),
    screen = "kind",
    selected = today,
    month = today.slice(0, 7),
    draft = null,
    returnScreen = "kind",
    revealed = null;
  const tg = window.Telegram?.WebApp;
  // Telegram Desktop uses a short webview. Keep the phone layout unchanged.
  const platform = tg?.platform || "unknown";
  const desktop =
    ["tdesktop", "macos", "web", "weba", "webk"].includes(platform) ||
    (platform === "unknown" &&
      window.matchMedia("(hover: hover) and (pointer: fine)").matches);
  document.documentElement.dataset.layout = desktop ? "desktop" : "mobile";
  try {
    tg?.ready();
    tg?.expand();
    tg?.setHeaderColor("#ffffff");
    tg?.setBackgroundColor("#ffffff");
    tg?.setBottomBarColor?.("#ffffff");
  } catch {}
  const icon = (name) =>
    `<img class="icon icon-${name}" src="${assetRoot}assets/${name}.svg" alt="" aria-hidden="true" width="24" height="24">`;
  const esc = (value) =>
    String(value).replace(
      /[&<>"']/g,
      (c) =>
        ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;",
        })[c],
    );
  const monthName = (key) =>
    new Intl.DateTimeFormat("ru", { month: "long", year: "numeric" })
      .format(M.parseDate(key + "-01"))
      .replace(/\s*г\.?$/, "");
  const count = (date) =>
    live
      ? snapshot?.counts[date] || 0
      : events.filter((e) => e.date === date).length;
  function announce(text) {
    announcer.textContent = text;
  }
  function persist() {
    if (live) return;
    const saved = M.save(storage, events);
    announce(
      saved
        ? "Демонстрационная запись сохранена"
        : "Запись сохранена до закрытия приложения",
    );
  }
  function header() {
    return `<header class="header"><button class="icon-button menu" data-action="home" aria-label="Главная">${icon("menu")}</button><div class="brand"><img src="${assetRoot}assets/logo.svg" alt="PoopCheck" width="203" height="47"></div></header>`;
  }
  function dayButton(date, full) {
    const n = count(date),
      active = date === selected,
      future = date > today;
    return `<button class="day ${n ? "has-events" : ""} ${active ? "selected" : ""}" data-date="${date}" aria-label="${date}${n ? ", отметок: " + n : ""}" aria-pressed="${active}" ${future ? "disabled" : ""}>${full ? `${n ? '<span class="poop-mark" aria-hidden="true"></span>' : ""}<span class="date-number">${Number(date.slice(-2))}</span>${n > 1 ? `<span class="badge">${n}</span>` : ""}` : `<span class="day-circle">${Number(date.slice(-2))}</span>`}</button>`;
  }
  function calendar(full) {
    let days = "";
    const date = M.parseDate(selected);
    let weekday = (date.getDay() + 6) % 7;
    if (full) {
      const start = month + "-01",
        offset = (M.parseDate(start).getDay() + 6) % 7,
        total = new Date(
          Number(month.slice(0, 4)),
          Number(month.slice(5)),
          0,
        ).getDate();
      days = "<span></span>".repeat(offset);
      for (let i = 1; i <= total; i++)
        days += dayButton(month + "-" + String(i).padStart(2, "0"), true);
    } else {
      const monday = M.addDays(selected, -weekday);
      for (let i = 0; i < 7; i++)
        days += dayButton(M.addDays(monday, i), false);
    }
    return `<section class="date-card" aria-label="${full ? "Календарь" : "Неделя"}"><div class="date-heading">${full ? `<div class="month-nav"><button data-action="previous-month" aria-label="Предыдущий месяц">${icon("left")}</button><button class="month-label" data-action="week" aria-label="Показать неделю">${monthName(month)}</button><button data-action="next-month" aria-label="Следующий месяц" ${month >= today.slice(0, 7) ? "disabled" : ""}>${icon("right")}</button></div>` : `<button class="month-button" data-action="calendar" aria-label="Открыть календарь">${monthName(selected.slice(0, 7))}${icon("down")}</button>`}<button class="icon-button" data-action="stats" aria-label="Статистика">${icon("stats")}</button></div><div class="weekdays" aria-hidden="true">${["пн", "вт", "ср", "чт", "пт", "сб", "вс"].map((d, i) => `<span class="${i === weekday ? "weekday-selected" : ""}">${d}</span>`).join("")}</div><div class="days ${full ? "month" : ""}">${days}</div></section>`;
  }
  function question() {
    const kind = screen === "kind",
      options = kind ? M.kinds : M.feelings;
    return `${calendar(false)}<section class="question"><h1>${kind ? `${icon("poop-face")}<span>Консистенция кала</span>` : "Мои ощущения"}</h1><div class="choices">${options.map((name, i) => `<button class="choice ${draft?.[kind ? "kind" : "feeling"] === i ? "selected" : ""}" data-choice="${i}">${name}</button>`).join("")}</div></section><button class="add" data-action="add" aria-label="Добавить отметку">${icon("add")}</button>`;
  }
  function history() {
    const rows = events
      .filter((e) => e.date === selected)
      .sort((a, b) => (a.time || "99:99").localeCompare(b.time || "99:99"));
    return `${calendar(true)}<section class="history"><h1>Итоги дня:</h1>${rows.length ? rows.map((e) => `<article class="event ${revealed === e.id ? "revealed" : ""}" data-event="${esc(e.id)}"><button class="delete" data-delete="${esc(e.id)}" aria-label="Удалить запись ${esc(e.time || "время не указано")}" ${revealed !== e.id ? 'tabindex="-1" aria-hidden="true"' : ""}>${icon("trash")}</button><div class="event-content"><div><time>${[e.date.slice(8), e.date.slice(5, 7), e.date.slice(2, 4)].join(".")} ${esc(e.time || "время не указано")}</time><div class="description">${esc([M.kinds[e.kind] || "Консистенция не указана", M.feelings[e.feeling]?.toLowerCase() || "ощущения не указаны"].join(", "))}</div></div><button class="edit" data-edit="${esc(e.id)}" aria-label="Изменить запись ${esc(e.time || "время не указано")}">${icon("edit")}</button></div></article>`).join("") : '<div class="empty">Нет отметок</div>'}</section>`;
  }
  function statistics() {
    const s = live ? snapshot.stats : M.stats(events),
      day = (M.parseDate(selected).getDay() + 6) % 7,
      start = M.addDays(selected, -day),
      counts = Array.from({ length: 7 }, (_, i) => count(M.addDays(start, i))),
      max = Math.max(3, ...counts);
    return `<section class="statistics"><button class="back" data-action="back" aria-label="Назад">${icon("left")}</button><div class="metrics"><div class="metric"><strong>${s.total}</strong><span>всего кала</span></div><div class="metric"><strong>${s.best}</strong><span>лучшая серия</span></div></div><div class="streak"><div class="round-icon">${icon("award")}</div><div><div class="streak-title">Ударный режим!</div><div class="streak-days">${s.current} ${plural(s.current)}</div></div></div><div class="dynamics"><div class="dynamics-heading"><div class="round-icon">${icon("stats-light")}</div><div><div class="dynamics-title">Динамика</div><div class="dynamics-subtitle">Результат за неделю</div></div></div><div class="chart" aria-label="Отметки за неделю">${counts.map((n, i) => `<div class="chart-column" aria-label="${M.addDays(start, i)}: ${n}"><span>${["пн", "вт", "ср", "чт", "пт", "сб", "вс"][i]}</span><div class="bar"><div class="bar-fill" style="height:${(100 * n) / max}%"></div></div><span>${n}</span></div>`).join("")}</div></div><div class="stat-placeholder" aria-hidden="true"></div></section>`;
  }
  function plural(n) {
    return n % 10 === 1 && n % 100 !== 11
      ? "день"
      : n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 12 || n % 100 > 14)
        ? "дня"
        : "дней";
  }
  function render() {
    if (live && (loading || loadError || !snapshot)) {
      app.innerHTML =
        header() +
        (loadError
          ? `<button class="empty" data-action="retry">${esc(loadError)}</button>`
          : '<div class="empty" role="status">Загрузка…</div>');
      return;
    }
    app.innerHTML =
      header() +
      (screen === "calendar"
        ? history()
        : screen === "stats"
          ? statistics()
          : question());
    try {
      if (screen === "kind") tg?.BackButton.hide();
      else tg?.BackButton.show();
    } catch {}
  }
  function go(next) {
    screen = next;
    revealed = null;
    render();
    window.scrollTo(0, 0);
  }
  function back() {
    if (saving || pendingMark) return;
    if (screen === "feeling") {
      go("kind");
      return;
    }
    go(screen === "stats" ? returnScreen : "kind");
  }
  function fresh() {
    if (live) selected = today;
    draft = null;
    go("kind");
    announce("Выберите консистенцию и ощущения");
  }
  async function commit(feeling) {
    if (live) {
      await mutate(draft?.id ? {
        action: "edit", eventId: draft.id, version: draft.version,
        date: draft.date, kind: draft.kind, feeling,
      } : {date: selected, kind: draft.kind, feeling});
      return;
    }
    const editing = draft?.id,
      event = {
        id:
          editing ||
          "demo-" +
            (globalThis.crypto?.randomUUID?.() ||
              Date.now() + "-" + Math.random().toString(36).slice(2)),
        date: selected,
        time:
          draft?.time ||
          new Date().toLocaleTimeString("ru", {
            hour: "2-digit",
            minute: "2-digit",
          }),
        kind: draft.kind,
        feeling,
      };
    if (editing) {
      events = events.map((e) => (e.id === editing ? event : e));
    } else events.push(event);
    persist();
    draft = null;
    month = selected.slice(0, 7);
    go("calendar");
    try {
      tg?.HapticFeedback.notificationOccurred("success");
    } catch {}
  }
  async function mutate(body) {
    if (saving) return;
    if (!pendingMark) {
      const candidate = {requestId: crypto.randomUUID(), ...body};
      try { live.remember(candidate.requestId); }
      catch (error) { notice(error.message); return; }
      pendingMark = candidate;
    }
    saving = true;
    app.querySelectorAll("button").forEach(b => { b.disabled = true; });
    tg?.enableClosingConfirmation?.();
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);
    const deleting = pendingMark.action === "delete";
    try {
      await live.create(pendingMark, controller.signal);
      live.forget(pendingMark.requestId);
      selected = pendingMark.date;
      month = selected.slice(0, 7);
      pendingMark = null;
      draft = null;
      revealed = null;
      screen = "calendar";
      announce(deleting ? "Отметка удалена" : "Отметка сохранена");
      try { tg?.HapticFeedback?.notificationOccurred("success"); } catch {}
      await refresh();
    } catch (error) {
      try {
        const outcome = await live.recoverOne(pendingMark.requestId);
        pendingMark = null;
        draft = null;
        revealed = null;
        screen = "calendar";
        await refresh();
        notice(outcome === "completed" ? "Изменение сохранено. Результат подтверждён сервером."
          : error.definitive ? error.message : "Действие не успело выполниться. Повтори его — предыдущий запрос отменён.");
      } catch (recoveryError) {
        notice(recoveryError.status === 401 || recoveryError.status === 403 ? recoveryError.message
          : "Связь прервалась. Можно повторить действие или открыть приложение позже — результат будет проверен перед новой отметкой.");
      }
    } finally {
      clearTimeout(timeout);
      saving = false;
      if (!pendingMark) tg?.disableClosingConfirmation?.();
      render();
    }
  }
  function confirmDelete() {
    return new Promise(resolve => {
      if (tg?.isVersionAtLeast?.("6.2"))
        tg.showConfirm("Удалить эту отметку? Статистика и серия будут пересчитаны.", resolve);
      else resolve(window.confirm("Удалить эту отметку? Статистика и серия будут пересчитаны."));
    });
  }
  app.addEventListener("click", async (e) => {
    const b = e.target.closest("button");
    if (!b || b.disabled || saving) return;
    if (b.dataset.action === "retry" && live) { await startLive(); return; }
    if (pendingMark) {
      const retry = pendingMark.action === "delete"
        ? b.dataset.delete === pendingMark.eventId
        : screen === "feeling" && b.dataset.choice !== undefined;
      if (retry) await mutate(pendingMark);
      else notice("Сначала повтори неподтверждённое действие.");
      return;
    }
    if (live && (!snapshot || loading || loadError)) return;
    if (b.dataset.choice !== undefined) {
      const value = Number(b.dataset.choice);
      if (live && !draft?.id && selected !== today) {
        notice("Добавить отметку можно только за сегодня. Для прошлых дней используй восстановление в боте.");
        return;
      }
      if (screen === "kind") {
        draft = { ...draft, kind: value };
        go("feeling");
      } else if (draft) await commit(value);
      return;
    }
    if (b.dataset.date) {
      selected = b.dataset.date;
      draft = null;
      revealed = null;
      if (screen === "feeling") go("kind");
      else if (live && selected.slice(0, 7) !== month) {
        month = selected.slice(0, 7);
        await refresh();
      } else render();
      return;
    }
    if (b.dataset.edit) {
      const event = events.find((e) => e.id === b.dataset.edit);
      if (event) {
        draft = { ...event };
        selected = event.date;
        go("kind");
      }
      return;
    }
    if (b.dataset.delete) {
      if (revealed !== b.dataset.delete) return;
      if (live) {
        const event = events.find(e => e.id === b.dataset.delete);
        if (event && await confirmDelete())
          await mutate({action: "delete", eventId: event.id, version: event.version, date: event.date});
        return;
      }
      events = events.filter((e) => e.id !== b.dataset.delete);
      persist();
      revealed = null;
      render();
      announce("Демонстрационная запись удалена");
      return;
    }
    switch (b.dataset.action) {
      case "home":
      case "add":
        fresh();
        break;
      case "calendar":
        month = selected.slice(0, 7);
        draft = null;
        go("calendar");
        break;
      case "week":
        draft = null;
        go("kind");
        break;
      case "stats":
        returnScreen = screen;
        go("stats");
        break;
      case "back":
        back();
        break;
      case "previous-month":
      case "next-month": {
        const d = M.parseDate(month + "-01");
        d.setMonth(
          d.getMonth() + (b.dataset.action === "previous-month" ? -1 : 1),
        );
        month = M.dateKey(d).slice(0, 7);
        if (live) {
          selected = month === today.slice(0, 7) ? today : month + "-01";
          await refresh();
        } else render();
        break;
      }
    }
  });
  let pointer = null;
  app.addEventListener("pointerdown", (e) => {
    if (saving || pendingMark) return;
    const row = e.target.closest("[data-event]");
    pointer = row
      ? { id: row.dataset.event, x: e.clientX, y: e.clientY }
      : null;
  });
  app.addEventListener("pointerup", (e) => {
    if (!pointer || saving || pendingMark) return;
    const dx = e.clientX - pointer.x,
      dy = e.clientY - pointer.y;
    if (Math.abs(dx) > 45 && Math.abs(dy) < 35) {
      revealed = dx < 0 ? pointer.id : null;
      render();
    }
    pointer = null;
  });
  app.addEventListener("pointercancel", () => {
    pointer = null;
  });
  app.addEventListener("keydown", (e) => {
    if (saving || pendingMark) return;
    const row = e.target.closest("[data-event]");
    if (row && e.key === "ArrowLeft") {
      revealed = row.dataset.event;
      render();
      app.querySelector(`[data-delete="${revealed}"]`)?.focus();
    }
    if (e.key === "Escape") {
      revealed = null;
      render();
    }
  });
  try {
    tg?.BackButton.onClick(back);
  } catch {}
  function notice(text) {
    if (tg?.isVersionAtLeast?.("6.2")) tg.showAlert(text);
    else window.alert(text);
  }
  async function refresh() {
    if (!live) return;
    const number = ++requestNumber;
    activeRequest?.abort();
    const controller = new AbortController();
    activeRequest = controller;
    loading = true;
    loadError = "";
    render();
    const timeout = setTimeout(() => controller.abort(), 15000);
    try {
      const data = await live.read(snapshot ? month : null, controller.signal);
      if (number !== requestNumber) return;
      const oldToday = today;
      today = data.today;
      if (!snapshot || selected === oldToday) selected = today;
      month = data.month;
      snapshot = data;
      app.setAttribute("aria-label", data.scope === "personal" ? "PoopCheck — личные отметки" : "PoopCheck — отметки в группе");
      events = data.events;
      lastRead = Date.now();
    } catch (error) {
      if (number !== requestNumber) return;
      events = [];
      loadError =
        error.name === "AbortError"
          ? "Не удалось загрузить данные. Нажми, чтобы повторить."
          : error.message;
    } finally {
      clearTimeout(timeout);
      if (number === requestNumber) {
        loading = false;
        activeRequest = null;
        render();
      }
    }
  }
  async function startLive() {
    if (saving) return;
    saving = true;
    loading = true;
    loadError = "";
    render();
    try {
      const outcomes = await live.recoverPending(pendingMark?.requestId);
      pendingMark = null;
      draft = null;
      revealed = null;
      if (outcomes.length) screen = "calendar";
      await refresh();
      if (outcomes.length) notice(outcomes.includes("cancelled")
        ? "Результаты проверены. Невыполненные действия отменены — при необходимости повтори их. Календарь показывает сохранённые отметки."
        : "Предыдущее действие сохранено. Календарь обновлён.");
      tg?.disableClosingConfirmation?.();
    } catch (error) {
      loading = false;
      loadError = error.status === 401 || error.status === 403 ? error.message
        : "Не удалось проверить предыдущее действие. Проверь связь и нажми, чтобы повторить.";
    } finally {
      saving = false;
      render();
    }
  }
  function resume() {
    if (document.visibilityState !== "visible") return;
    if (live && !saving && !loading && (pendingMark || loadError || live.pendingIds().length)) {
      startLive();
      return;
    }
    if (
      live &&
      !loading && !saving && !draft && !pendingMark &&
      document.visibilityState === "visible" &&
      Date.now() - lastRead > 5000
    ) {
      if (selected === today) month = null; // Server chooses the current month, including at midnight.
      refresh();
    }
  }
  if (live) {
    document.addEventListener("visibilitychange", resume);
    tg?.onEvent?.("activated", resume);
    window.addEventListener("online", resume);
    startLive();
  } else render();
})();
