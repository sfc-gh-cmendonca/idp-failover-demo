const ok = (n: number) => Number.isFinite(n);

export const num = (n: number) => (ok(n) ? n.toLocaleString() : "-");
export const msFromMs = (ms: number) => (ok(ms) ? `${ms.toFixed(1)} ms` : "-");
export const pct = (p: number) => (ok(p) ? `${p.toFixed(1)}%` : "-");
export const kb = (bytes: number) => (ok(bytes) ? `${(bytes / 1024).toFixed(1)} KB` : "-");
export const mb = (bytes: number) => (ok(bytes) ? `${(bytes / (1024 * 1024)).toFixed(1)} MB` : "-");
