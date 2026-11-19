"""Append-only JSONL journal of produced rows (hourly files, 24 h retention).

Stands in for the upstream source's message retention (e.g. AMPS replay): after a
failover, replay.py reads it to resend rows the secondary never received.
"""

import glob
import json
import os
import time

RETENTION_SEC = 24 * 3600


def _hour_file(directory: str, t: float) -> str:
    return os.path.join(directory, time.strftime("journal-%Y%m%d%H.jsonl", time.gmtime(t)))


def _count_lines(path: str) -> int:
    n = 0
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            n += chunk.count(b"\n")
    return n


class Journal:
    def __init__(self, directory: str):
        self.dir = directory
        os.makedirs(directory, exist_ok=True)
        self._prune()
        self.rows = sum(_count_lines(p) for p in journal_files(directory))
        self._path, self._f = None, None

    def write(self, rows):
        path = _hour_file(self.dir, time.time())
        if path != self._path:
            if self._f:
                self._f.close()
            self._prune()
            self._path, self._f = path, open(path, "a", encoding="utf-8")
        self._f.writelines(json.dumps(r, default=str, separators=(",", ":")) + "\n" for r in rows)
        self.rows += len(rows)

    def flush(self):
        if self._f:
            self._f.flush()

    def close(self):
        if self._f:
            self._f.flush()
            os.fsync(self._f.fileno())
            self._f.close()
            self._f = None

    def _prune(self):
        cutoff = time.time() - RETENTION_SEC
        for p in journal_files(self.dir):
            if os.path.getmtime(p) < cutoff:
                os.remove(p)


def journal_files(directory: str):
    return sorted(glob.glob(os.path.join(directory, "journal-*.jsonl")))


def read_since(directory: str, since_ts: str):
    """Yield journal rows with EVENT_TS >= since_ts (both 'YYYY-MM-DD HH:MM:SS.ffffff' UTC)."""
    since_hour = since_ts[:13].replace("-", "").replace(" ", "")   # YYYYMMDDHH
    for p in journal_files(directory):
        if os.path.basename(p)[8:18] < since_hour:
            continue
        with open(p, encoding="utf-8") as f:
            for line in f:
                if not line.endswith("\n"):
                    break                       # torn final line from a crash
                row = json.loads(line)
                if row["EVENT_TS"] >= since_ts:
                    yield row
