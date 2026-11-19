#!/bin/bash
# Producer control surface for the failover storyline UI.
# Installed as the forced command for the demo backend's SSH key, so this is the ONLY
# thing that key can run. Arguments come from SSH_ORIGINAL_COMMAND and are validated.
#   status | start | stop | target primary|secondary | replay <target> <since-utc> [dry]
set -euo pipefail
ENV_FILE=/etc/idp-harness.env
JOURNAL_DIR=/opt/idp-harness/journal
APP=/opt/idp-harness/app

read -r -a argv <<< "${SSH_ORIGINAL_COMMAND:-$*}"
[[ "${argv[0]:-}" == "idp-demo-ctl" ]] && argv=("${argv[@]:1}")
cmd="${argv[0]:-status}"

target_now() { sudo grep -E '^SF_TARGET=' "$ENV_FILE" | cut -d= -f2; }

case "$cmd" in
  status)
    stats=$(sudo cat "$JOURNAL_DIR/stats.json" 2>/dev/null || echo null)
    printf '{"service":"%s","target":"%s","stats":%s}\n' \
      "$(systemctl is-active idp-harness || true)" "$(target_now)" "$stats" ;;
  start) sudo systemctl start idp-harness; echo '{"ok":true}' ;;
  stop)  sudo systemctl stop idp-harness;  echo '{"ok":true}' ;;
  target)
    t="${argv[1]:-}"
    [[ "$t" =~ ^(primary|secondary)$ ]] || { echo '{"error":"target must be primary|secondary"}'; exit 1; }
    sudo sed -i "s/^SF_TARGET=.*/SF_TARGET=$t/" "$ENV_FILE"
    if systemctl is-active --quiet idp-harness; then sudo systemctl restart idp-harness; fi
    echo "{\"ok\":true,\"target\":\"$t\"}" ;;
  replay)
    t="${argv[1]:-}"; since="${argv[2]:-} ${argv[3]:-}"; dry="${argv[4]:-}"
    [[ "$t" =~ ^(primary|secondary)$ ]] || { echo '{"error":"bad target"}'; exit 1; }
    [[ "$since" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}\ [0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?$ ]] \
      || { echo '{"error":"since must be YYYY-MM-DD HH:MM:SS[.ffffff]"}'; exit 1; }
    extra=(); [[ "$dry" == "dry" ]] && extra=(--dry-run)
    errf=$(mktemp)
    set +e
    sudo -u idp bash -c 'set -a; . '"$ENV_FILE"'; set +a; cd '"$APP"'; exec /opt/idp-harness/venv/bin/python replay.py "$@"' \
      replay --target "$t" --since "$since" "${extra[@]}" 2>"$errf" \
      | grep --line-buffered '^{'      # the SDK logs to stdout; keep only the JSON summary
    rc=${PIPESTATUS[0]}
    set -e
    cat "$errf" >> /tmp/idp-replay.log
    if [[ $rc -ne 0 ]]; then
      # Surface the real cause (last exception line) to the UI as JSON
      msg=$(grep -E 'Error|Exception' "$errf" | tail -1 | cut -c1-400 | sed 's/\\/\\\\/g; s/"/\\"/g')
      printf '{"error":"replay exited %s: %s"}\n' "$rc" "${msg:-see /tmp/idp-replay.log}"
    fi
    rm -f "$errf"
    exit $rc ;;
  *) echo '{"error":"usage: status|start|stop|target <t>|replay <t> <since> [dry]"}'; exit 1 ;;
esac
