"""Calls IDP_ADMIN.PUBLIC.DEMO_OPS(<action>) in either account as the ops service user."""

import json
import threading
from datetime import datetime, timezone

import snowflake.connector
from cryptography.hazmat.primitives import serialization

from . import settings

_conns: dict = {}
_locks = {k: threading.Lock() for k in settings.ACCOUNTS}


def _key():
    with open(settings.OPS_KEY, "rb") as f:
        return serialization.load_pem_private_key(f.read(), password=None)


def _connect(region: str):
    return snowflake.connector.connect(
        account=settings.ACCOUNTS[region], user=settings.OPS_USER, private_key=_key(),
        role="IDP_DEMO_OPS_RL", warehouse="IDP_ADMIN_WH", login_timeout=20, network_timeout=60,
        client_session_keep_alive=True, session_parameters={"TIMEZONE": "UTC"})


def ops(region: str, action: str) -> dict:
    """Run one DEMO_OPS action; reconnects once if the cached session has gone away."""
    with _locks[region]:
        for attempt in (1, 2):
            try:
                con = _conns.get(region) or _connect(region)
                _conns[region] = con
                cur = con.cursor()
                # PROMOTE_IDP() can switch the session warehouse; pin it before every call.
                cur.execute("USE WAREHOUSE IDP_ADMIN_WH")
                raw = cur.execute("CALL IDP_ADMIN.PUBLIC.DEMO_OPS(%s)", (action,)).fetchone()[0]
                out = json.loads(raw) if isinstance(raw, str) else raw
                if isinstance(out, dict) and out.get("error"):
                    raise RuntimeError(out["error"])
                return out
            except snowflake.connector.errors.ProgrammingError:
                raise
            except Exception:
                _conns.pop(region, None)
                if attempt == 2:
                    raise
    raise AssertionError("unreachable")


def parse_ts(s) -> datetime | None:
    """Parse Snowflake timestamp strings ('... Z', '... +0000', '... -0800', or NTZ = UTC)."""
    if not s:
        return None
    s = str(s).strip().replace(" Z", "+00:00")
    for fmt in ("%Y-%m-%d %H:%M:%S.%f %z", "%Y-%m-%d %H:%M:%S %z", "%Y-%m-%d %H:%M:%S.%f%z",
                "%Y-%m-%d %H:%M:%S%z", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            d = datetime.strptime(s, fmt)
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None
