/**
 * Linked-slider utility: when one weight changes, the others adjust
 * proportionally so the total always sums to 100%.
 */
export const TARGET_TOTAL = 100;

/**
 * Adjust a record of weights so that `changedKey` becomes `newWeight`
 * and all other keys are scaled proportionally. The result always sums
 * to exactly `target` (default 100).
 */
export function adjustWeight(
  weights: Record<string, number>,
  changedKey: string,
  newWeight: number,
  target: number = TARGET_TOTAL,
): Record<string, number> {
  const keys = Object.keys(weights);
  const otherKeys = keys.filter((k) => k !== changedKey);

  // Edge case: only one key → it must equal the target
  if (otherKeys.length === 0) {
    return { [changedKey]: target };
  }

  // Clamp the new weight so remaining keys can still sum to a valid amount
  // Each other key must be ≥ 0, so newWeight ≤ target.
  newWeight = Math.min(Math.max(newWeight, 0), target);

  const remaining = target - newWeight;
  const otherTotal = otherKeys.reduce((s, k) => s + (weights[k] ?? 0), 0);

  let result: Record<string, number>;

  if (otherTotal === 0) {
    // All other weights are 0 → distribute evenly
    const each = remaining / otherKeys.length;
    result = {};
    for (const k of otherKeys) result[k] = Math.round(each);
  } else {
    // Scale other weights proportionally
    const scale = remaining / otherTotal;
    result = {};
    for (const k of otherKeys) result[k] = Math.round((weights[k] ?? 0) * scale);
  }

  // Fix any rounding error by nudging the largest other key
  const currentTotal = Object.values(result).reduce((s, v) => s + v, 0);
  const diff = remaining - currentTotal;
  if (diff !== 0) {
    const largest = otherKeys.reduce((a, b) =>
      (result[a] ?? 0) >= (result[b] ?? 0) ? a : b,
    );
    result[largest] = Math.max(0, (result[largest] ?? 0) + diff);
  }

  result[changedKey] = newWeight;
  return result;
}

/**
 * Sum all values in a weight record.
 */
export function sumWeights(weights: Record<string, number>): number {
  return Object.values(weights).reduce((s, v) => s + v, 0);
}
