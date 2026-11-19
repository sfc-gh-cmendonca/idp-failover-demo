"""Continuous JDBC lookup traffic (RdpLoadTest --continuous) with a per-second stats feed."""

import collections
import json
import signal
import subprocess
import threading
import time

from . import settings


class Traffic:
    def __init__(self):
        self._proc: subprocess.Popen | None = None
        self.region: str | None = None
        self.points = collections.deque(maxlen=900)   # 15 min of 1-second points
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self, region: str):
        with self._lock:
            self._stop_locked()
            account = settings.ACCOUNTS[region]
            common = ["--account", account, "--user", settings.LOADTEST_USER, "--key", settings.LOADTEST_KEY,
                      "--connections", str(settings.TRAFFIC_CONNECTIONS), "--rampup", "5"]
            if settings.TRAFFIC_ENGINE == "adbc":
                cmd = [settings.PYTHON, str(settings.ADBC_TRAFFIC), *common,
                       "--warehouse", "IDP_IW_XS", "--pool_warehouse", "IDP_ADMIN_WH"]
            else:
                cmd = [settings.JAVA, "-Xmx2g", "--add-opens=java.base/java.nio=ALL-UNNAMED", "-cp", settings.JAVA_CP,
                       "RdpLoadTest", *common, "--database", "IDP_DEMO", "--schema", "STREAMING",
                       "--table", "IDP_DEMO.STREAMING.PRODUCT_PAYLOAD", "--warehouse", "IDP_IW_XS",
                       "--pool_warehouse", "IDP_ADMIN_WH", "--mix", "product_single", "--ticker_bias", "hot80",
                       "--continuous", "true"]
            self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=open("/tmp/demo-traffic.err", "a"),
                                          text=True, bufsize=1)
            self.region = region
            threading.Thread(target=self._read, args=(self._proc, region), daemon=True).start()

    def stop(self):
        with self._lock:
            self._stop_locked()

    def _stop_locked(self):
        if self.running:
            self._proc.send_signal(signal.SIGTERM)
            try:
                self._proc.wait(15)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc, self.region = None, None

    def _read(self, proc, region):
        for line in proc.stdout:
            if line.startswith("{"):
                try:
                    p = json.loads(line)
                except ValueError:
                    continue
                p["region"] = region
                self.points.append(p)

    def latest(self) -> dict | None:
        p = self.points[-1] if self.points else None
        return p if p and time.time() * 1000 - p["ts"] < 5000 else None

    def wait_for_qps(self, timeout: int = 120) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            p = self.latest()
            if p and p["qps"] > 0 and p["region"] == self.region:
                return p
            if self._proc is not None and self._proc.poll() is not None:
                raise RuntimeError("traffic engine exited; see /tmp/demo-traffic.err")
            time.sleep(1)
        raise TimeoutError("no successful lookups yet")


traffic = Traffic()
