"""Drives the producer EC2 through its restricted idp-demo-ctl forced command over SSH."""

import json
import subprocess

from . import settings


def ctl(*args: str, timeout: int = 60) -> dict:
    cmd = ["ssh", "-i", settings.PRODUCER_SSH_KEY, "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
           "-o", "StrictHostKeyChecking=accept-new", settings.PRODUCER_SSH, " ".join(("idp-demo-ctl",) + args)]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    lines = [ln for ln in p.stdout.splitlines() if ln.startswith("{")]
    if not lines:
        raise RuntimeError(f"idp-demo-ctl {' '.join(args)} failed (rc={p.returncode}): "
                           f"{(p.stderr or p.stdout).strip()[-300:]}")
    out = json.loads(lines[-1])
    if "error" in out:
        raise RuntimeError(f"idp-demo-ctl {' '.join(args)}: {out['error']}")
    return out


def status() -> dict:
    return ctl("status", timeout=20)


def start() -> dict:
    return ctl("start")


def stop() -> dict:
    return ctl("stop", timeout=120)


def set_target(region: str) -> dict:
    return ctl("target", settings.PRODUCER_TARGET[region])


def replay(region: str, since_utc: str, dry: bool = False) -> dict:
    args = ["replay", settings.PRODUCER_TARGET[region], since_utc] + (["dry"] if dry else [])
    return ctl(*args, timeout=600)
