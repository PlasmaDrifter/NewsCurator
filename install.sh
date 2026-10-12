#!/usr/bin/env bash
# NewsCurator Installer
# Builds the local Podman image, installs the systemd Quadlet container,
# application icon, and desktop application launcher.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
QUADLET_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/containers/systemd"
DESKTOP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICON_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"
DATA_DIR="${REPO_DIR}/app/data"

echo "========================================="
echo "       NewsCurator Installation         "
echo "========================================="

# 1. Check prerequisites
if ! command -v podman >/dev/null 2>&1; then
    echo "Error: podman is not installed or not in PATH."
    echo "Please install Podman before running this installer."
    exit 1
fi

if ! command -v systemctl >/dev/null 2>&1; then
    echo "Error: systemctl is not available on this system."
    exit 1
fi

# 2. Prepare directories
mkdir -p "${QUADLET_DIR}" "${DESKTOP_DIR}" "${ICON_DIR}" "${DATA_DIR}"

# 3. Build Podman container image
echo "Building Podman container image (localhost/newscurator:latest)..."
podman build -t localhost/newscurator:latest -f "${REPO_DIR}/app/Containerfile" "${REPO_DIR}/app"

# 4. Generate systemd Quadlet container file with dynamic repository path
echo "Configuring systemd Quadlet container definition..."
cat > "${QUADLET_DIR}/newscurator.container" <<QUADLET_EOF
[Unit]
Description=News Curator
After=network-online.target

[Container]
Image=localhost/newscurator:latest
ContainerName=newscurator
PublishPort=5006:5006
Volume=${REPO_DIR}/app:/app:Z
Volume=${REPO_DIR}/app/data:/app/data:Z
AutoUpdate=local

[Service]
Restart=on-failure

[Install]
WantedBy=default.target
QUADLET_EOF

# 5. Install application icon
if [ -f "${REPO_DIR}/app/static/favicons/newspaper.svg" ]; then
    cp -f "${REPO_DIR}/app/static/favicons/newspaper.svg" "${ICON_DIR}/newscurator.svg"
    echo "Installed icon: ${ICON_DIR}/newscurator.svg"
fi

# 6. Install desktop launcher
echo "Creating desktop application launcher..."
cat > "${DESKTOP_DIR}/newscurator.desktop" <<DESKTOP_EOF
[Desktop Entry]
Type=Application
Name=News Curator
GenericName=RSS News Dashboard
Comment=Self-hosted RSS news aggregator and dashboard
Exec=xdg-open http://localhost:5006
Icon=newscurator
Terminal=false
Categories=Network;News;Feed;
Keywords=rss;news;feed;curator;aggregator;articles;
DESKTOP_EOF

# Update desktop & icon caches if tools are present
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "${DESKTOP_DIR}" >/dev/null 2>&1 || true
fi
if command -v kbuildsycoca6 >/dev/null 2>&1; then
    kbuildsycoca6 --noincremental >/dev/null 2>&1 || true
elif command -v kbuildsycoca5 >/dev/null 2>&1; then
    kbuildsycoca5 --noincremental >/dev/null 2>&1 || true
fi

# 7. Reload systemd daemon and restart Quadlet service
echo "Reloading user systemd daemon..."
systemctl --user daemon-reload

echo "Starting newscurator service..."
systemctl --user start newscurator.service
systemctl --user enable newscurator.service

echo ""
echo "========================================="
echo "NewsCurator successfully installed and running!"
echo "Dashboard URL: http://localhost:5006"
echo "Service Status: systemctl --user status newscurator.service"
echo "========================================="
