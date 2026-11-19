"""Replay the failover RPO gap from the producer journal into the target account.

  replay.py --target ireland --since '2026-11-02 15:27:24.146' [--margin-sec 600] [--dry-run]

1. Reads journal rows with EVENT_TS >= since - margin.
2. Reads the (PRODUCT_ID, PRODUCT_VERSION) keys the target already has in that window.
3. Streams only the missing rows on a dedicated 'idp_replay' channel and waits for commit.

The table is append-only, so the diff is what keeps replay exactly-once: re-running it
after a partial replay only sends what is still missing. Prints one JSON summary line.
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta

import snowflake.connector
from cryptography.hazmat.primitives import serialization
from snowflake.ingest.streaming import StreamingIngestClient

from journal import read_since

TARGETS = {"primary": "PRIMARY", "frankfurt": "PRIMARY", "secondary": "SECONDARY", "ireland": "SECONDARY"}
TS_FMT = "%Y-%m-%d %H:%M:%S.%f"
BATCH = 5000

log = logging.getLogger("idp-replay")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", required=True, choices=sorted(TARGETS))
    ap.add_argument("--since", required=True, help="UTC 'YYYY-MM-DD HH:MM:SS[.ffffff]' (last replication snapshot)")
    ap.add_argument("--margin-sec", type=int, default=600)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")

    env = os.environ
    account = env[f"SF_ACCOUNT_{TARGETS[args.target]}"]
    db, schema, pipe = env.get("SF_DATABASE", "IDP_DEMO"), env.get("SF_SCHEMA", "STREAMING"), env.get("SF_PIPE", "PRODUCT_PAYLOAD_PIPE")
    table = f"{db}.{schema}.PRODUCT_PAYLOAD"
    with open(env["PRIVATE_KEY_PATH"]) as f:
        pem = f.read()

    since = datetime.strptime(args.since if "." in args.since else args.since + ".000000", TS_FMT)
    window_start = (since - timedelta(seconds=args.margin_sec)).strftime(TS_FMT)
    t0 = time.monotonic()

    client, channel = None, None
    if not args.dry_run:
        # Open (and so take over) the replay channel BEFORE diffing: any in-flight rows from
        # an interrupted earlier replay are fenced off and can't land after the diff.
        host = account.lower().replace("_", "-")
        client = StreamingIngestClient(
            client_name="idp_replay", db_name=db, schema_name=schema, pipe_name=pipe,
            properties={"account": account, "user": env.get("SF_USER", "IDP_STREAMING_SVC"),
                        "role": env.get("SF_ROLE", "IDP_STREAMING_RL"),
                        "url": f"https://{host}.snowflakecomputing.com:443", "private_key": pem})
        channel, _ = client.open_channel("idp_replay")
        time.sleep(5)

    journal = list(read_since(env.get("JOURNAL_DIR", "/opt/idp-harness/journal"), window_start))
    present = existing_keys(account, env, pem, table, window_start)
    missing = sorted((r for r in journal if (r["PRODUCT_ID"], int(r["PRODUCT_VERSION"])) not in present),
                     key=lambda r: r["EVENT_TS"])
    log.info("window_start=%s journal=%d present=%d missing=%d", window_start, len(journal), len(present), len(missing))

    committed = 0
    if channel and missing:
        last = channel.get_latest_committed_offset_token()
        offset = int(last) if last else 0
        for i in range(0, len(missing), BATCH):
            rows = missing[i:i + BATCH]
            channel.append_rows(rows, str(offset + 1), str(offset + len(rows)))
            offset += len(rows)
        committed = wait_committed(channel, offset, base=offset - len(missing))
    if client:
        channel.close(wait_for_flush=True, timeout_seconds=60)
        client.close()

    print(json.dumps({
        "target": args.target, "account": account, "since": since.strftime(TS_FMT), "window_start": window_start,
        "journal_rows_in_window": len(journal), "already_present": len(journal) - len(missing),
        "missing": len(missing), "replayed": committed, "dry_run": args.dry_run,
        "elapsed_sec": round(time.monotonic() - t0, 1),
    }))
    return 0 if args.dry_run or committed == len(missing) else 2


def existing_keys(account, env, pem, table, window_start):
    key = serialization.load_pem_private_key(pem.encode(), password=None)
    with snowflake.connector.connect(
            account=account, user=env.get("SF_USER", "IDP_STREAMING_SVC"), role=env.get("SF_ROLE", "IDP_STREAMING_RL"),
            warehouse=env.get("REPLAY_WAREHOUSE", "IDP_ADMIN_WH"), private_key=key,
            session_parameters={"TIMEZONE": "UTC"}) as con:
        cur = con.cursor().execute(
            f"SELECT PRODUCT_ID, PRODUCT_VERSION FROM {table} WHERE EVENT_TS >= %s", (window_start,))
        return {(pid, int(v)) for pid, v in cur}


def wait_committed(channel, target_offset, base, timeout=180):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        tok = channel.get_latest_committed_offset_token()
        if tok and int(tok) >= target_offset:
            return target_offset - base
        time.sleep(1)
    tok = channel.get_latest_committed_offset_token()
    return max(0, (int(tok) if tok else 0) - base)


if __name__ == "__main__":
    sys.exit(main())
