"""Settings for the failover storyline demo (all overridable via environment)."""

import os
import sys
from pathlib import Path

from ..config import HARNESS_ROOT

ACCOUNTS = {
    "frankfurt": os.environ.get("DEMO_ACCOUNT_FRANKFURT", "SFSEEUROPE-EU_DEMO59"),
    "ireland": os.environ.get("DEMO_ACCOUNT_IRELAND", "SFSEEUROPE-CMENDONCA_DEMO_IR"),
}
# Producer's idp-demo-ctl speaks primary/secondary (its env-file names for the two accounts)
PRODUCER_TARGET = {"frankfurt": "primary", "ireland": "secondary"}

OPS_USER = os.environ.get("DEMO_OPS_USER", "IDP_DEMO_OPS_SVC")
OPS_KEY = os.environ.get("DEMO_OPS_KEY", str(HARNESS_ROOT / "keys" / "ops_key.p8"))

PRODUCER_SSH = os.environ.get("DEMO_PRODUCER_SSH", "ec2-user@3.252.136.74")
PRODUCER_SSH_KEY = os.environ.get("DEMO_PRODUCER_SSH_KEY", str(HARNESS_ROOT / "keys" / "producer_ctl_ssh"))

JAVA = os.environ.get("DEMO_JAVA", "/usr/lib/jvm/java-17-amazon-corretto.x86_64/bin/java")
JAVA_CP = os.environ.get("DEMO_JAVA_CP", ":".join(str(HARNESS_ROOT / p) for p in (
    "java-harness/out", "lib/snowflake-jdbc.jar", "lib/slf4j-api.jar", "lib/slf4j-simple.jar")))
LOADTEST_USER = os.environ.get("DEMO_LOADTEST_USER", "IDP_LOADTEST_SVC")
LOADTEST_KEY = os.environ.get("DEMO_LOADTEST_KEY", str(HARNESS_ROOT / "keys" / "rsa_key.p8"))
TRAFFIC_CONNECTIONS = int(os.environ.get("DEMO_TRAFFIC_CONNECTIONS", "100"))
# Consumption driver for the lookup traffic: "adbc" (Arrow, Python) or "jdbc" (Java RdpLoadTest)
TRAFFIC_ENGINE = os.environ.get("DEMO_TRAFFIC_ENGINE", "adbc").lower()
PYTHON = os.environ.get("DEMO_PYTHON", sys.executable)
ADBC_TRAFFIC = Path(os.environ.get("DEMO_ADBC_TRAFFIC", str(HARNESS_ROOT / "adbc_traffic.py")))

STATE_FILE = Path(os.environ.get("DEMO_STATE_FILE", str(HARNESS_ROOT / "backend" / "demo_state.json")))
DEMO_PASSWORD = os.environ.get("DEMO_PASSWORD", "")
STATIC_DIR = Path(os.environ.get("DEMO_STATIC_DIR", str(HARNESS_ROOT / "frontend" / "dist")))
