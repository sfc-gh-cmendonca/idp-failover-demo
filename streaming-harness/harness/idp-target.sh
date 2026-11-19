#!/bin/bash
# Point the IDP harness at the primary (Frankfurt) or secondary (Ireland) Snowflake account.
#   idp-target status | primary | secondary | frankfurt | ireland
set -euo pipefail
ENV_FILE=/etc/idp-harness.env
# env file is root-only (holds account config)
if [[ $EUID -ne 0 ]]; then exec sudo "$0" "$@"; fi

current() { grep -E '^SF_TARGET=' "$ENV_FILE" | cut -d= -f2; }
account_for() { grep -E "^SF_ACCOUNT_${1^^}=" "$ENV_FILE" | cut -d= -f2; }

case "${1:-status}" in
  status)
    t=$(current)
    echo "target=$t account=$(account_for "$t") service=$(systemctl is-active idp-harness || true)"
    exit 0 ;;
  primary|frankfurt)   t=primary ;;
  secondary|ireland)   t=secondary ;;
  *) echo "usage: idp-target status|primary|secondary|frankfurt|ireland" >&2; exit 1 ;;
esac

sed -i "s/^SF_TARGET=.*/SF_TARGET=$t/" "$ENV_FILE"
systemctl restart idp-harness
echo "Switched to $t ($(account_for "$t")); tail logs: journalctl -u idp-harness -f"
