#!/usr/bin/env python3
"""
NewsCurator Desktop Launcher (Standalone Desktop App)
Launches the FastAPI backend server in a background daemon thread
and presents the interface in a native desktop window via pywebview.
"""
import os
import sys
import time
import socket
import shutil
import threading
from pathlib import Path
import webview

# Determine bundle directory (supports PyInstaller frozen mode)
if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    BUNDLE_DIR = Path(sys._MEIPASS)
else:
    BUNDLE_DIR = Path(__file__).resolve().parent

# Ensure the app directories are on sys.path
app_path = BUNDLE_DIR / "app"
if str(app_path) not in sys.path:
    sys.path.insert(0, str(app_path))
if str(BUNDLE_DIR) not in sys.path:
    sys.path.insert(0, str(BUNDLE_DIR))

try:
    from app.app.main import app
except ImportError:
    try:
        from app.main import app
    except ImportError:
        from main import app


def find_free_port(default_port=5006):
    """Attempt default port 5006; fallback to an ephemeral port if occupied."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(('127.0.0.1', default_port))
            return default_port
        except OSError:
            pass
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def run_server(port):
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


def setup_desktop_integration(title, icon_path):
    """Register application name, prgname and icons for Wayland and X11."""
    if sys.platform == 'win32' or os.name == 'nt':
        return
    try:
        import gi
        gi.require_version('Gtk', '3.0')
        from gi.repository import Gtk, GLib

        GLib.set_prgname("newscurator")
        GLib.set_application_name(title)

        if icon_path.exists():
            Gtk.Window.set_default_icon_from_file(str(icon_path))
    except Exception as e:
        print(f"GTK icon setup note: {e}", file=sys.stderr)

    # Register user desktop entry & icon so Wayland compositor / dock displays icon
    try:
        home = Path.home()
        icon_dir = home / ".local" / "share" / "icons" / "hicolor" / "scalable" / "apps"
        icon_dir.mkdir(parents=True, exist_ok=True)
        dest_icon = icon_dir / "newscurator.svg"
        if icon_path.exists():
            shutil.copy2(icon_path, dest_icon)

        apps_dir = home / ".local" / "share" / "applications"
        apps_dir.mkdir(parents=True, exist_ok=True)
        desktop_file = apps_dir / "newscurator.desktop"
        if getattr(sys, "frozen", False):
            exec_cmd = f'"{Path(sys.executable).resolve()}"'
        else:
            exec_cmd = f'"{sys.executable}" "{Path(__file__).resolve()}"'

        desktop_content = f"""[Desktop Entry]
Name={title}
Comment=Modern RSS News Curator & Aggregator
Exec={exec_cmd}
Icon=newscurator
Terminal=false
Type=Application
Categories=News;Feed;Network;
StartupWMClass=newscurator
"""
        desktop_file.write_text(desktop_content)
    except Exception as e:
        print(f"Desktop entry registration note: {e}", file=sys.stderr)


def main():
    port = find_free_port(5006)
    server_thread = threading.Thread(target=run_server, args=(port,), daemon=True)
    server_thread.start()

    # Wait briefly for uvicorn to bind
    for _ in range(30):
        time.sleep(0.1)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(('127.0.0.1', port)) == 0:
                break

    title = "News Curator"
    candidates = [
        BUNDLE_DIR / "app" / "static" / "favicons" / "newspaper.svg",
        BUNDLE_DIR / "static" / "favicons" / "newspaper.svg",
    ]
    icon_path = next((p for p in candidates if p.exists()), candidates[0])

    setup_desktop_integration(title, icon_path)

    window = webview.create_window(
        title=title,
        url=f'http://127.0.0.1:{port}',
        width=1440,
        height=900,
        min_size=(960, 600),
        background_color='#0e1117'
    )
    webview.start()


if __name__ == '__main__':
    main()
