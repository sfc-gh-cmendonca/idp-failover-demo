"""IDP product-payload streaming harness.

Streams Securities Services product UDM messages into a Snowflake interactive
table using the Snowpipe Streaming high-performance SDK.

All settings come from environment variables (see README / terraform user_data).
"""

import json
import logging
import os
import signal
import sys
import time
import uuid
import zlib

from snowflake.ingest.streaming import StreamingIngestClient

from journal import Journal
from udm import ProductUniverse

log = logging.getLogger("idp-harness")


def env(name, default=None, cast=str):
    v = os.environ.get(name, default)
    if v is None:
        sys.exit(f"Missing required environment variable {name}")
    return cast(v)


def load_private_key() -> str:
    secret_id = os.environ.get("PRIVATE_KEY_SECRET_ID")
    if secret_id:
        import boto3
        region = os.environ.get("AWS_REGION", "eu-west-1")
        return boto3.client("secretsmanager", region_name=region).get_secret_value(
            SecretId=secret_id)["SecretString"]
    with open(env("PRIVATE_KEY_PATH")) as f:
        return f.read()


class Producer:
    def __init__(self):
        # Target region: SF_TARGET=primary|secondary picks SF_ACCOUNT_PRIMARY / _SECONDARY.
        # SF_ACCOUNT, if set, overrides both (e.g. for local runs).
        self.target = os.environ.get("SF_TARGET", "primary").lower()
        if self.target not in ("primary", "secondary"):
            sys.exit(f"SF_TARGET must be 'primary' or 'secondary', got {self.target!r}")
        self.account = os.environ.get("SF_ACCOUNT") or env(f"SF_ACCOUNT_{self.target.upper()}")
        self.user = env("SF_USER", "IDP_STREAMING_SVC")
        self.role = env("SF_ROLE", "IDP_STREAMING_RL")
        self.database = env("SF_DATABASE", "IDP_DEMO")
        self.schema = env("SF_SCHEMA", "STREAMING")
        self.pipe = env("SF_PIPE", "PRODUCT_PAYLOAD_PIPE")
        self.rate = env("RATE_PER_SEC", "100", float)
        self.universe_size = env("UNIVERSE_SIZE", "50000", int)
        self.n_channels = env("CHANNELS", "2", int)
        self.duration = env("DURATION_SEC", "0", int)          # 0 = run until stopped
        self.instance = env("INSTANCE_ID", uuid.uuid4().hex[:8])
        # Journal of every row handed to the SDK: the source-side retention used to
        # replay the RPO gap after a failover (see replay.py). Empty = disabled.
        self.journal_dir = os.environ.get("JOURNAL_DIR", "")
        self.stop = False

        host = self.account.lower().replace("_", "-")
        self.props = {
            "account": self.account,
            "user": self.user,
            "role": self.role,
            "url": f"https://{host}.snowflakecomputing.com:443",
            "private_key": load_private_key(),
        }

    def start(self):
        log.info("Building universe of %d products", self.universe_size)
        self.universe = ProductUniverse(self.universe_size)
        self.client = StreamingIngestClient(
            client_name=f"idp_harness_{self.instance}",
            db_name=self.database,
            schema_name=self.schema,
            pipe_name=self.pipe,
            properties=self.props,
        )
        self.channels, self.offsets = [], []
        for i in range(self.n_channels):
            ch, _ = self.client.open_channel(f"idp_{self.instance}_ch{i}")
            last = ch.get_latest_committed_offset_token()
            self.channels.append(ch)
            self.offsets.append(int(last) if last else 0)
        log.info("Opened %d channels on %s / %s.%s.%s (target=%s)", self.n_channels,
                 self.account, self.database, self.schema, self.pipe, self.target)
        self.journal = Journal(self.journal_dir) if self.journal_dir else None

    def _channel_for(self, product_id: str) -> int:
        # Same product always goes to the same channel, so its versions stay ordered.
        return zlib.crc32(product_id.encode()) % self.n_channels

    def run(self):
        tick = 0.1
        sent, started = 0, time.monotonic()
        last_report = last_log = started

        while not self.stop:
            t0 = time.monotonic()
            # Pace against elapsed time so slow appends don't drag the rate down.
            n = int(self.rate * (t0 - started)) - sent

            batches = [[] for _ in range(self.n_channels)]
            for _ in range(n):
                row = self.universe.next_event()
                batches[self._channel_for(row["PRODUCT_ID"])].append(row)

            for i, rows in enumerate(batches):
                if not rows:
                    continue
                start = self.offsets[i] + 1
                end = self.offsets[i] + len(rows)
                self.channels[i].append_rows(rows, str(start), str(end))
                self.offsets[i] = end
                if self.journal:
                    self.journal.write(rows)
            sent += n
            if self.journal:
                self.journal.flush()

            now = time.monotonic()
            if now - last_report >= 2:
                self._write_stats(sent, now - started)
                last_report = now
            if now - last_log >= 10:
                last_log = now
                committed = [ch.get_latest_committed_offset_token() for ch in self.channels]
                log.info("sent=%d rate=%.0f/s committed_offsets=%s", sent, sent / (now - started), committed)
            if self.duration and now - started >= self.duration:
                break
            time.sleep(max(0.0, tick - (time.monotonic() - t0)))

        self._write_stats(sent, time.monotonic() - started, running=False)
        log.info("Stopping after %d messages", sent)

    def _write_stats(self, sent: int, elapsed: float, running: bool = True):
        """Small JSON status file polled by the demo UI (via idp-demo-ctl stats)."""
        if not self.journal_dir:
            return
        stats = {"running": running, "target": self.target, "account": self.account,
                 "sent": sent, "rate": round(sent / elapsed, 1) if elapsed else 0.0,
                 "journal_rows": self.journal.rows if self.journal else 0, "ts": time.time()}
        tmp = os.path.join(self.journal_dir, ".stats.tmp")
        with open(tmp, "w") as f:
            json.dump(stats, f)
        os.replace(tmp, os.path.join(self.journal_dir, "stats.json"))

    def close(self):
        if self.journal:
            self.journal.close()
        for ch in self.channels:
            try:
                ch.close(wait_for_flush=True, timeout_seconds=60)
            except Exception as e:  # keep closing the rest
                log.warning("Channel close failed: %s", e)
        self.client.close()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    p = Producer()

    def _stop(*_):
        p.stop = True
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    p.start()
    try:
        p.run()
    finally:
        p.close()


if __name__ == "__main__":
    main()
