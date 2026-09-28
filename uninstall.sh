#!/bin/zsh
# Uninstalls Boulito: removes what the project put outside its folder,
# then shows what is left to do by hand. After that, deleting the folder is enough.
set -u
DIR="${0:A:h}"
AGENT="$HOME/Library/LaunchAgents/local.boulito.plist"

echo "Uninstalling Boulito ($DIR)"
echo

# 1. Stop the assistant if it is running (the app and the project's Python; path compared as is, not as a regular expression)
pids=($(ps -axo pid=,command= | awk -v a="$DIR/Boulito.app/" -v b="$DIR/.venv/bin/voix" '{pid=$1; sub(/^ *[0-9]+ /, ""); if (index($0, a) == 1 || index($0, " " b) > 0) print pid}'))
if (( ${#pids} )); then
  kill $pids 2>/dev/null
  echo "✓ Boulito stopped"
else
  echo "· Boulito was not running"
fi

# Local signing certificate: its trust is stored outside the folder (the session's trust settings)
if [[ -f "$DIR/.signing/cert.pem" ]]; then
  if security remove-trusted-cert "$DIR/.signing/cert.pem" 2>/dev/null; then
    echo "✓ Trust of the local signing certificate removed"
  else
    echo "· Local signing certificate: not trusted (nothing to remove)"
  fi
fi
# Password of the local signing keychain: kept in the login keychain (the release one is left alone)
if security delete-generic-password -s "Boulito signing keychain" -a "$DIR/.signing/boulito.keychain-db" >/dev/null 2>&1; then
  echo "✓ Password of the local signing keychain removed from the login keychain"
fi

# 2. LaunchAgent (open at login)
if [[ -f "$AGENT" ]]; then
  launchctl bootout "gui/$(id -u)" "$AGENT" 2>/dev/null
  rm "$AGENT"
  echo "✓ LaunchAgent removed"
else
  echo "· No LaunchAgent"
fi

cat <<EOF

To do by hand:

1. System Settings → Privacy & Security: remove Boulito from
   Microphone, Input Monitoring, Accessibility and Calendars,
   and under Automation → Boulito, uncheck Safari, Music and Reminders.
   System Settings → Notifications: Boulito can stay (it disappears with the app) or be turned off.

2. Safari → Settings → Developer: uncheck
   “Allow JavaScript from Apple Events”.

3. Shortcuts: delete “Minuteur Boulito” and “Minuteur Boulito – Gestion” (or “Boulito Timer…”)
   if you installed them. Music: delete the “Boulito queue” playlist if it exists.

4. Optional: brew uninstall uv (if nothing else uses it).

5. Move the folder to the Trash:
   $DIR
EOF
