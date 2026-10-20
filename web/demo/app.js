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
    saving = false,
    confirming = false,
    settlingMonth = false,
    calendarDrag = null,
    accessBlocked = false,
    retryFollowToday = true,
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
  function dayButton(date, full, selection = selected, counts = null) {
    const n = counts ? counts[date] || 0 : count(date),
      active = date === selection,
      future = date > today || (live && date < "2000-01-01");
    return `<button class="day ${n ? "has-events" : ""} ${active ? "selected" : ""}" data-date="${date}" aria-label="${date}${n ? ", отметок: " + n : ""}" aria-pressed="${active}" ${future ? "disabled" : ""}>${full ? `${n ? '<span class="poop-mark" aria-hidden="true"></span>' : ""}<span class="date-number">${Number(date.slice(-2))}</span>${n > 1 ? `<span class="badge">${n}</span>` : ""}` : `<span class="day-circle">${Number(date.slice(-2))}</span>`}</button>`;
  }
  function calendar(full, key = month, selection = selected, counts = null, pending = false) {
    let days = "";
    const date = M.parseDate(selection);
    let weekday = (date.getDay() + 6) % 7;
    if (full) {
      const start = key + "-01",
        offset = (M.parseDate(start).getDay() + 6) % 7,
        total = new Date(
          Number(key.slice(0, 4)),
          Number(key.slice(5)),
          0,
        ).getDate();
      days = "<span></span>".repeat(offset);
      for (let i = 1; i <= total; i++)
        days += dayButton(key + "-" + String(i).padStart(2, "0"), true, selection, counts);
    } else {
      const monday = M.addDays(selection, -weekday);
      for (let i = 0; i < 7; i++)
        days += dayButton(M.addDays(monday, i), false, selection, counts);
    }
    return `<section class="date-card" ${pending ? 'data-preview-pending="true"' : ""} aria-label="${full ? "Календарь" : "Неделя"}"><div class="date-heading">${full ? `<div class="month-nav"><button data-action="previous-month" aria-label="Предыдущий месяц" ${live && key <= "2000-01" ? "disabled" : ""}>${icon("left")}</button><button class="month-label" data-action="week" aria-label="Показать неделю">${monthName(key)}</button><button data-action="next-month" aria-label="Следующий месяц" ${key >= today.slice(0, 7) ? "disabled" : ""}>${icon("right")}</button></div>` : `<button class="month-button" data-action="calendar" aria-label="Открыть календарь">${monthName(selection.slice(0, 7))}${icon("down")}</button>`}<button class="icon-button" data-action="stats" aria-label="Статистика">${icon("stats")}</button></div><div class="weekdays" aria-hidden="true">${["пн", "вт", "ср", "чт", "пт", "сб", "вс"].map((d, i) => `<span class="${i === weekday ? "weekday-selected" : ""}">${d}</span>`).join("")}</div><div class="days ${full ? "month" : ""}">${days}</div></section>`;
  }
  function question() {
    const kind = screen === "kind",
      options = kind ? M.kinds : M.feelings;
    return `<div class="month-viewport week-viewport" ${!draft ? 'data-week-draggable="true"' : ""}>${calendar(false)}</div><section class="question"><h1>${kind ? `${icon("poop-face")}<span>Консистенция кала</span>` : "Мои ощущения"}</h1><div class="choices">${options.map((name, i) => `<button class="choice ${draft?.[kind ? "kind" : "feeling"] === i ? "selected" : ""}" data-choice="${i}">${name}</button>`).join("")}</div></section><button class="add" data-action="add" aria-label="Добавить отметку">${icon("add")}</button>`;
  }
  function history() {
    const rows = events
      .filter((e) => e.date === selected)
      .sort((a, b) => (a.time || "99:99").localeCompare(b.time || "99:99"));
    return `<div class="month-viewport">${calendar(true)}</div><section class="history"><h1>Итоги дня:</h1>${rows.length ? rows.map((e) => `<article class="event ${revealed === e.id ? "revealed" : ""}" data-event="${esc(e.id)}"><button class="delete" data-delete="${esc(e.id)}" aria-label="Удалить запись ${esc(e.time || "время не указано")}" ${revealed !== e.id ? 'tabindex="-1" aria-hidden="true"' : ""}>${icon("trash")}</button><div class="event-content"><div><time>${[e.date.slice(8), e.date.slice(5, 7), e.date.slice(2, 4)].join(".")} ${esc(e.time || "время не указано")}</time><div class="description">${esc([M.kinds[e.kind] || "Консистенция не указана", M.feelings[e.feeling]?.toLowerCase() || "ощущения не указаны"].join(", "))}</div></div><button class="edit" data-edit="${esc(e.id)}" aria-label="Изменить запись ${esc(e.time || "время не указано")}">${icon("edit")}</button></div></article>`).join("") : '<div class="empty">Нет отметок</div>'}</section>`;
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
    clearCalendarDrag();
    app.setAttribute("data-screen", screen);
    app.setAttribute("data-month-motion", "");
    updateBackButton();
    if (live && (loading || loadError || !snapshot)) {
      app.innerHTML =
        header() +
        (loadError
          ? accessBlocked
            ? `<div class="empty" role="alert">${esc(loadError)}</div>`
            : `<button class="empty" data-action="retry">${esc(loadError)}</button>`
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
  }
  function updateBackButton() {
    try {
      if (loading || saving || confirming || settlingMonth || pendingMark || loadError || (screen === "kind" && !draft)) tg?.BackButton.hide();
      else tg?.BackButton.show();
    } catch {}
  }
  function blockAccess(error) {
    if (error.status !== 401 && error.status !== 403) return false;
    accessBlocked = true;
    events = [];
    loadError = error.message;
    return true;
  }
  async function go(next, revalidate = false, followToday = true) {
    screen = next;
    revealed = null;
    if (live && revalidate) await refresh({followToday});
    else render();
    window.scrollTo(0, 0);
  }
  async function back() {
    if (saving || loading || confirming || settlingMonth || pendingMark || loadError) return;
    if (screen === "feeling") {
      go("kind");
      return;
    }
    if (screen === "stats") {
      await go(returnScreen, true);
      return;
    }
    const editing = Boolean(draft?.id);
    draft = null;
    if (editing) month = selected.slice(0, 7);
    await go(editing ? "calendar" : "kind", true, !editing);
  }
  async function fresh() {
    if (live) selected = today;
    draft = null;
    month = selected.slice(0, 7);
    await go("kind", true);
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
    if (saving || accessBlocked) return;
    if (!pendingMark) {
      const candidate = {requestId: crypto.randomUUID(), ...body};
      try { live.remember(candidate.requestId); }
      catch (error) { notice(error.message); return; }
      pendingMark = candidate;
    }
    saving = true;
    updateBackButton();
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
      await refresh({followToday: false});
    } catch (error) {
      if (blockAccess(error)) return; // Keep the receipt marker for a fresh Telegram login.
      try {
        const outcome = await live.recoverOne(pendingMark.requestId);
        pendingMark = null;
        draft = null;
        revealed = null;
        screen = "calendar";
        await refresh({followToday: false});
        notice(outcome === "completed" ? "Изменение сохранено. Результат подтверждён сервером."
          : error.definitive ? error.message : "Действие не успело выполниться. Повтори его — предыдущий запрос отменён.");
      } catch (recoveryError) {
        if (!blockAccess(recoveryError)) notice("Связь прервалась. Можно повторить действие или открыть приложение позже — результат будет проверен перед новой отметкой.");
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
  function adjacentMonth(delta) {
    const d = M.parseDate(month + "-01");
    d.setMonth(d.getMonth() + delta);
    const key = M.dateKey(d).slice(0, 7);
    return key > today.slice(0, 7) || (live && key < "2000-01") ? null : key;
  }
  function adjacentCalendar(delta) {
    if (screen === "calendar") {
      const key = adjacentMonth(delta);
      return key ? {month: key, selection: key === today.slice(0, 7) ? today : key + "-01", full: true} : null;
    }
    if (screen !== "kind" || draft) return null;
    const weekday = (M.parseDate(selected).getDay() + 6) % 7;
    const monday = M.addDays(selected, -weekday + delta * 7);
    if (monday > today || (live && M.addDays(monday, 6) < "2000-01-01")) return null;
    let selection = M.addDays(selected, delta * 7);
    if (selection > today) selection = today;
    if (live && selection < "2000-01-01") selection = "2000-01-01";
    return {month: selection.slice(0, 7), selection, full: false};
  }
  async function changeCalendar(delta, options = {}) {
    if (screen === "calendar") return changeMonth(delta, options);
    if (saving || loading || confirming || pendingMark || loadError) return;
    const next = adjacentCalendar(delta);
    if (!next) return;
    selected = next.selection;
    month = next.month;
    if (live) await refresh({followToday: false, prepared: options.prepared});
    else render();
  }
  async function changeMonth(delta, {prepared = null, animate = true} = {}) {
    if (screen !== "calendar" || saving || loading || confirming || pendingMark || loadError) return;
    const nextMonth = adjacentMonth(delta);
    if (!nextMonth) return;
    month = nextMonth;
    revealed = null;
    selected = month === today.slice(0, 7) ? today : month + "-01";
    if (live) {
      if (!await refresh({followToday: false, prepared})) return;
    } else render();
    if (animate) app.setAttribute("data-month-motion", delta > 0 ? "next" : "previous");
  }
  let suppressPointerClick = false;
  app.addEventListener("click", async (e) => {
    // A completed drag must not also select the date/button under its release.
    if (suppressPointerClick) {
      suppressPointerClick = false;
      if (e.detail !== 0) { e.preventDefault(); return; }
    }
    const b = e.target.closest("button");
    if (!b || b.disabled || saving || confirming || settlingMonth || accessBlocked) return;
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
      if (screen === "feeling") screen = "kind";
      month = selected.slice(0, 7);
      if (live) await refresh({followToday: false});
      else render();
      return;
    }
    if (b.dataset.edit) {
      if (live && !await refresh({followToday: false})) return;
      const event = events.find((e) => e.id === b.dataset.edit);
      if (event) {
        draft = { ...event };
        selected = event.date;
        go("kind");
      } else if (live) notice("Эта отметка уже удалена. Календарь обновлён.");
      return;
    }
    if (b.dataset.delete) {
      if (revealed !== b.dataset.delete) return;
      if (live) {
        const event = events.find(e => e.id === b.dataset.delete);
        if (!event) return;
        confirming = true;
        updateBackButton();
        let confirmed = false;
        try { confirmed = await confirmDelete(); }
        catch { notice("Не удалось открыть подтверждение. Попробуй ещё раз."); }
        finally { confirming = false; updateBackButton(); }
        if (confirmed)
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
        await fresh();
        break;
      case "calendar":
        month = selected.slice(0, 7);
        draft = null;
        await go("calendar", true);
        break;
      case "week":
        draft = null;
        await go("kind", true);
        break;
      case "stats":
        returnScreen = screen;
        await go("stats", true);
        break;
      case "back":
        await back();
        break;
      case "previous-month":
      case "next-month": {
        await changeMonth(b.dataset.action === "previous-month" ? -1 : 1);
        break;
      }
    }
  });
  // The adjacent card is a read-only preview. Its response is reused on commit;
  // moving the pointer never starts repeated requests or changes selected data.
  function clearCalendarDrag(keep = null) {
    const drag = calendarDrag;
    calendarDrag = null;
    settlingMonth = false;
    if (!drag) return;
    for (const animation of drag.animations) animation.cancel();
    drag.card.style.transform = "";
    for (const neighbor of drag.neighbors.values()) {
      if (neighbor !== keep) neighbor.controller?.abort();
      neighbor.element.remove();
    }
    app.setAttribute("data-month-drag", "");
  }
  function dragNeighbor(drag, delta) {
    const next = adjacentCalendar(delta);
    if (!next) return null;
    const {month: key, selection, full} = next;
    if (drag.neighbors.has(delta)) return drag.neighbors.get(delta);
    const element = document.createElement("div");
    element.className = "month-drag-neighbor";
    element.setAttribute("aria-hidden", "true");
    element.setAttribute("inert", "");
    element.innerHTML = calendar(full, key, selection, live ? {} : null, Boolean(live));
    drag.viewport.appendChild(element);
    const neighbor = {element, month: key, controller: live ? new AbortController() : null};
    drag.neighbors.set(delta, neighbor);
    if (live) {
      const timeout = setTimeout(() => neighbor.controller.abort(), 15000);
      neighbor.promise = live.read(key, neighbor.controller.signal).then(data => {
        if (calendarDrag === drag) element.innerHTML = calendar(full, key, selection, data.counts);
        return data;
      }).finally(() => clearTimeout(timeout));
      // Cancellation or an unavailable preview must never produce an unhandled rejection.
      neighbor.promise.catch(() => {
        if (calendarDrag === drag) element.setAttribute("data-preview-error", "true");
      });
    }
    return neighbor;
  }
  function moveCalendarDrag(dx) {
    if (!calendarDrag) {
      const viewport = app.querySelector(".month-viewport"), card = viewport?.querySelector(".date-card");
      if (!card) return;
      app.setAttribute("data-month-motion", "");
      calendarDrag = {viewport, card, width: viewport.getBoundingClientRect().width,
        offset: 0, neighbors: new Map(), animations: []};
      app.setAttribute("data-month-drag", "true");
    }
    const drag = calendarDrag, delta = dx < 0 ? 1 : -1;
    const neighbor = dragNeighbor(drag, delta);
    drag.delta = delta;
    drag.offset = neighbor ? Math.max(-drag.width, Math.min(drag.width, dx))
      : Math.sign(dx) * Math.min(Math.abs(dx) * 0.22, drag.width * 0.15);
    drag.card.style.transform = `translateX(${drag.offset}px)`;
    for (const [direction, entry] of drag.neighbors) {
      entry.element.style.visibility = direction === delta ? "visible" : "hidden";
      entry.element.style.transform = `translateX(${drag.offset + direction * drag.width}px)`;
    }
  }
  async function finishCalendarDrag(commit, delta) {
    const drag = calendarDrag;
    if (!drag) { if (commit) await changeCalendar(delta); return; }
    settlingMonth = true;
    updateBackButton();
    const neighbor = drag.neighbors.get(delta), target = commit ? -delta * drag.width : 0;
    const duration = window.matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 200;
    const slide = (element, from, to) => {
      const animation = element.animate?.([{transform: `translateX(${from}px)`},
        {transform: `translateX(${to}px)`}], {duration, easing: "cubic-bezier(.2,.8,.2,1)", fill: "forwards"});
      if (animation) { drag.animations.push(animation); return animation.finished.catch(() => {}); }
      element.style.transform = `translateX(${to}px)`;
    };
    await Promise.all([slide(drag.card, drag.offset, target),
      neighbor && slide(neighbor.element, drag.offset + delta * drag.width, target + delta * drag.width)]);
    if (calendarDrag !== drag) return;
    if (commit) {
      // Keep the incoming card visible while its data arrives, rather than
      // snapping back to the outgoing month or displaying a false empty month.
      if (neighbor?.promise) await neighbor.promise.catch(() => {});
      if (calendarDrag !== drag) return;
      clearCalendarDrag(neighbor);
      await changeCalendar(delta, {prepared: neighbor, animate: false});
    } else clearCalendarDrag();
    updateBackButton();
  }
  let pointer = null;
  function resetPointer() {
    const old = pointer;
    pointer = null;
    if (old && app.hasPointerCapture?.(old.pointerId)) app.releasePointerCapture(old.pointerId);
  }
  app.addEventListener("pointerdown", (e) => {
    if (settlingMonth) return;
    resetPointer();
    clearCalendarDrag();
    suppressPointerClick = false;
    if (e.isPrimary === false || e.button !== 0) return;
    if (saving || loading || confirming || settlingMonth || pendingMark || loadError) return;
    const row = e.target.closest("[data-event]");
    if (!row && screen !== "calendar" &&
        !(screen === "kind" && !draft && e.target.closest("[data-week-draggable]"))) return;
    pointer = { id: row?.dataset.event, pointerId: e.pointerId,
      x: e.clientX, y: e.clientY, lastX: e.clientX, lastTime: e.timeStamp,
      velocity: 0, axis: null, screen, month, selected };
  });
  app.addEventListener("pointermove", (e) => {
    if (!pointer || e.pointerId !== pointer.pointerId) return;
    const dx = Math.abs(e.clientX - pointer.x), dy = Math.abs(e.clientY - pointer.y);
    if (!pointer.axis) {
      if (dy > 10 && dy >= dx) pointer.axis = "vertical";
      else if (dx > 10 && dx > dy * 1.5) {
        pointer.axis = "horizontal";
        app.setPointerCapture?.(e.pointerId);
      }
    }
    const elapsed = e.timeStamp - pointer.lastTime;
    if (elapsed > 0) pointer.velocity = (e.clientX - pointer.lastX) / elapsed;
    pointer.lastX = e.clientX;
    pointer.lastTime = e.timeStamp;
    if (pointer.axis === "horizontal" && !pointer.id) moveCalendarDrag(e.clientX - pointer.x);
  });
  app.addEventListener("pointerup", async (e) => {
    if (!pointer || e.pointerId !== pointer.pointerId) return;
    const gesture = pointer;
    resetPointer();
    if (saving || loading || confirming || pendingMark || loadError ||
        gesture.screen !== screen || gesture.month !== month || gesture.selected !== selected || gesture.axis === "vertical") return;
    const dx = e.clientX - gesture.x, dy = e.clientY - gesture.y;
    if (gesture.id) {
      if (Math.abs(dx) <= 45 || Math.abs(dx) <= Math.abs(dy) * 1.5) return;
      suppressPointerClick = true;
      revealed = dx < 0 ? gesture.id : null;
      render();
    } else {
      const horizontal = gesture.axis === "horizontal" || (Math.abs(dx) > 10 && Math.abs(dx) > Math.abs(dy) * 1.5);
      if (!horizontal) { clearCalendarDrag(); return; }
      suppressPointerClick = true;
      moveCalendarDrag(dx);
      const delta = dx < 0 ? 1 : -1, width = calendarDrag?.width || 320;
      const fast = Math.abs(dx) >= 24 && e.timeStamp - gesture.lastTime < 100 &&
        Math.sign(gesture.velocity) === Math.sign(dx) && Math.abs(gesture.velocity) >= 0.5;
      const commit = Boolean(adjacentCalendar(delta)) && (Math.abs(dx) >= Math.max(48, width * 0.24) || fast);
      await finishCalendarDrag(commit, delta);
    }
  });
  function cancelPointer(e) {
    if (!pointer || (e && e.pointerId !== pointer.pointerId)) return;
    if (pointer.axis === "horizontal") suppressPointerClick = true;
    resetPointer();
    finishCalendarDrag(false, calendarDrag?.delta || 1);
  }
  app.addEventListener("pointercancel", cancelPointer);
  app.addEventListener("lostpointercapture", (e) => {
    // Touch implicitly captures the initial child. When horizontal dragging
    // transfers capture to app, the child's loss bubbles here; it is not an
    // interruption. Only losing our own capture cancels the active gesture.
    if (e.target === app) cancelPointer(e);
  });
  window.addEventListener("blur", () => { resetPointer(); clearCalendarDrag(); updateBackButton(); });
  window.addEventListener("resize", () => { resetPointer(); clearCalendarDrag(); updateBackButton(); });
  app.addEventListener("dragstart", (e) => {
    if (pointer) e.preventDefault();
  });
  app.addEventListener("keydown", (e) => {
    if (saving || loading || confirming || settlingMonth || pendingMark || loadError) return;
    const row = e.target.closest("[data-event]");
    if (row && e.key === "ArrowLeft") {
      revealed = row.dataset.event;
      render();
      app.querySelector(`[data-delete="${revealed}"]`)?.focus();
    }
    if (e.key === "Escape") {
      resetPointer();
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
  async function refresh({followToday = true, prepared = null} = {}) {
    if (!live || accessBlocked) return false;
    retryFollowToday = followToday;
    const number = ++requestNumber;
    activeRequest?.abort();
    const controller = new AbortController();
    activeRequest = controller;
    loading = true;
    loadError = "";
    render();
    const timeout = setTimeout(() => controller.abort(), 15000);
    try {
      // Follow today across midnight only when no unfinished form is bound to it.
      const requestedMonth = !snapshot || (followToday && !draft && selected === today) ? null : month;
      const data = await (prepared?.month === requestedMonth ? prepared.promise : live.read(requestedMonth, controller.signal));
      if (number !== requestNumber) return;
      const oldToday = today;
      today = data.today;
      if (!snapshot || (followToday && !draft && selected === oldToday)) selected = today;
      month = data.month;
      snapshot = data;
      app.setAttribute("aria-label", data.scope === "personal" ? "PoopCheck — личные отметки" : "PoopCheck — отметки в группе");
      events = data.events;
      return true;
    } catch (error) {
      if (number !== requestNumber) return;
      if (blockAccess(error)) return false;
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
    if (saving || accessBlocked) return;
    saving = true;
    loading = true;
    loadError = "";
    render();
    try {
      const outcomes = await live.recoverPending(pendingMark?.requestId);
      pendingMark = null;
      if (outcomes.length) {
        draft = null;
        revealed = null;
        screen = "calendar";
      }
      const refreshed = await refresh({followToday: retryFollowToday});
      if (outcomes.length) notice(outcomes.includes("cancelled")
        ? "Результаты проверены. Невыполненные действия отменены — при необходимости повтори их."
        : refreshed ? "Предыдущее действие сохранено. Календарь обновлён."
        : "Предыдущее действие сохранено, но календарь не загрузился. Следуй подсказке на экране.");
      tg?.disableClosingConfirmation?.();
    } catch (error) {
      loading = false;
      if (!blockAccess(error)) loadError = "Не удалось проверить предыдущее действие. Проверь связь и нажми, чтобы повторить.";
    } finally {
      saving = false;
      render();
    }
  }
  function resume() {
    if (document.visibilityState !== "visible") {
      resetPointer();
      clearCalendarDrag();
      return;
    }
    if (accessBlocked || confirming || settlingMonth || pointer) return;
    if (live && !saving && !loading && (pendingMark || loadError || live.pendingIds().length)) {
      startLive();
      return;
    }
    if (live && !loading && !saving && !pendingMark) {
      // Event-driven revalidation, including unfinished forms. Keep their values
      // and original revision so a concurrent edit still conflicts on save.
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
