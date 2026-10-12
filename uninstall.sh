#!/usr/bin/env bash
# NewsCurator Uninstaller
# Stops and removes systemd service (Quadlet or native), container, desktop launcher, and icon.
# Preserves data database unless specifically removed.

set -euo pipefail

QUADLET_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/containers/systemd"
SYSTEMD_USER_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
DESKTOP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICON_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"
SERVICE_NAME="newscurator.service"

echo "========================================="
echo "      NewsCurator Uninstallation        "
echo "========================================="

# 1. Stop and disable systemd service
if command -v systemctl >/dev/null 2>&1; then
    if systemctl --user is-active --quiet "${SERVICE_NAME}" 2>/dev/null; then
        echo "Stopping systemd service (${SERVICE_NAME})..."
        systemctl --user stop "${SERVICE_NAME}"
    fi

    if systemctl --user is-enabled --quiet "${SERVICE_NAME}" 2>/dev/null; then
        echo "Disabling systemd service (${SERVICE_NAME})..."
        systemctl --user disable "${SERVICE_NAME}"
    fi
fi

# 2. Stop and remove Podman container instance if running
if command -v podman >/dev/null 2>&1; then
    if podman ps -a --format '{{.Names}}' | grep -qx "newscurator"; then
        echo "Removing Podman container instance (newscurator)..."
        podman rm -f "newscurator" >/dev/null 2>&1 || true
    fi
fi

# 3. Stop any background uvicorn processes if running natively
pkill -f "uvicorn app.main:app" 2>/dev/null || true

# 4. Remove installed Quadlet configuration, native service, desktop entry, and icon
echo "Removing service definitions, desktop entry, and icon..."
rm -f "${QUADLET_DIR}/newscurator.container"
rm -f "${SYSTEMD_USER_DIR}/${SERVICE_NAME}"
rm -f "${DESKTOP_DIR}/newscurator.desktop"
rm -f "${ICON_DIR}/newscurator.svg"

# 5. Reload systemd daemon
if command -v systemctl >/dev/null 2>&1; then
    echo "Reloading systemd daemon..."
    systemctl --user daemon-reload
fi

# 6. Refresh desktop and icon databases
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "${DESKTOP_DIR}" >/dev/null 2>&1 || true
fi
if command -v kbuildsycoca6 >/dev/null 2>&1; then
    kbuildsycoca6 --noincremental >/dev/null 2>&1 || true
elif command -v kbuildsycoca5 >/dev/null 2>&1; then
    kbuildsycoca5 --noincremental >/dev/null 2>&1 || true
fi

echo ""
echo "========================================="
echo "NewsCurator has been uninstalled."
echo "Note: Your database data in app/data/news.db was preserved."
echo "========================================="
