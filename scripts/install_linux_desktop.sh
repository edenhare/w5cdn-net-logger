#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
DESKTOP_FILE="$APP_DIR/w5cdn-net-logger.desktop"

chmod +x "$REPO_DIR/start.sh"
mkdir -p "$APP_DIR"

cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=W5CDN Net Logger
Comment=Local Ham Radio Net Control, Skywarn, RACES & NTS Radiogram Logger
Exec=$REPO_DIR/start.sh
Path=$REPO_DIR
Icon=$REPO_DIR/src/net_logger/static/icon.png
Terminal=false
Categories=HamRadio;Utility;Network;
StartupWMClass=w5cdn-net-logger
EOF

chmod 644 "$DESKTOP_FILE"
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$APP_DIR" >/dev/null 2>&1 || true
fi

echo "Installed Linux desktop launcher: $DESKTOP_FILE"
