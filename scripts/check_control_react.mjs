import assert from "node:assert/strict";

import { createControlMutation } from "../packages/typescript/control-react/dist/index.js";

function deferred() {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

const first = deferred();
const second = deferred();
const mutation = createControlMutation((input) => (input === "first" ? first.promise : second.promise));

const firstRun = mutation.mutate("first");
const secondRun = mutation.mutate("second");
first.resolve("older");
assert.equal(await firstRun, "older");
assert.deepEqual(mutation.getSnapshot(), { pending: true, data: null, error: null });

second.resolve("newer");
assert.equal(await secondRun, "newer");
assert.deepEqual(mutation.getSnapshot(), { pending: false, data: "newer", error: null });

const lateFirst = deferred();
const earlySecond = deferred();
const lastWins = createControlMutation((input) =>
  input === "first" ? lateFirst.promise : earlySecond.promise,
);
const lateFirstRun = lastWins.mutate("first");
const earlySecondRun = lastWins.mutate("second");
earlySecond.resolve("newest");
assert.equal(await earlySecondRun, "newest");
lateFirst.resolve("stale");
assert.equal(await lateFirstRun, "stale");
assert.deepEqual(lastWins.getSnapshot(), { pending: false, data: "newest", error: null });

console.log("control-react mutation concurrency contract passed");
