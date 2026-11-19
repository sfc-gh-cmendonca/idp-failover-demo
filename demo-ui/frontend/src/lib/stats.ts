export function toNumber(v: unknown): number {
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : 0;
}

export function mean(xs: number[]): number {
  return xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : 0;
}

/** Nearest-rank percentile, p in [0, 1]. */
export function percentile(xs: number[], p: number): number {
  if (!xs.length) return 0;
  const s = [...xs].sort((a, b) => a - b);
  return s[Math.min(s.length - 1, Math.max(0, Math.ceil(p * s.length) - 1))];
}

export interface Bin {
  label: string;
  count: number;
}

export function histogram(xs: number[], bins = 30): Bin[] {
  if (!xs.length) return [];
  let lo = Infinity;
  let hi = -Infinity;
  for (const x of xs) {
    if (x < lo) lo = x;
    if (x > hi) hi = x;
  }
  const width = hi > lo ? (hi - lo) / bins : 1;
  const counts = new Array(bins).fill(0);
  for (const x of xs) counts[Math.min(bins - 1, Math.floor((x - lo) / width))]++;
  return counts.map((count, i) => ({ label: (lo + i * width).toFixed(0), count }));
}
