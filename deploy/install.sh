#!/bin/sh
# Install Jarvis as a systemd service plus a kiosk autostart entry.
#
# Run from the repo root on the Pi:   sudo ./deploy/install.sh
#
# The service file is generated rather than copied, because Raspberry Pi OS no
# longer creates a default "pi" user -- the username is chosen at imaging time,
# so a hardcoded User= fails on most current installs.
set -eu

# Resolve the real user even under sudo. SUDO_USER is who invoked us.
TARGET_USER="${SUDO_USER:-$(id -un)}"
if [ "$TARGET_USER" = "root" ]; then
    echo "Refusing to install Jarvis as root: run this with sudo from your normal user." >&2
    exit 1
fi
TARGET_HOME=$(getent passwd "$TARGET_USER" | cut -d: -f6)
REPO_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)

if [ "$REPO_DIR" != "$TARGET_HOME/jarvis" ]; then
    echo "Expected the repo at $TARGET_HOME/jarvis but found it at $REPO_DIR." >&2
    echo "The generated unit uses \$HOME/jarvis; clone it there or edit the paths." >&2
    exit 1
fi

if [ ! -x "$REPO_DIR/.venv/bin/python" ]; then
    echo "No venv at $REPO_DIR/.venv -- run: python3 -m venv .venv && .venv/bin/pip install -e ." >&2
    exit 1
fi

if [ ! -f "$REPO_DIR/ui/dist/index.html" ]; then
    echo "WARNING: ui/dist/index.html is missing, so the kiosk will show an error page." >&2
    echo "         Build it with: cd ui && npm ci && npm run build" >&2
fi

echo "Installing for user '$TARGET_USER' (home: $TARGET_HOME)"

TARGET_UID=$(id -u "$TARGET_USER")

sed -e "s#@USER@#$TARGET_USER#g" -e "s#@HOME@#$TARGET_HOME#g" -e "s#@UID@#$TARGET_UID#g" \
    "$REPO_DIR/deploy/jarvis.service.in" > /etc/systemd/system/jarvis.service
echo "  wrote /etc/systemd/system/jarvis.service"

systemctl daemon-reload
systemctl enable jarvis >/dev/null
# restart, not "enable --now": --now only starts a stopped unit, so re-running
# this script would rewrite the unit file and leave the old process running with
# the old settings.
systemctl restart jarvis
echo "  service enabled and (re)started"

# Kiosk autostart. Bookworm on a Pi 5 defaults to Wayland (wayfire/labwc), which
# does not read ~/.config/autostart the way the old X11/LXDE session did.
AUTOSTART_DIR="$TARGET_HOME/.config/autostart"
install -d -o "$TARGET_USER" -g "$TARGET_USER" "$AUTOSTART_DIR"
install -o "$TARGET_USER" -g "$TARGET_USER" -m 644 \
    "$REPO_DIR/deploy/kiosk.desktop" "$AUTOSTART_DIR/kiosk.desktop"
echo "  wrote $AUTOSTART_DIR/kiosk.desktop"

case "${XDG_SESSION_TYPE:-}" in
    wayland)
        echo "  NOTE: this session is Wayland, which may not read ~/.config/autostart." \
             "If the kiosk does not appear after a reboot, that is why -- see the" \
             "kiosk notes in README.md." ;;
esac

echo
echo "Done. Verify with:"
echo "  systemctl status jarvis"
echo "  journalctl -u jarvis -f"
echo "Then reboot and confirm both the service and the kiosk come back on their own."
