const { test } = require("node:test");
const assert = require("node:assert/strict");
const M = require("../web/demo/model.js");

test("demo matches the three counters in the design", () => {
  const events = M.seed();
  assert.ok(M.valid(events));
  assert.deepEqual(M.stats(events), { total: 196, best: 42, current: 24 });
});
test("editing/deleting demo events keeps streaks and counters consistent", () => {
  const events = M.seed();
  assert.equal(M.stats(events.slice(0, -1)).current, 24);
  assert.deepEqual(M.stats(events.filter((e) => e.date !== M.TODAY)), {
    total: 193,
    best: 42,
    current: 23,
  });
  assert.equal(
    M.stats(events.filter((e) => e.date !== "2026-06-22")).current,
    1,
  );
  const edited = events.map((e) => ({ ...e, feeling: 2 }));
  assert.deepEqual(M.stats(edited), M.stats(events));
});
test("bad storage falls back without importing arbitrary or malformed records", () => {
  assert.equal(
    M.load({
      getItem() {
        return "{broken";
      },
    }).length,
    196,
  );
  const bad = M.seed();
  bad[0].date = "2026-02-31";
  assert.equal(M.valid(bad), false);
  bad[0].date = "2026-03-01";
  bad[0].feeling = "<script>";
  assert.equal(M.valid(bad), false);
  assert.equal(M.load(null).length, 196);
  assert.equal(M.save(null, M.seed()), false);
});
test("demo storage persists only the dedicated versioned key", () => {
  const values = new Map([["unrelated", "untouched"]]);
  const storage = {
    getItem: (k) => values.get(k),
    setItem: (k, v) => values.set(k, v),
  };
  const events = M.seed().slice(1);
  assert.equal(M.save(storage, events), true);
  assert.deepEqual(M.load(storage), events);
  assert.equal(values.get("unrelated"), "untouched");
});
