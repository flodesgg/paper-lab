#!/bin/zsh
# Install / remove / inspect the launchd LaunchAgent that runs the forward paper loop.
# Run it yourself from the repo:   scripts/agent.sh install | uninstall | status | run-now
# It only touches ~/Library/LaunchAgents/com.paperlab.forward.plist (your user account).
set -euo pipefail

LABEL=com.paperlab.forward
REPO=${0:A:h:h}
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"

case "${1:-}" in
  install)
    mkdir -p "$REPO/logs" "$HOME/Library/LaunchAgents"
    cd "$REPO"
    uv run --frozen paper-lab agent-plist > "$PLIST.tmp"
    plutil -lint "$PLIST.tmp" >/dev/null
    mv "$PLIST.tmp" "$PLIST"
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    launchctl bootstrap "$DOMAIN" "$PLIST"
    echo "installed $PLIST (runs now once, then at the configured times; logs in $REPO/logs/)"
    ;;
  uninstall)
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    rm -f "$PLIST" "$PLIST.tmp"
    if launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
      echo "still loaded: $DOMAIN/$LABEL" >&2
      exit 1
    fi
    echo "removed: $LABEL is not loaded and $PLIST is gone"
    ;;
  status)
    if launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
      launchctl print "$DOMAIN/$LABEL" | grep -E '^\s*(state|runs|last exit code|path) ='
    else
      echo "not installed"
    fi
    [[ -f "$REPO/journal/runs.csv" ]] && { echo "last runs:"; tail -n 3 "$REPO/journal/runs.csv"; }
    ;;
  run-now)
    launchctl kickstart -k "$DOMAIN/$LABEL"
    ;;
  *)
    echo "usage: $0 install | uninstall | status | run-now" >&2
    exit 2
    ;;
esac
