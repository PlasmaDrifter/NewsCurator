#!/usr/bin/env bash
# NewsCurator Installer
# Supports two installation modes:
#   1. Podman Quadlet (rootless container service)
#   2. Native Python (venv + systemd user service)

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
QUADLET_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/containers/systemd"
SYSTEMD_USER_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
DESKTOP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICON_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"
DATA_DIR="${REPO_DIR}/app/data"
VENV_DIR="${REPO_DIR}/app/.venv"

echo "========================================="
echo "       NewsCurator Installation         "
echo "========================================="
echo ""
echo "Choose installation mode:"
echo "  1) Podman Quadlet (Recommended for isolated containers)"
echo "  2) Native Python (Virtualenv + systemd service, no Podman needed)"
echo ""

# Default mode or parse flag / prompt
MODE="${1:-}"
if [ -z "${MODE}" ]; then
    if [ -t 0 ]; then
        read -rp "Select mode [1/2] (default: 1): " CHOICE
        case "${CHOICE}" in
            2|native|python) MODE="native" ;;
            *) MODE="podman" ;;
        esac
    else
        MODE="podman"
    fi
fi

if ! command -v systemctl >/dev/null 2>&1; then
    echo "Error: systemctl is not available on this system."
    exit 1
fi

mkdir -p "${DESKTOP_DIR}" "${ICON_DIR}" "${DATA_DIR}"

if [ "${MODE}" = "podman" ] || [ "${MODE}" = "1" ]; then
    echo ""
    echo "--- Installing via Podman Quadlet ---"
    if ! command -v podman >/dev/null 2>&1; then
        echo "Error: podman is not installed or not in PATH."
        echo "Please install Podman or choose Native Python mode (./install.sh native)."
        exit 1
    fi

    mkdir -p "${QUADLET_DIR}"

    echo "Building Podman container image (localhost/newscurator:latest)..."
    podman build -t localhost/newscurator:latest -f "${REPO_DIR}/app/Containerfile" "${REPO_DIR}/app"

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

    # Clean up native systemd service if previously installed
    rm -f "${SYSTEMD_USER_DIR}/newscurator.service"

elif [ "${MODE}" = "native" ] || [ "${MODE}" = "2" ]; then
    echo ""
    echo "--- Installing via Native Python (venv) ---"
    PYTHON_BIN="$(command -v python3 || echo "")"
    if [ -z "${PYTHON_BIN}" ]; then
        echo "Error: python3 is not installed or not in PATH."
        exit 1
    fi

    mkdir -p "${SYSTEMD_USER_DIR}"

    if [ ! -d "${VENV_DIR}" ]; then
        echo "Creating Python virtual environment in ${VENV_DIR}..."
        "${PYTHON_BIN}" -m venv "${VENV_DIR}"
    fi

    echo "Installing Python dependencies from requirements.txt..."
    "${VENV_DIR}/bin/pip" install --upgrade pip
    "${VENV_DIR}/bin/pip" install -r "${REPO_DIR}/app/requirements.txt"

    echo "Creating systemd user service unit..."
    cat > "${SYSTEMD_USER_DIR}/newscurator.service" <<SERVICE_EOF
[Unit]
Description=News Curator
After=network-online.target

[Service]
Type=simple
WorkingDirectory=${REPO_DIR}/app
ExecStart=${VENV_DIR}/bin/uvicorn app.main:app --host 0.0.0.0 --port 5006
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
SERVICE_EOF

    # Clean up Quadlet file if previously installed
    rm -f "${QUADLET_DIR}/newscurator.container"
else
    echo "Unknown mode: ${MODE}. Use 'podman' (1) or 'native' (2)."
    exit 1
fi

# Install application icon
if [ -f "${REPO_DIR}/app/static/favicons/newspaper.svg" ]; then
    cp -f "${REPO_DIR}/app/static/favicons/newspaper.svg" "${ICON_DIR}/newscurator.svg"
    echo "Installed icon: ${ICON_DIR}/newscurator.svg"
fi

# Install desktop launcher
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

# Refresh desktop & icon caches
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "${DESKTOP_DIR}" >/dev/null 2>&1 || true
fi
if command -v kbuildsycoca6 >/dev/null 2>&1; then
    kbuildsycoca6 --noincremental >/dev/null 2>&1 || true
elif command -v kbuildsycoca5 >/dev/null 2>&1; then
    kbuildsycoca5 --noincremental >/dev/null 2>&1 || true
fi

# Reload systemd and start service
echo "Reloading systemd user daemon..."
systemctl --user daemon-reload

echo "Starting newscurator service..."
systemctl --user start newscurator.service
systemctl --user enable newscurator.service

echo ""
echo "========================================="
echo "NewsCurator successfully installed (${MODE} mode) and running!"
echo "Dashboard URL:  http://localhost:5006"
echo "Service Status: systemctl --user status newscurator.service"
echo "========================================="
