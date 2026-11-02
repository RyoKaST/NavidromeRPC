#!/usr/bin/env bash
set -euo pipefail

config="$HOME/.config/vesktop"
# skip the first-run wizard
[ -f "$config/settings.json" ] || echo '{"arRPC": true, "minimizeToTray": false}' > "$config/settings.json"
[ -f "$config/state.json" ] || echo '{"firstLaunch": false}' > "$config/state.json"
rm -f "$config/SingletonLock" "$config/SingletonSocket" "$config/SingletonCookie"
rm -f /tmp/.X99-lock /tmp/.X11-unix/X99 "$XDG_RUNTIME_DIR"/discord-ipc-*

screen=320x240x24
[ "${LOGIN:-0}" = 1 ] && screen=1024x700x24
Xvfb :99 -screen 0 "$screen" -nolisten tcp &
until [ -e /tmp/.X11-unix/X99 ]; do sleep 0.2; done

if [ "${LOGIN:-0}" = 1 ]; then
  openbox &
  # no password, compose only publishes this on 127.0.0.1
  x11vnc -display :99 -forever -shared -nopw -localhost -rfbport 5900 -quiet &
  websockify --web=/usr/share/novnc 0.0.0.0:6080 127.0.0.1:5900 &
fi

python3 /bridge.py &
# docker blocks chromium's sandbox
vesktop --no-sandbox --disable-gpu --disable-dev-shm-usage --ozone-platform=x11 \
  --enable-low-end-device-mode &

# go down with the first process so docker restarts everything
wait -n
