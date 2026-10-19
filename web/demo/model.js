/* Isolated demonstration data. No bot IDs, credentials or production API. */
(function (root) {
  "use strict";
  const TODAY = "2026-06-23",
    KEY = "poopcheck.design-demo.v1";
  const kinds = ["Жестко", "Норма", "Мягко", "Водичка"];
  const feelings = ["Прекрасно", "Сойдёт", "Ужасно"];
  function dateKey(date) {
    return [
      date.getFullYear(),
      String(date.getMonth() + 1).padStart(2, "0"),
      String(date.getDate()).padStart(2, "0"),
    ].join("-");
  }
  function parseDate(key) {
    return new Date(key + "T12:00:00");
  }
  function addDays(key, n) {
    const d = parseDate(key);
    d.setDate(d.getDate() + n);
    return dateKey(d);
  }
  function seed() {
    const result = [];
    let id = 0;
    function day(date, n) {
      for (let i = 0; i < n; i++)
        result.push({
          id: "seed-" + ++id,
          date,
          time: ["10:25", "14:36", "21:40"][i % 3],
          kind: i % 4,
          feeling: i % 3,
        });
    }
    // A completed 42-day series and a separate current 24-day series.
    for (let i = 0; i < 42; i++) day(addDays("2026-03-01", i), 3);
    for (let i = 0; i < 23; i++) day(addDays("2026-05-31", i), i === 0 ? 1 : 3);
    day(TODAY, 3);
    const today = result.filter((e) => e.date === TODAY);
    today[0].kind = 1;
    today[0].feeling = 0;
    today[1].kind = 0;
    today[1].feeling = 1;
    today[2].kind = 3;
    today[2].feeling = 2;
    return result;
  }
  function valid(events) {
    return (
      Array.isArray(events) &&
      events.length < 10000 &&
      new Set(events.map((e) => e?.id)).size === events.length &&
      events.every(
        (e) =>
          e &&
          typeof e.id === "string" &&
          /^[\w-]{1,80}$/.test(e.id) &&
          /^\d{4}-\d{2}-\d{2}$/.test(e.date) &&
          dateKey(parseDate(e.date)) === e.date &&
          /^([01]\d|2[0-3]):[0-5]\d$/.test(e.time) &&
          Number.isInteger(e.kind) &&
          e.kind >= 0 &&
          e.kind <= 3 &&
          Number.isInteger(e.feeling) &&
          e.feeling >= 0 &&
          e.feeling <= 2,
      )
    );
  }
  function load(storage) {
    try {
      const v = JSON.parse(storage.getItem(KEY));
      if (v?.version === 1 && valid(v.events)) return v.events;
    } catch {}
    return seed();
  }
  function save(storage, events) {
    try {
      storage.setItem(KEY, JSON.stringify({ version: 1, events }));
      return true;
    } catch {
      return false;
    }
  }
  function stats(events) {
    const dates = [...new Set(events.map((e) => e.date))].sort();
    let best = 0,
      run = 0,
      prev = null;
    for (const d of dates) {
      run = prev && addDays(prev, 1) === d ? run + 1 : 1;
      best = Math.max(best, run);
      prev = d;
    }
    const set = new Set(dates);
    let current = 0;
    let d = set.has(TODAY) ? TODAY : addDays(TODAY, -1);
    while (set.has(d)) {
      current++;
      d = addDays(d, -1);
    }
    return { total: events.length, best, current };
  }
  const model = {
    TODAY,
    KEY,
    kinds,
    feelings,
    dateKey,
    parseDate,
    addDays,
    seed,
    valid,
    load,
    save,
    stats,
  };
  root.PoopDemo = model;
  if (typeof module !== "undefined") module.exports = model;
})(typeof window !== "undefined" ? window : globalThis);
