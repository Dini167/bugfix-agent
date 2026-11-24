import { test } from "node:test";
import assert from "node:assert/strict";
import { averageScore, topScores } from "../src/leaderboard.js";

test("returns the highest scores, highest first", () => {
  assert.deepEqual(topScores([40, 95, 70, 88], 2), [95, 88]);
});

test("does not modify the input", () => {
  const scores = [3, 1, 2];
  topScores(scores, 2);
  assert.deepEqual(scores, [3, 1, 2]);
});

test("average of scores", () => {
  assert.equal(averageScore([10, 20, 30]), 20);
});

test("average of empty list is zero", () => {
  assert.equal(averageScore([]), 0);
});
