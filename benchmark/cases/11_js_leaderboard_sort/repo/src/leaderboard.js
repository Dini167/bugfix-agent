/** Leaderboard helpers. */

/**
 * The `n` highest scores, highest first. Does not modify the input.
 * @param {number[]} scores
 * @param {number} n
 * @returns {number[]}
 */
export function topScores(scores, n) {
  return [...scores].sort((a, b) => a - b).slice(0, n);
}

/** Average score, or 0 for an empty list. */
export function averageScore(scores) {
  if (scores.length === 0) return 0;
  return scores.reduce((sum, s) => sum + s, 0) / scores.length;
}
