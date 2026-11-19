"""Continuous ADBC point-lookup traffic for the failover storyline UI.

Same contract as `RdpLoadTest --continuous`: runs until SIGTERM and prints one JSON
line per second on stdout:
  {"ts":..., "account":..., "qps":..., "p50_ms":..., "p95_ms":..., "errors":..., "active":..., "last_error":...}

  python adbc_traffic.py --account SFSEEUROPE-EU_DEMO59 --user IDP_LOADTEST_SVC --key keys/rsa_key.p8 \
      --connections 100 --rampup 5 [--warehouse IDP_IW_XS --pool_warehouse IDP_ADMIN_WH]
"""

import argparse
import base64
import json
import math
import multiprocessing as mp
import queue
import random
import re
import signal
import sys
import threading
import time

import adbc_driver_snowflake.dbapi as adbc
from cryptography.hazmat.primitives import serialization

SAFE_KEY = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")   # keys are inlined into SQL
STOP = threading.Event()


def dsn(a, warehouse: str) -> str:
    with open(a.key, "rb") as f:
        key = serialization.load_pem_private_key(f.read(), password=None)
    der = key.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())
    pk = base64.urlsafe_b64encode(der).decode()
    return (f"{a.user}@{a.account}/{a.database}/{a.schema}?warehouse={warehouse}"
            f"&authenticator=SNOWFLAKE_JWT&privateKey={pk}")


def load_pool(a) -> list[str]:
    with adbc.connect(dsn(a, a.pool_warehouse)) as con, con.cursor() as cur:
        cur.execute(f"SELECT DISTINCT PRODUCT_ID FROM {a.table} SAMPLE (1000000 ROWS)")
        ids = [r[0] for r in cur.fetchall() if r[0] and SAFE_KEY.match(r[0])]
    if not ids:
        sys.exit(f"No PRODUCT_ID values found in {a.table}")
    random.Random(42).shuffle(ids)
    return ids


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", required=True)
    ap.add_argument("--user", required=True)
    ap.add_argument("--key", required=True)
    ap.add_argument("--database", default="IDP_DEMO")
    ap.add_argument("--schema", default="STREAMING")
    ap.add_argument("--table", default="IDP_DEMO.STREAMING.PRODUCT_PAYLOAD")
    ap.add_argument("--warehouse", default="IDP_IW_XS")
    ap.add_argument("--pool_warehouse", default="IDP_ADMIN_WH")
    ap.add_argument("--connections", type=int, default=100)
    ap.add_argument("--rampup", type=int, default=5)
    ap.add_argument("--hot", type=float, default=0.8, help="share of lookups from the 1,000 hottest ids")
    ap.add_argument("--procs", type=int, default=4,
                    help="worker processes; connections are split across them to avoid the GIL")
    a = ap.parse_args()
    signal.signal(signal.SIGTERM, lambda *_: STOP.set())
    signal.signal(signal.SIGINT, lambda *_: STOP.set())

    pool = load_pool(a)
    print(f"Loaded {len(pool)} product ids", file=sys.stderr)
    procs = max(1, min(a.procs, a.connections))
    # spawn, not fork: the ADBC driver's runtime (already initialised by load_pool) does not survive fork()
    ctx = mp.get_context("spawn")
    out = ctx.Queue()
    stop = ctx.Event()
    children = []
    for p in range(procs):
        n = a.connections // procs + (1 if p < a.connections % procs else 0)
        c = ctx.Process(target=run_workers, args=(a, pool, n, p, out, stop), daemon=True)
        c.start()
        children.append(c)

    # Aggregate per-second windows from every worker process into one JSON line
    while not STOP.wait(1):
        lat, errs, err, active = [], 0, "", 0
        seen = set()
        deadline = time.monotonic() + 0.5
        while len(seen) < procs and time.monotonic() < deadline:
            try:
                pid, l, e, msg, act = out.get(timeout=0.1)
            except queue.Empty:
                continue
            seen.add(pid)
            lat += l
            errs += e
            err = msg or err
            active += act
        lat.sort()

        def pct(q):
            return lat[max(0, math.ceil(q * len(lat)) - 1)] * 1000 if lat else 0.0
        print(json.dumps({"ts": int(time.time() * 1000), "account": a.account, "qps": len(lat),
                          "p50_ms": round(pct(0.5), 1), "p95_ms": round(pct(0.95), 1), "errors": errs,
                          "active": active, "last_error": err, "engine": "adbc"}), flush=True)
    stop.set()
    for c in children:
        c.join(10)


def run_workers(a, pool, n_conns, proc_id, out, stop_evt):
    """One worker process: n_conns threads, each with its own ADBC connection."""
    signal.signal(signal.SIGTERM, signal.SIG_IGN)   # parent coordinates shutdown
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    hot = pool[:1000]
    serve_dsn = dsn(a, a.warehouse)

    lock = threading.Lock()
    window: list[float] = []
    stats = {"errors": 0, "active": 0, "last_error": ""}

    def worker(i: int):
        rng = random.Random(i * 7919 + time.time_ns())
        backoff = 0.5
        while not STOP.is_set():
            try:
                with adbc.connect(serve_dsn) as con, con.cursor() as cur:
                    with lock:
                        stats["active"] += 1
                    backoff = 0.5
                    try:
                        while not STOP.is_set():
                            pid = hot[rng.randrange(len(hot))] if rng.random() < a.hot else pool[rng.randrange(len(pool))]
                            t0 = time.perf_counter()
                            try:
                                cur.execute(
                                    "SELECT PRODUCT_ID, PRODUCT_VERSION, ISIN, PRODUCT_NAME, PRICE, STATUS, PAYLOAD "
                                    f"FROM {a.table} WHERE PRODUCT_ID = '{pid}' ORDER BY PRODUCT_VERSION DESC LIMIT 1")
                                cur.fetchall()
                                dt = time.perf_counter() - t0
                                with lock:
                                    window.append(dt)
                            except Exception as e:      # query error: keep the connection
                                with lock:
                                    stats["errors"] += 1
                                    stats["last_error"] = str(e)[:200]
                    finally:
                        with lock:
                            stats["active"] -= 1
            except Exception as e:                       # connect error: back off and retry
                with lock:
                    stats["errors"] += 1
                    stats["last_error"] = "conn: " + str(e)[:200]
                STOP.wait(backoff)
                backoff = min(backoff * 2, 10)

    delay = a.rampup / max(1, a.connections)
    STOP.wait(delay * proc_id)                     # stagger processes across the ramp
    for i in range(n_conns):
        if stop_evt.is_set():
            break
        threading.Thread(target=worker, args=(proc_id * 10_000 + i,), daemon=True).start()
        STOP.wait(delay * max(1, a.procs))

    while not stop_evt.wait(1):
        with lock:
            lat, window[:] = list(window), []
            errs, stats["errors"] = stats["errors"], 0
            err, stats["last_error"] = stats["last_error"], ""
            active = stats["active"]
        out.put((proc_id, lat, errs, err, active))
    STOP.set()


if __name__ == "__main__":
    main()
