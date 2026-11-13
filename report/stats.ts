// Interval and test statistics, ported from kernel/xwebmcp's bench/lib/stats.mjs.

const Z = 1.959963984540054;

// Wilson score interval for k successes out of n.
export function wilson(k: number, n: number): [number, number] | undefined {
  if (!n) return undefined;
  const p = k / n;
  const denominator = 1 + (Z * Z) / n;
  const center = (p + (Z * Z) / (2 * n)) / denominator;
  const half =
    (Z * Math.sqrt((p * (1 - p)) / n + (Z * Z) / (4 * n * n))) / denominator;
  return [Math.max(0, center - half), Math.min(1, center + half)];
}

// Newcombe's hybrid score interval for p1 - p2 (method 10).
export function newcombe(
  k1: number,
  n1: number,
  k2: number,
  n2: number,
): [number, number] | undefined {
  const first = wilson(k1, n1);
  const second = wilson(k2, n2);
  if (!first || !second) return undefined;
  const p1 = k1 / n1;
  const p2 = k2 / n2;
  const difference = p1 - p2;
  return [
    difference - Math.hypot(p1 - first[0], second[1] - p2),
    difference + Math.hypot(first[1] - p1, p2 - second[0]),
  ];
}

function binomial(n: number, k: number): number {
  let result = 1;
  for (let index = 1; index <= k; index += 1)
    result = (result * (n - index + 1)) / index;
  return result;
}

// Two-sided exact sign test: k of n pairs went one way.
export function signTest(k: number, n: number): number | undefined {
  if (!n) return undefined;
  const tail = Math.min(k, n - k);
  let p = 0;
  for (let index = 0; index <= tail; index += 1) p += binomial(n, index);
  return Math.min(1, (2 * p) / 2 ** n);
}
