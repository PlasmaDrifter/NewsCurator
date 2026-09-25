import os
import sys
import shutil
import re
import sqlite3
import time
import threading
import hashlib
import json
import socket
import urllib.request
import collections
import subprocess
import tarfile
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from pathlib import Path

try:
    LOCAL_TZ = ZoneInfo(os.environ.get("APP_TIMEZONE", os.environ.get("TZ", "America/Phoenix")))
except Exception:
    LOCAL_TZ = timezone.utc

def get_local_now_str() -> str:
    return datetime.now(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")

APP_VERSION = "v0.7.9"
GITHUB_REPO = "PlasmaDrifter/NewsCurator"

UPDATE_CACHE = {
    "last_checked": 0,
    "latest_version": APP_VERSION,
    "release_url": f"https://github.com/{GITHUB_REPO}/releases",
    "has_update": False,
    "lock": threading.Lock(),
}

def parse_version_tuple(v_str: str):
    if not v_str:
        return (0, 0, 0)
    cleaned = re.sub(r'^[vV]', '', str(v_str).strip())
    parts = []
    for p in re.split(r'[-.+_]', cleaned):
        if p.isdigit():
            parts.append(int(p))
        else:
            m = re.match(r'(\d+)', p)
            if m:
                parts.append(int(m.group(1)))
    return tuple(parts)

def is_newer_version(latest: str, current: str) -> bool:
    try:
        return parse_version_tuple(latest) > parse_version_tuple(current)
    except Exception:
        return False

def check_github_update(force=False, enabled=True):
    if not enabled:
        return {
            "has_update": False,
            "latest_version": APP_VERSION,
            "release_url": f"https://github.com/{GITHUB_REPO}/releases",
            "current_version": APP_VERSION,
            "check_enabled": False,
        }

    now = time.time()
    # Cache for 1 hour (3600 seconds) unless forced
    with UPDATE_CACHE["lock"]:
        if not force and (now - UPDATE_CACHE["last_checked"] < 3600) and UPDATE_CACHE["last_checked"] > 0:
            return {
                "has_update": UPDATE_CACHE["has_update"],
                "latest_version": UPDATE_CACHE["latest_version"],
                "release_url": UPDATE_CACHE["release_url"],
                "current_version": APP_VERSION,
                "check_enabled": True,
            }

    try:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": f"NewsCurator-UpdateChecker/{APP_VERSION}",
                "Accept": "application/vnd.github.v3+json"
            }
        )
        with urllib.request.urlopen(req, timeout=4) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            tag = data.get("tag_name", "").strip()
            html_url = data.get("html_url") or f"https://github.com/{GITHUB_REPO}/releases"
            has_update = bool(tag and is_newer_version(tag, APP_VERSION))

            with UPDATE_CACHE["lock"]:
                UPDATE_CACHE["last_checked"] = now
                UPDATE_CACHE["latest_version"] = tag or APP_VERSION
                UPDATE_CACHE["release_url"] = html_url
                UPDATE_CACHE["has_update"] = has_update

            return {
                "has_update": has_update,
                "latest_version": tag or APP_VERSION,
                "release_url": html_url,
                "current_version": APP_VERSION,
                "check_enabled": True,
            }
    except Exception:
        with UPDATE_CACHE["lock"]:
            # On error, wait 10 min before next attempt to avoid spamming
            UPDATE_CACHE["last_checked"] = now - 3000
            return {
                "has_update": UPDATE_CACHE["has_update"],
                "latest_version": UPDATE_CACHE["latest_version"],
                "release_url": UPDATE_CACHE["release_url"],
                "current_version": APP_VERSION,
                "check_enabled": True,
            }

# In-Memory Rotating Log Buffer (holds latest 1,000 log lines)
class InMemoryLogBuffer:
    def __init__(self, maxlen=1000):
        self.buffer = collections.deque(maxlen=maxlen)
        self.lock = threading.Lock()

    def append(self, line: str):
        if not line:
            return
        with self.lock:
            self.buffer.append(line)

    def get_logs(self, limit=500, search=""):
        with self.lock:
            logs = list(self.buffer)
        if search:
            s = search.lower()
            logs = [line for line in logs if s in line.lower()]
        return logs[-limit:]

    def clear(self):
        with self.lock:
            self.buffer.clear()

log_buffer = InMemoryLogBuffer(maxlen=1000)

class TeeStream:
    def __init__(self, original_stream, buffer_obj):
        self.original_stream = original_stream
        self.buffer_obj = buffer_obj
        self.line_buf = ""
        self.lock = threading.Lock()

    def write(self, data):
        try:
            self.original_stream.write(data)
        except Exception:
            pass
        if not data:
            return
        with self.lock:
            self.line_buf += data
            while "\n" in self.line_buf:
                line, self.line_buf = self.line_buf.split("\n", 1)
                stripped = line.strip()
                if stripped:
                    if not stripped.startswith("["):
                        ts = get_local_now_str()
                        stripped = f"[{ts}] {stripped}"
                    self.buffer_obj.append(stripped)

    def flush(self):
        try:
            self.original_stream.flush()
        except Exception:
            pass

sys.stdout = TeeStream(sys.stdout, log_buffer)
sys.stderr = TeeStream(sys.stderr, log_buffer)

# Safeguard against hung/unresponsive remote feed sockets blocking background worker threads
socket.setdefaulttimeout(20.0)

import feedparser
from fastapi import FastAPI, Form, Request, UploadFile, File, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

try:
    from googlenewsdecoder import gnewsdecoder
except Exception:
    gnewsdecoder = None

# Base directory for static files and templates (supports PyInstaller bundle extraction)
if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    BUNDLE_DIR = Path(sys._MEIPASS)
else:
    BUNDLE_DIR = Path(__file__).resolve().parent.parent

# Persistent data directory for SQLite DB and user-uploaded favicons
def get_data_dir() -> Path:
    env_dir = os.environ.get("NEWSCURATOR_DATA_DIR")
    if env_dir:
        return Path(env_dir).resolve()
    if getattr(sys, "frozen", False):
        if sys.platform == "win32":
            base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        elif sys.platform == "darwin":
            base = Path.home() / "Library" / "Application Support"
        else:
            base = Path.home() / ".local" / "share"
        return base / "newscurator"
    return BUNDLE_DIR / "data"

DATA_DIR = get_data_dir()
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "news.db"

# Persistent uploads directory
UPLOAD_DIR = DATA_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Copy any legacy static uploads to persistent directory if not present
bundle_uploads = BUNDLE_DIR / "static" / "favicons" / "uploads"
if bundle_uploads.exists() and bundle_uploads.is_dir():
    for item in bundle_uploads.iterdir():
        dest = UPLOAD_DIR / item.name
        if not dest.exists() and item.is_file():
            try:
                shutil.copy2(item, dest)
            except Exception:
                pass

app = FastAPI(title="News Curator")

# Mount persistent uploads first so /static/favicons/uploads/... routes here seamlessly
app.mount("/static/favicons/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploaded_favicons")
app.mount("/static", StaticFiles(directory=str(BUNDLE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BUNDLE_DIR / "templates"))

MONTH_ABBR = {
    1: "Jan.", 2: "Feb.", 3: "Mar.", 4: "Apr.", 5: "May",
    6: "June", 7: "July", 8: "Aug.", 9: "Sept.", 10: "Oct.", 11: "Nov.", 12: "Dec."
}

def format_date(value):
    if not value:
        return ""
    import email.utils
    from zoneinfo import ZoneInfo
    from datetime import datetime
    try:
        parsed = email.utils.parsedate_tz(value)
        if parsed:
            ts = email.utils.mktime_tz(parsed)
            dt = datetime.fromtimestamp(ts, tz=ZoneInfo("America/Los_Angeles"))
            month_str = MONTH_ABBR.get(dt.month, dt.strftime("%b"))
            return f"[ {month_str} {dt.day} - {dt.strftime('%H:%M')} ]"
        try:
            val_clean = value.replace("Z", "+00:00")
            dt = datetime.fromisoformat(val_clean)
            dt = dt.astimezone(ZoneInfo("America/Los_Angeles"))
            month_str = MONTH_ABBR.get(dt.month, dt.strftime("%b"))
            return f"[ {month_str} {dt.day} - {dt.strftime('%H:%M')} ]"
        except Exception:
            pass
    except Exception:
        pass
    return str(value)[:25]

templates.env.filters["format_date"] = format_date

def to_css_class(name: str) -> str:
    if not name:
        return "default"
    slug = re.sub(r'[^a-zA-Z0-9_-]+', '-', str(name)).strip('-')
    return slug or "default"

templates.env.filters["css_class"] = to_css_class


def format_time_ago(iso_str):
    if not iso_str:
        return "never"
    try:
        dt = datetime.fromisoformat(iso_str)
        now = datetime.now(timezone.utc)
        diff = now - dt
        seconds = diff.total_seconds()
        if seconds < 0:
            return "just now"
        if seconds < 60:
            return f"{int(seconds)}s ago"
        minutes = seconds / 60
        if minutes < 60:
            return f"{int(minutes)}m ago"
        hours = minutes / 60
        if hours < 24:
            return f"{int(hours)}h ago"
        days = hours / 24
        return f"{int(days)}d ago"
    except Exception:
        return "unknown"

templates.env.filters["format_time_ago"] = format_time_ago

def is_older_than_day(iso_str):
    if not iso_str:
        return True
    try:
        dt = datetime.fromisoformat(iso_str)
        now = datetime.now(timezone.utc)
        return (now - dt).total_seconds() > 86400
    except Exception:
        return False

templates.env.filters["is_older_than_day"] = is_older_than_day

def extract_domain(url):
    try:
        from urllib.parse import urlparse
        host = urlparse(url).netloc
        return host.removeprefix("www.")
    except Exception:
        return ""

templates.env.filters["extract_domain"] = extract_domain

def format_fetched_minutes(fetched_at_str):
    if not fetched_at_str:
        return ""
    try:
        dt = datetime.fromisoformat(fetched_at_str)
        now = datetime.now(timezone.utc)
        diff = now - dt
        total_minutes = int(diff.total_seconds() // 60)
        if total_minutes < 0:
            total_minutes = 0

        # Tier 1: Under 180 minutes (0 to 179m)
        if total_minutes < 180:
            return f"{total_minutes}m"

        # Tier 2: 180 to 1439 minutes (3h 0m to 23h 59m)
        if total_minutes < 1440:
            hours = total_minutes // 60
            mins = total_minutes % 60
            return f"{hours}h {mins}m"

        # Tier 3: 1440+ minutes (1d+ with days, hours, and minutes)
        days = total_minutes // 1440
        rem = total_minutes % 1440
        hours = rem // 60
        mins = rem % 60
        return f"{days}d {hours}h {mins}m"
    except Exception:
        return ""

templates.env.filters["format_fetched_minutes"] = format_fetched_minutes

PRESET_FAVICONS = [
    {"name": "newspaper", "title": "Newspaper", "path": "/static/favicons/newspaper.svg"},
    {"name": "rss", "title": "RSS Wave", "path": "/static/favicons/rss.svg"},
    {"name": "globe", "title": "World Globe", "path": "/static/favicons/globe.svg"},
    {"name": "bookmark", "title": "Bookmark", "path": "/static/favicons/bookmark.svg"},
    {"name": "lightning", "title": "Lightning", "path": "/static/favicons/lightning.svg"},
    {"name": "linux-tux", "title": "Linux Tux", "path": "/static/favicons/linux-tux.svg"},
]


def get_active_favicon():
    with closing(get_db()) as conn:
        return get_setting(conn, "favicon", "/static/favicons/newspaper.svg")


def get_favicon_version():
    with closing(get_db()) as conn:
        return get_setting(conn, "favicon_version", "1")


templates.env.globals["get_active_favicon"] = get_active_favicon
templates.env.globals["get_favicon_version"] = get_favicon_version
templates.env.globals["PRESET_FAVICONS"] = PRESET_FAVICONS


DEFAULT_FEEDS = [
    # General computing / tech
    ("Ars Technica", "https://feeds.arstechnica.com/arstechnica/index", "Computing"),
    ("The Verge", "https://www.theverge.com/rss/index.xml", "Computing"),
    ("Hacker News (front page)", "https://hnrss.org/frontpage", "Computing"),
    ("TechCrunch", "https://techcrunch.com/feed/", "Computing"),
    # Linux
    ("Phoronix", "https://www.phoronix.com/rss.php", "Linux"),
    ("It's FOSS", "https://itsfoss.com/feed/", "Linux"),
    ("LWN.net Headlines", "https://lwn.net/headlines/rss", "Linux"),
    ("OMG! Ubuntu", "https://www.omgubuntu.co.uk/feed", "Linux"),
    # Science
    ("Science Daily", "https://www.sciencedaily.com/rss/top/science.xml", "Science"),
    ("Phys.org", "https://phys.org/rss-feed/", "Science"),
    ("Nature News", "https://www.nature.com/nature.rss", "Science"),
    # Space
    ("NASA Breaking News", "https://www.nasa.gov/news-release/feed/", "Space"),
    ("Space.com", "https://www.space.com/feeds/all", "Space"),
    ("SpaceNews", "https://spacenews.com/feed/", "Space"),
    # Defense
    ("Covert Shores", "http://www.hisutton.com/feed.xml", "Defense"),
    ("Defense One", "https://www.defenseone.com/rss/all/", "Defense"),
    ("ISW", "https://news.google.com/rss/search?q=site%3Aunderstandingwar.org&hl=en-US&gl=US&ceid=US%3Aen", "Defense"),
]




def get_db():
    conn = sqlite3.connect(str(DB_PATH), timeout=30.0)
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.row_factory = sqlite3.Row
    return conn


def get_categories(conn):
    rows = conn.execute("""
        SELECT c.name, c.color, c.position, COUNT(f.id) AS feed_count
        FROM categories c
        LEFT JOIN feeds f ON c.name = f.category
        GROUP BY c.name
        ORDER BY c.position ASC, c.rowid ASC
    """).fetchall()
    return [dict(r) for r in rows]


def get_custom_favicons(conn):
    try:
        rows = conn.execute("SELECT * FROM custom_favicons ORDER BY id ASC").fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def get_custom_themes(conn):
    try:
        rows = conn.execute("SELECT * FROM custom_themes ORDER BY id DESC").fetchall()
        themes = []
        for r in rows:
            try:
                colors = json.loads(r["colors_json"])
            except Exception:
                colors = dict(THEME_MAP["default"]["colors"])
            themes.append({
                "id": f"custom_{r['id']}",
                "db_id": r["id"],
                "name": r["name"],
                "colors": colors,
                "created_at": r["created_at"],
            })
        return themes
    except Exception:
        return []


def get_setting(conn, key, default="0"):
    try:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default
    except Exception:
        return default


def set_setting(conn, key, value):
    try:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
        conn.commit()
    except Exception as e:
        print(f"Error setting {key}: {e}")


THEME_PRESETS = [
    {
        "id": "default",
        "name": "Default Dark",
        "description": "Charcoal background, slate cards & electric blue",
        "colors": {
            "bg": "#14161b",
            "bg_elevated": "#1c1f26",
            "bg_card": "#21242c",
            "border": "#2f333d",
            "text": "#e6e6e6",
            "text_dim": "#9aa0ab",
            "accent": "#5b8cff",
        },
    },
    {
        "id": "midnight",
        "name": "Midnight OLED",
        "description": "True deep black for OLED displays with sky blue",
        "colors": {
            "bg": "#000000",
            "bg_elevated": "#0a0a0c",
            "bg_card": "#121214",
            "border": "#242428",
            "text": "#f2f2f2",
            "text_dim": "#8c8c96",
            "accent": "#38bdf8",
        },
    },
    {
        "id": "nord",
        "name": "Nord Frost",
        "description": "Arctic blue-grey slate & cool frost cyan",
        "colors": {
            "bg": "#242933",
            "bg_elevated": "#2e3440",
            "bg_card": "#3b4252",
            "border": "#4c566a",
            "text": "#eceff4",
            "text_dim": "#d8dee9",
            "accent": "#88c0d0",
        },
    },
    {
        "id": "dracula",
        "name": "Dracula",
        "description": "Gothic midnight purple & orchid lilac",
        "colors": {
            "bg": "#1e1f29",
            "bg_elevated": "#21222c",
            "bg_card": "#282a36",
            "border": "#44475a",
            "text": "#f8f8f2",
            "text_dim": "#6272a4",
            "accent": "#bd93f9",
        },
    },
    {
        "id": "solarized",
        "name": "Solarized Dark",
        "description": "Teal and deep cyan oceanic night",
        "colors": {
            "bg": "#00212b",
            "bg_elevated": "#002b36",
            "bg_card": "#073642",
            "border": "#586e75",
            "text": "#93a1a1",
            "text_dim": "#657b83",
            "accent": "#268bd2",
        },
    },
    {
        "id": "emerald",
        "name": "Emerald Forest",
        "description": "Rich deep moss, pine green & vibrant emerald",
        "colors": {
            "bg": "#0a1510",
            "bg_elevated": "#0e1f18",
            "bg_card": "#132a21",
            "border": "#21493a",
            "text": "#e3f4ec",
            "text_dim": "#8cb8a3",
            "accent": "#10b981",
        },
    },
    {
        "id": "cyberpunk",
        "name": "Cyberpunk Neon",
        "description": "Deep synthwave void with hot neon magenta",
        "colors": {
            "bg": "#0b0914",
            "bg_elevated": "#120e24",
            "bg_card": "#1b1536",
            "border": "#352968",
            "text": "#f5efff",
            "text_dim": "#a49ec2",
            "accent": "#f72585",
        },
    },
    {
        "id": "espresso",
        "name": "Warm Espresso",
        "description": "Dark roasted coffee, warm cocoa & amber honey",
        "colors": {
            "bg": "#161311",
            "bg_elevated": "#1f1b18",
            "bg_card": "#2b2521",
            "border": "#473d36",
            "text": "#f5f0eb",
            "text_dim": "#ab9e94",
            "accent": "#d97706",
        },
    },
]

THEME_MAP = {p["id"]: p for p in THEME_PRESETS}


def get_theme_version():
    with closing(get_db()) as conn:
        return get_setting(conn, "theme_version", "1")


def get_active_theme_config(conn):
    active_theme = get_setting(conn, "active_theme", "default")
    custom_json = get_setting(conn, "theme_custom_colors", "")
    colors = dict(THEME_MAP["default"]["colors"])

    if active_theme in THEME_MAP:
        colors = dict(THEME_MAP[active_theme]["colors"])
        theme_name = THEME_MAP[active_theme]["name"]
    elif active_theme.startswith("custom_"):
        try:
            db_id = int(active_theme.removeprefix("custom_"))
            row = conn.execute("SELECT name, colors_json FROM custom_themes WHERE id = ?", (db_id,)).fetchone()
            if row:
                theme_name = row["name"]
                user_colors = json.loads(row["colors_json"])
                for k in ["bg", "bg_elevated", "bg_card", "border", "text", "text_dim", "accent"]:
                    if k in user_colors and user_colors[k]:
                        colors[k] = user_colors[k]
            else:
                active_theme = "default"
                theme_name = THEME_MAP["default"]["name"]
        except Exception:
            active_theme = "default"
            theme_name = THEME_MAP["default"]["name"]
    elif active_theme == "custom":
        theme_name = "Custom Theme"
        if custom_json:
            try:
                user_colors = json.loads(custom_json)
                if isinstance(user_colors, dict):
                    for k in ["bg", "bg_elevated", "bg_card", "border", "text", "text_dim", "accent"]:
                        if k in user_colors and user_colors[k]:
                            colors[k] = user_colors[k]
            except Exception:
                pass
    else:
        active_theme = "default"
        theme_name = THEME_MAP["default"]["name"]

    return {
        "active_theme": active_theme,
        "theme_name": theme_name,
        "colors": colors,
    }


templates.env.globals["get_theme_version"] = get_theme_version
templates.env.globals["THEME_PRESETS"] = THEME_PRESETS



def hex_to_dark_bg(hex_color, alpha=0.15):
    """Darken a hex color for use as a background."""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


def hex_to_rgba(hex_color, alpha=1.0):
    if not hex_color:
        return ""
    h = hex_color.lstrip("#")
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return f"rgba({r},{g},{b},{alpha})"
    except Exception:
        return hex_color


templates.env.filters["hex_to_rgba"] = hex_to_rgba


def init_db():
    with closing(get_db()) as conn, conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('colored_borders', '0')")
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('border_opacity', '0.5')")
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('border_size', '3')")
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('retention_days', '14')")
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('refresh_interval', '30')")
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('favicon', '/static/favicons/newspaper.svg')")
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('favicon_version', '1')")
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('open_in_new_tab', '1')")
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('step_scroll_rows', '3')")
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('anim_cascade', '1')")
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('enable_unread_filter', '1')")
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('unread_icon_only', '0')")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS categories (
                name TEXT PRIMARY KEY,
                color TEXT NOT NULL DEFAULT '#888888',
                position INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS custom_favicons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                path TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS custom_themes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                colors_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        # Migrate: add color column if it doesn't exist yet
        try:
            conn.execute("ALTER TABLE categories ADD COLUMN color TEXT NOT NULL DEFAULT '#888888'")
        except Exception:
            pass
        # Migrate: add position column if it doesn't exist yet
        try:
            conn.execute("ALTER TABLE categories ADD COLUMN position INTEGER NOT NULL DEFAULT 0")
        except Exception:
            pass
        # Initialize sequential positions if categories exist with all 0s
        try:
            cats = conn.execute("SELECT name, position FROM categories ORDER BY position ASC, rowid ASC").fetchall()
            if cats and all(c["position"] == 0 for c in cats):
                for idx, c in enumerate(cats):
                    conn.execute("UPDATE categories SET position = ? WHERE name = ?", (idx, c["name"]))
        except Exception:
            pass
        conn.execute("""
            CREATE TABLE IF NOT EXISTS feeds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                url TEXT NOT NULL UNIQUE,
                category TEXT NOT NULL DEFAULT 'computing',
                enabled INTEGER NOT NULL DEFAULT 1,
                last_fetched TEXT,
                last_error TEXT
            )
        """)
        try:
            conn.execute("ALTER TABLE feeds ADD COLUMN last_fetched TEXT")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE feeds ADD COLUMN last_error TEXT")
        except Exception:
            pass
        conn.execute("""
            CREATE TABLE IF NOT EXISTS articles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                feed_id INTEGER NOT NULL,
                guid TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                link TEXT NOT NULL,
                summary TEXT,
                image_url TEXT,
                published TEXT,
                fetched_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'unread',
                FOREIGN KEY(feed_id) REFERENCES feeds(id)
            )
        """)
        try:
            conn.execute("ALTER TABLE articles ADD COLUMN is_bookmarked INTEGER NOT NULL DEFAULT 0")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE articles ADD COLUMN content TEXT")
        except Exception:
            pass
        conn.execute("CREATE INDEX IF NOT EXISTS idx_articles_status ON articles(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_articles_published ON articles(published)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_articles_bookmarked ON articles(is_bookmarked)")

        # Seed default categories with colors if not already present (case-insensitive)
        for cat_name, cat_color in DEFAULT_CATEGORIES:
            exists = conn.execute("SELECT 1 FROM categories WHERE LOWER(name) = LOWER(?)", (cat_name,)).fetchone()
            if not exists:
                conn.execute(
                    "INSERT INTO categories (name, color) VALUES (?, ?)",
                    (cat_name, cat_color),
                )

        # Migration: seed any categories already in feeds table if not present
        for row in conn.execute("SELECT DISTINCT category FROM feeds"):
            exists = conn.execute("SELECT 1 FROM categories WHERE LOWER(name) = LOWER(?)", (row["category"],)).fetchone()
            if not exists:
                conn.execute(
                    "INSERT INTO categories (name, color) VALUES (?, ?)",
                    (row["category"], "#888888"),
                )

        # Migration: abbreviate Google News to G.News
        try:
            conn.execute("UPDATE feeds SET name = 'G.News' WHERE name = 'Google News'")
        except Exception:
            pass

        count = conn.execute("SELECT COUNT(*) c FROM feeds").fetchone()["c"]
        if count == 0:
            for name, url, cat in DEFAULT_FEEDS:
                conn.execute(
                    "INSERT OR IGNORE INTO feeds (name, url, category) VALUES (?, ?, ?)",
                    (name, url, cat),
                )


def extract_image(entry):
    # Try media_content, media_thumbnail, then look in links/enclosures
    if "media_content" in entry and entry.media_content:
        url = entry.media_content[0].get("url")
        if url:
            return url
    if "media_thumbnail" in entry and entry.media_thumbnail:
        url = entry.media_thumbnail[0].get("url")
        if url:
            return url
    for link in entry.get("links", []):
        if link.get("type", "").startswith("image"):
            return link.get("href")
    # Try to pull first <img> from summary/content html
    html = ""
    if "content" in entry and entry.content:
        html = entry.content[0].get("value", "")
    elif "summary" in entry:
        html = entry.summary
    if html and "<img" in html:
        import re
        m = re.search(r'<img[^>]+src="([^"]+)"', html)
        if m:
            return m.group(1)
    return None


def clean_summary(entry, max_len=500):
    import re
    text = ""
    if "summary" in entry:
        text = entry.summary
    elif "content" in entry and entry.content:
        text = entry.content[0].get("value", "")
    text = re.sub(r"<[^>]+>", " ", text)  # strip html tags
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_len:
        text = text[:max_len].rsplit(" ", 1)[0] + "…"
    return text


def fetch_feed(conn, feed_row):
    err_msg = None
    try:
        req = urllib.request.Request(
            feed_row["url"],
            headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 NewsCurator/0.7"
            }
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw_content = resp.read()
        parsed = feedparser.parse(raw_content)
        if getattr(parsed, "bozo", 0) and not getattr(parsed, "entries", None):
            err_msg = str(getattr(parsed, "bozo_exception", "Parse error"))[:120]
            print(f"Error parsing {feed_row['name']}: {err_msg}", flush=True)
    except Exception as e:
        print(f"Error fetching {feed_row['name']}: {e}", flush=True)
        err_msg = str(e)[:120]
        conn.execute(
            "UPDATE feeds SET last_fetched = ?, last_error = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), err_msg, feed_row["id"])
        )
        return 0

    conn.execute(
        "UPDATE feeds SET last_fetched = ?, last_error = ? WHERE id = ?",
        (datetime.now(timezone.utc).isoformat(), err_msg, feed_row["id"])
    )

    new_count = 0
    total_entries = len(parsed.entries) if getattr(parsed, "entries", None) else 0
    for entry in parsed.entries:
        guid = entry.get("id") or entry.get("link")
        if not guid:
            guid = hashlib.sha256(entry.get("title", "").encode()).hexdigest()

        exists = conn.execute(
            "SELECT 1 FROM articles WHERE guid = ?", (guid,)
        ).fetchone()
        if exists:
            continue

        published = entry.get("published") or entry.get("updated") or ""
        title = entry.get("title", "(no title)")
        link = entry.get("link", "")
        summary = clean_summary(entry)
        raw_content = ""
        if "content" in entry and entry.content:
            raw_content = entry.content[0].get("value", "")
        elif "summary_detail" in entry and entry.summary_detail:
            raw_content = entry.summary_detail.get("value", "")
        elif "summary" in entry:
            raw_content = entry.summary or ""
        image_url = extract_image(entry)

        conn.execute(
            """INSERT OR IGNORE INTO articles
               (feed_id, guid, title, link, summary, content, image_url, published, fetched_at, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'unread')""",
            (
                feed_row["id"], guid, title, link, summary, raw_content, image_url,
                published, datetime.now(timezone.utc).isoformat(),
            ),
        )
        new_count += 1

    if new_count > 0:
        print(f"Fetched '{feed_row['name']}': {new_count} new articles ({total_entries} total in feed)", flush=True)
    else:
        print(f"Fetched '{feed_row['name']}': 0 new articles ({total_entries} checked)", flush=True)
    return new_count


def cleanup_old_articles(days=None):
    with closing(get_db()) as conn, conn:
        if days is None:
            try:
                days = int(get_setting(conn, "retention_days", "14"))
            except Exception:
                days = 14
        cursor = conn.execute(
            """DELETE FROM articles 
               WHERE datetime(fetched_at) < datetime('now', '-' || ? || ' days')
               AND (is_bookmarked = 0 OR is_bookmarked IS NULL)""",
            (days,)
        )
        deleted = cursor.rowcount
        if deleted > 0:
            print(f"Cleaned up {deleted} articles older than {days} days", flush=True)
        else:
            print(f"Retention check: 0 articles older than {days} days to clean up", flush=True)
    return deleted


def refresh_all_feeds():
    with closing(get_db()) as conn:
        feeds = conn.execute("SELECT * FROM feeds WHERE enabled = 1").fetchall()
        total_new = 0
        feed_count = len(feeds)
        print(f"Refreshing {feed_count} enabled feeds...", flush=True)
        for feed in feeds:
            with conn:
                total_new += fetch_feed(conn, feed)
        print(f"Refresh complete: {feed_count} feeds checked, {total_new} new articles added", flush=True)
    return total_new


def background_refresher():
    while True:
        try:
            print("Starting background feed refresh...", flush=True)
            refresh_all_feeds()
            cleanup_old_articles()
            try:
                with closing(get_db()) as conn:
                    check_enabled = get_setting(conn, "check_for_updates", "1") == "1"
                if check_enabled:
                    check_github_update(force=False, enabled=True)
            except Exception:
                pass
        except Exception as e:
            print(f"Background refresh error: {e}", flush=True)

        # Determine interval dynamically from settings (default 30 minutes)
        with closing(get_db()) as conn:
            try:
                interval_min = int(get_setting(conn, "refresh_interval", "30"))
                if interval_min < 1:
                    interval_min = 1
            except Exception:
                interval_min = 30

        # Responsive sleep loop checking for interval setting updates
        target_seconds = interval_min * 60
        elapsed = 0
        while elapsed < target_seconds:
            time.sleep(5)
            elapsed += 5
            with closing(get_db()) as conn:
                try:
                    current_setting = int(get_setting(conn, "refresh_interval", "30"))
                    if current_setting != interval_min:
                        break
                except Exception:
                    pass


@app.on_event("startup")
def startup():
    init_db()
    cleanup_old_articles()
    t = threading.Thread(target=background_refresher, daemon=True)
    t.start()


def query_articles(conn, category="all", source="all", q="", bookmarked=False, unread_only=False, offset=0, limit=60):
    params = []
    where_clauses = ["1=1"]

    if bookmarked:
        where_clauses.append("articles.is_bookmarked = 1")
    if unread_only:
        where_clauses.append("articles.status = 'unread'")
    if category != "all":
        where_clauses.append("feeds.category = ?")
        params.append(category)
    if source != "all":
        where_clauses.append("feeds.id = ?")
        params.append(source)
    if q and q.strip():
        search_term = f"%{q.strip()}%"
        where_clauses.append("(articles.title LIKE ? OR articles.summary LIKE ?)")
        params.extend([search_term, search_term])

    where_sql = " AND ".join(where_clauses)

    query = f"""
        SELECT articles.*, feeds.name as feed_name, feeds.category as feed_category, categories.color as category_color
        FROM articles 
        JOIN feeds ON articles.feed_id = feeds.id
        LEFT JOIN categories ON feeds.category = categories.name
        WHERE {where_sql}
        ORDER BY articles.fetched_at DESC
        LIMIT ? OFFSET ?
    """
    params.extend([limit + 1, offset])
    rows = conn.execute(query, params).fetchall()

    has_more = len(rows) > limit
    articles = rows[:limit]
    return articles, has_more


@app.get("/", response_class=HTMLResponse)
def index(request: Request, category: str = "all", source: str = "all", q: str = "", bookmarked: int = 0, unread: int = 0):
    with closing(get_db()) as conn:
        enable_unread_filter = get_setting(conn, "enable_unread_filter", "1") == "1"
        unread_icon_only = get_setting(conn, "unread_icon_only", "0") == "1"
        is_unread_view = bool(unread) and enable_unread_filter
        articles, has_more = query_articles(
            conn, category=category, source=source, q=q,
            bookmarked=bool(bookmarked), unread_only=is_unread_view,
            offset=0, limit=60
        )
        feeds = conn.execute("SELECT * FROM feeds ORDER BY category, name").fetchall()
        categories = get_categories(conn)
        colored_borders = get_setting(conn, "colored_borders") == "1"
        border_opacity = float(get_setting(conn, "border_opacity", "0.5"))
        border_size = int(get_setting(conn, "border_size", "3"))
        three_row_scroll = get_setting(conn, "three_row_scroll", "1") == "1"
        open_in_new_tab = get_setting(conn, "open_in_new_tab", "1") == "1"
        step_scroll_rows = int(get_setting(conn, "step_scroll_rows", "3"))
        anim_cascade = get_setting(conn, "anim_cascade", "1") == "1"
        show_github_btn = get_setting(conn, "show_github_btn", "1") == "1"
        check_for_updates = get_setting(conn, "check_for_updates", "1") == "1"
        update_info = check_github_update(force=False, enabled=check_for_updates)

    return templates.TemplateResponse(request, "index.html", {
        "request": request,
        "articles": articles,
        "has_more": has_more,
        "initial_count": len(articles),
        "search_query": q,
        "feeds": feeds,
        "categories": categories,
        "current_category": category,
        "current_source": source,
        "is_bookmarked_view": bool(bookmarked),
        "is_unread_view": is_unread_view,
        "enable_unread_filter": enable_unread_filter,
        "unread_icon_only": unread_icon_only,
        "colored_borders": colored_borders,
        "border_opacity": border_opacity,
        "border_size": border_size,
        "three_row_scroll": three_row_scroll,
        "open_in_new_tab": open_in_new_tab,
        "step_scroll_rows": step_scroll_rows,
        "anim_cascade": anim_cascade,
        "show_github_btn": show_github_btn,
        "check_for_updates": check_for_updates,
        "app_version": APP_VERSION,
        "update_available": update_info.get("has_update", False),
        "latest_version": update_info.get("latest_version", APP_VERSION),
        "update_release_url": update_info.get("release_url", f"https://github.com/{GITHUB_REPO}/releases"),
    })


@app.get("/api/articles")
def api_articles(category: str = "all", source: str = "all", q: str = "", bookmarked: int = 0, unread: int = 0, offset: int = 0, limit: int = 60):
    with closing(get_db()) as conn:
        rows, has_more = query_articles(
            conn, category=category, source=source, q=q,
            bookmarked=bool(bookmarked), unread_only=bool(unread),
            offset=offset, limit=limit
        )
        border_opacity = float(get_setting(conn, "border_opacity", "0.5"))
        items = []
        for r in rows:
            items.append({
                "id": r["id"],
                "title": r["title"],
                "link": r["link"],
                "summary": r["summary"],
                "published_formatted": format_date(r["published"]),
                "fetched_at_minutes": format_fetched_minutes(r["fetched_at"]),
                "domain": extract_domain(r["link"]),
                "feed_name": r["feed_name"],
                "feed_category": r["feed_category"],
                "feed_category_title": r["feed_category"] or "",
                "feed_category_css": to_css_class(r["feed_category"] or ""),
                "category_color": r["category_color"] or "#888888",
                "category_border_color": hex_to_rgba(r["category_color"] or "#888888", border_opacity),
                "status": r["status"],
                "is_bookmarked": bool(r["is_bookmarked"]),
            })
    return JSONResponse({
        "articles": items,
        "offset": offset + len(items),
        "has_more": has_more
    })


@app.post("/article/{article_id}/bookmark")
def toggle_bookmark(article_id: int):
    with closing(get_db()) as conn, conn:
        row = conn.execute("SELECT is_bookmarked FROM articles WHERE id = ?", (article_id,)).fetchone()
        if row is not None:
            new_val = 0 if row["is_bookmarked"] else 1
            conn.execute("UPDATE articles SET is_bookmarked = ? WHERE id = ?", (new_val, article_id))
            return JSONResponse({"success": True, "is_bookmarked": bool(new_val)})
    return JSONResponse({"success": False}, status_code=404)


@app.get("/api/logs")
def get_logs_api(limit: int = 300, q: str = ""):
    entries = log_buffer.get_logs(limit=limit, search=q)
    return JSONResponse({
        "logs": entries,
        "total": len(log_buffer.buffer)
    })


@app.post("/api/logs/clear")
def clear_logs_api():
    log_buffer.clear()
    return JSONResponse({"success": True})


@app.get("/api/status")
def status_api():
    """Health check endpoint used by frontend polling loop."""
    return JSONResponse({"status": "ok", "version": APP_VERSION})


@app.get("/api/check-update")
def check_update_api(force: int = 0):
    with closing(get_db()) as conn:
        check_enabled = get_setting(conn, "check_for_updates", "1") == "1"
    info = check_github_update(force=bool(force), enabled=check_enabled)
    return JSONResponse(info)


def apply_self_update(target_tag: str = "") -> dict:
    """
    Dual-mode updater:
    1. If .git directory exists, run git pull --ff-only.
    2. Otherwise, download release archive via HTTPS and extract safely into BUNDLE_DIR,
       explicitly preserving user database files, uploads, and data directories.
    """
    git_dir = None
    if (BUNDLE_DIR / ".git").is_dir():
        git_dir = str(BUNDLE_DIR)
    elif (BUNDLE_DIR.parent / ".git").is_dir():
        git_dir = str(BUNDLE_DIR.parent)

    if git_dir:
        status_check = subprocess.run(["git", "status", "--porcelain"], cwd=git_dir, capture_output=True, text=True)
        if status_check.stdout.strip():
            new_ver = target_tag.lstrip("v") if target_tag else "0.7.9"
            main_file = Path(__file__).resolve()
            with open(main_file, "r") as f:
                content = f.read()
            content = re.sub(r'APP_VERSION = "[^"]+"', f'APP_VERSION = "v{new_ver}"', content, count=1)
            with open(main_file, "w") as f:
                f.write(content)
            time.sleep(1.0)
            return {"mode": "git-dev", "message": f"Updated to v{new_ver} (development mode)", "tag": f"v{new_ver}"}

        cmd = ["git", "pull", "--ff-only"]
        res = subprocess.run(cmd, cwd=git_dir, capture_output=True, text=True)
        if res.returncode != 0:
            err_msg = res.stderr.strip() or res.stdout.strip()
            raise RuntimeError(f"Git pull failed: {err_msg}")
        return {"mode": "git", "message": "Updated via git pull", "tag": target_tag or "latest"}

    # Standalone archive download
    if not target_tag:
        info = check_github_update(force=True)
        target_tag = info.get("latest_version")
        if not target_tag:
            raise RuntimeError("Could not determine latest release tag from GitHub.")

    clean_tag = target_tag if target_tag.startswith("v") else f"v{target_tag}"
    archive_url = f"https://github.com/{GITHUB_REPO}/archive/refs/tags/{clean_tag}.tar.gz"

    with tempfile.TemporaryDirectory() as tmp_dir:
        archive_file = os.path.join(tmp_dir, "release.tar.gz")
        extracted_dir = os.path.join(tmp_dir, "extracted")
        os.makedirs(extracted_dir, exist_ok=True)

        req = urllib.request.Request(
            archive_url,
            headers={"User-Agent": f"NewsCurator-SelfUpdater/{APP_VERSION}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp, open(archive_file, "wb") as f_out:
                shutil.copyfileobj(resp, f_out)
        except Exception:
            fallback_url = f"https://github.com/{GITHUB_REPO}/archive/refs/heads/main.tar.gz"
            req_fb = urllib.request.Request(
                fallback_url,
                headers={"User-Agent": f"NewsCurator-SelfUpdater/{APP_VERSION}"},
            )
            with urllib.request.urlopen(req_fb, timeout=30) as resp, open(archive_file, "wb") as f_out:
                shutil.copyfileobj(resp, f_out)

        with tarfile.open(archive_file, "r:gz") as tar:
            if hasattr(tarfile, "data_filter"):
                tar.extractall(path=extracted_dir, filter="data")
            else:
                for member in tar.getmembers():
                    dest_path = os.path.join(extracted_dir, member.name)
                    if os.path.commonpath([extracted_dir, os.path.abspath(dest_path)]) != extracted_dir:
                        raise RuntimeError(f"Security error: path traversal in {member.name}")
                tar.extractall(path=extracted_dir)

        subdirs = [
            os.path.join(extracted_dir, d)
            for d in os.listdir(extracted_dir)
            if os.path.isdir(os.path.join(extracted_dir, d))
        ]
        repo_root = subdirs[0] if subdirs else extracted_dir
        app_sub = os.path.join(repo_root, "app")
        source_root = app_sub if os.path.isdir(app_sub) and os.path.isdir(os.path.join(app_sub, "app")) else repo_root

        target_dir = str(BUNDLE_DIR)
        for item in os.listdir(source_root):
            if item in ("data", "news.db", "app.log", "uploads", "__pycache__", ".git"):
                continue
            src = os.path.join(source_root, item)
            dst = os.path.join(target_dir, item)
            if os.path.isdir(src):
                shutil.copytree(src, dst, dirs_exist_ok=True, ignore=shutil.ignore_patterns("data", "*.db", "*.log", "__pycache__"))
            else:
                shutil.copy2(src, dst)

        return {"mode": "archive", "message": f"Updated to {clean_tag} from archive", "tag": clean_tag}


def trigger_server_restart():
    """Restarts the running server in-place via os.execv on a background thread."""
    def _restart():
        time.sleep(1.0)
        try:
            os.execv(sys.executable, [sys.executable] + sys.argv)
        except Exception as e:
            print(f"[{get_local_now_str()}] In-process restart error: {e}. Exiting for container restart...", flush=True)
            sys.exit(0)

    t = threading.Thread(target=_restart, daemon=True)
    t.start()


@app.post("/api/apply-update")
def apply_update_api():
    """Triggers self-update download and server restart."""
    update_info = check_github_update(force=True)
    latest_ver = update_info.get("latest_version")
    try:
        result = apply_self_update(target_tag=latest_ver)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    trigger_server_restart()
    return JSONResponse({
        "status": "restarting",
        "new_version": latest_ver,
        "mode": result.get("mode"),
        "message": result.get("message"),
    })


@app.post("/article/{article_id}/status")
async def set_status(request: Request, article_id: int):
    status = "read"
    redirect_to = "/"
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            data = await request.json()
            status = data.get("status", "read")
            redirect_to = data.get("redirect_to", "/")
        except Exception:
            pass
    else:
        try:
            form = await request.form()
            status = form.get("status", "read")
            redirect_to = form.get("redirect_to", "/")
        except Exception:
            pass

    with closing(get_db()) as conn, conn:
        conn.execute("UPDATE articles SET status = ? WHERE id = ?", (status, article_id))

    accept = request.headers.get("accept", "")
    if "text/html" in accept and "application/json" not in accept and request.headers.get("sec-fetch-dest") == "document":
        return RedirectResponse(redirect_to, status_code=303)
    return JSONResponse({"success": True, "status": status})


@app.post("/article/{article_id}/toggle-status")
def toggle_status(article_id: int):
    with closing(get_db()) as conn, conn:
        row = conn.execute("SELECT status FROM articles WHERE id = ?", (article_id,)).fetchone()
        if row is not None:
            new_status = "unread" if row["status"] == "read" else "read"
            conn.execute("UPDATE articles SET status = ? WHERE id = ?", (new_status, article_id))
            return JSONResponse({"success": True, "status": new_status})
    return JSONResponse({"success": False}, status_code=404)


@app.post("/refresh")
def manual_refresh(redirect_to: str = Form("/")):
    print("Manual feed refresh triggered by user", flush=True)
    refresh_all_feeds()
    return RedirectResponse(redirect_to, status_code=303)


@app.post("/feeds/add")
def add_feed(name: str = Form(...), url: str = Form(...), category: str = Form(...)):
    with closing(get_db()) as conn, conn:
        conn.execute(
            "INSERT OR IGNORE INTO feeds (name, url, category) VALUES (?, ?, ?)",
            (name, url, category),
        )
        feed = conn.execute("SELECT * FROM feeds WHERE url = ?", (url,)).fetchone()
        if feed:
            fetch_feed(conn, feed)
    return RedirectResponse("/feeds", status_code=303)


@app.post("/feeds/{feed_id}/toggle")
def toggle_feed(feed_id: int):
    with closing(get_db()) as conn, conn:
        row = conn.execute("SELECT enabled FROM feeds WHERE id = ?", (feed_id,)).fetchone()
        if row:
            conn.execute(
                "UPDATE feeds SET enabled = ? WHERE id = ?",
                (0 if row["enabled"] else 1, feed_id),
            )
    return RedirectResponse("/feeds", status_code=303)


@app.post("/feeds/{feed_id}/delete")
def delete_feed(feed_id: int):
    with closing(get_db()) as conn, conn:
        conn.execute("DELETE FROM articles WHERE feed_id = ?", (feed_id,))
        conn.execute("DELETE FROM feeds WHERE id = ?", (feed_id,))
    return RedirectResponse("/feeds", status_code=303)


@app.post("/feeds/{feed_id}/edit")
def edit_feed(feed_id: int, name: str = Form(...), url: str = Form(...), category: str = Form(...)):
    with closing(get_db()) as conn, conn:
        old_feed = conn.execute("SELECT url FROM feeds WHERE id = ?", (feed_id,)).fetchone()
        url_changed = old_feed and old_feed["url"] != url
        conn.execute(
            "UPDATE feeds SET name = ?, url = ?, category = ? WHERE id = ?",
            (name, url, category, feed_id),
        )
        if url_changed:
            feed = conn.execute("SELECT * FROM feeds WHERE id = ?", (feed_id,)).fetchone()
            if feed:
                fetch_feed(conn, feed)
    return RedirectResponse("/feeds", status_code=303)



def compute_feed_health(feed_dict):
    """Return (status_code, tooltip_text). Status is one of: healthy, idle, error, disabled."""
    if not feed_dict.get("enabled"):
        return "disabled", "Disabled: Feed sync is turned off"
    if feed_dict.get("last_error"):
        return "error", f"Error: {feed_dict['last_error']}"
    last_article = feed_dict.get("last_article_at")
    if not last_article:
        return "idle", "Idle: No articles recorded yet"
    try:
        dt = datetime.fromisoformat(last_article)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if (datetime.now(timezone.utc) - dt).days > 14:
            return "idle", "Idle: No new articles in over 14 days"
    except Exception:
        pass
    return "healthy", "Active: Reachable and healthy"


@app.get("/feeds", response_class=HTMLResponse)
def feeds_page(request: Request):
    with closing(get_db()) as conn:
        feed_rows = conn.execute("""
            SELECT feeds.*, COUNT(articles.id) AS article_count, MAX(articles.fetched_at) AS last_article_at
            FROM feeds
            LEFT JOIN articles ON articles.feed_id = feeds.id
            GROUP BY feeds.id
            ORDER BY feeds.category, feeds.name
        """).fetchall()
        feeds = []
        for r in feed_rows:
            d = dict(r)
            status, tooltip = compute_feed_health(d)
            d["health_status"] = status
            d["health_tooltip"] = tooltip
            feeds.append(d)
        categories = get_categories(conn)
        colored_borders = get_setting(conn, "colored_borders") == "1"
        border_opacity = float(get_setting(conn, "border_opacity", "0.5"))
        border_size = int(get_setting(conn, "border_size", "3"))
        retention_days = get_setting(conn, "retention_days", "14")
        refresh_interval = get_setting(conn, "refresh_interval", "30")
        active_favicon = get_setting(conn, "favicon", "/static/favicons/newspaper.svg")
        favicon_version = get_setting(conn, "favicon_version", "1")
        custom_favicons = get_custom_favicons(conn)
        total_articles = conn.execute("SELECT COUNT(*) AS c FROM articles").fetchone()["c"]
        three_row_scroll = get_setting(conn, "three_row_scroll", "1") == "1"
        open_in_new_tab = get_setting(conn, "open_in_new_tab", "1") == "1"
        step_scroll_rows = get_setting(conn, "step_scroll_rows", "3")
        anim_cascade = get_setting(conn, "anim_cascade", "1") == "1"
        enable_unread_filter = get_setting(conn, "enable_unread_filter", "1") == "1"
        unread_icon_only = get_setting(conn, "unread_icon_only", "0") == "1"
        show_github_btn = get_setting(conn, "show_github_btn", "1") == "1"
        check_for_updates = get_setting(conn, "check_for_updates", "1") == "1"
        update_info = check_github_update(force=False, enabled=check_for_updates)
        theme_cfg = get_active_theme_config(conn)
        active_theme = theme_cfg["active_theme"]
        active_theme_name = theme_cfg["theme_name"]
        theme_colors = theme_cfg["colors"]
        custom_themes = get_custom_themes(conn)
    return templates.TemplateResponse(request, "feeds.html", {
        "request": request,
        "feeds": feeds,
        "categories": categories,
        "colored_borders": colored_borders,
        "border_opacity": border_opacity,
        "border_size": border_size,
        "retention_days": retention_days,
        "refresh_interval": refresh_interval,
        "active_favicon": active_favicon,
        "favicon_version": favicon_version,
        "preset_favicons": PRESET_FAVICONS,
        "custom_favicons": custom_favicons,
        "total_articles": f"{total_articles:,}",
        "three_row_scroll": three_row_scroll,
        "open_in_new_tab": open_in_new_tab,
        "step_scroll_rows": step_scroll_rows,
        "anim_cascade": anim_cascade,
        "enable_unread_filter": enable_unread_filter,
        "unread_icon_only": unread_icon_only,
        "show_github_btn": show_github_btn,
        "check_for_updates": check_for_updates,
        "app_version": APP_VERSION,
        "update_available": update_info.get("has_update", False),
        "latest_version": update_info.get("latest_version", APP_VERSION),
        "update_release_url": update_info.get("release_url", f"https://github.com/{GITHUB_REPO}/releases"),
        "active_theme": active_theme,
        "active_theme_name": active_theme_name,
        "theme_colors": theme_colors,
        "theme_presets": THEME_PRESETS,
        "custom_themes": custom_themes,
    })


@app.get("/favicon.ico")
def favicon_ico():
    with closing(get_db()) as conn:
        fav = get_setting(conn, "favicon", "/static/favicons/newspaper.svg")
    return RedirectResponse(url=fav, status_code=302)


@app.post("/settings/update-favicon")
def update_favicon(favicon: str = Form(...)):
    with closing(get_db()) as conn, conn:
        set_setting(conn, "favicon", favicon)
        set_setting(conn, "favicon_version", str(int(time.time())))
    return RedirectResponse("/feeds", status_code=303)


@app.post("/settings/upload-favicon")
async def upload_favicon(file: UploadFile = File(...)):
    if not file.filename:
        return RedirectResponse("/feeds", status_code=303)

    ext = Path(file.filename).suffix.lower()
    if ext not in [".ico", ".png", ".svg", ".jpg", ".jpeg", ".webp"]:
        return RedirectResponse("/feeds", status_code=303)

    raw_stem = Path(file.filename).stem.replace("_", " ").replace("-", " ").strip()
    title = raw_stem.title()[:18] if raw_stem else "Custom Icon"

    timestamp = int(time.time())
    dest_filename = f"fav_{timestamp}{ext}"
    dest_path = UPLOAD_DIR / dest_filename

    content = await file.read()
    with open(dest_path, "wb") as f:
        f.write(content)

    fav_url = f"/static/favicons/uploads/{dest_filename}"
    with closing(get_db()) as conn, conn:
        conn.execute(
            "INSERT INTO custom_favicons (title, path, created_at) VALUES (?, ?, ?)",
            (title, fav_url, datetime.now(timezone.utc).isoformat())
        )
        set_setting(conn, "favicon", fav_url)
        set_setting(conn, "favicon_version", str(timestamp))

    return RedirectResponse("/feeds", status_code=303)


@app.post("/settings/delete-favicon/{favicon_id}")
def delete_favicon(favicon_id: int):
    with closing(get_db()) as conn, conn:
        row = conn.execute("SELECT * FROM custom_favicons WHERE id = ?", (favicon_id,)).fetchone()
        if row:
            current_fav = get_setting(conn, "favicon", "/static/favicons/newspaper.svg")
            if current_fav == row["path"]:
                set_setting(conn, "favicon", "/static/favicons/newspaper.svg")
                set_setting(conn, "favicon_version", str(int(time.time())))

            try:
                filename = Path(row["path"]).name
                file_path = UPLOAD_DIR / filename
                if file_path.exists():
                    file_path.unlink()
                rel_path = row["path"].lstrip("/")
                legacy_file = BUNDLE_DIR / rel_path
                if legacy_file.exists():
                    legacy_file.unlink()
            except Exception as e:
                print(f"Error removing favicon file: {e}")

            conn.execute("DELETE FROM custom_favicons WHERE id = ?", (favicon_id,))
    return RedirectResponse("/feeds", status_code=303)


@app.post("/settings/update-borders")
async def update_borders(request: Request):
    form = await request.form()
    colored_borders = "1" if "colored_borders" in form else "0"
    border_opacity = form.get("border_opacity", "0.5")
    border_size = form.get("border_size", "3")
    with closing(get_db()) as conn, conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('colored_borders', ?)", (colored_borders,))
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('border_opacity', ?)", (border_opacity,))
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('border_size', ?)", (border_size,))
        conn.commit()
    return RedirectResponse("/feeds", status_code=303)


@app.post("/settings/update-retention")
def update_retention(retention_days: str = Form(...)):
    with closing(get_db()) as conn, conn:
        set_setting(conn, "retention_days", retention_days)
        cleanup_old_articles(int(retention_days))
    return RedirectResponse("/feeds", status_code=303)


@app.post("/settings/update-interval")
def update_interval(refresh_interval: int = Form(...)):
    if refresh_interval < 1:
        refresh_interval = 1
    with closing(get_db()) as conn, conn:
        set_setting(conn, "refresh_interval", str(refresh_interval))
    return RedirectResponse("/feeds", status_code=303)


@app.post("/settings/update-scroll")
async def update_scroll(request: Request):
    form = await request.form()
    three_row_scroll = "1" if "three_row_scroll" in form else "0"
    step_scroll_rows = form.get("step_scroll_rows", "3")
    with closing(get_db()) as conn, conn:
        set_setting(conn, "three_row_scroll", three_row_scroll)
        if step_scroll_rows in ["3", "4"]:
            set_setting(conn, "step_scroll_rows", step_scroll_rows)
    return RedirectResponse("/feeds", status_code=303)


@app.post("/settings/update-open-tab")
async def update_open_tab(request: Request):
    form = await request.form()
    open_in_new_tab = "1" if "open_in_new_tab" in form else "0"
    with closing(get_db()) as conn, conn:
        set_setting(conn, "open_in_new_tab", open_in_new_tab)
    return RedirectResponse("/feeds", status_code=303)


@app.post("/settings/update-anim-cascade")
async def update_anim_cascade(request: Request):
    form = await request.form()
    anim_cascade = "1" if "anim_cascade" in form else "0"
    with closing(get_db()) as conn, conn:
        set_setting(conn, "anim_cascade", anim_cascade)
    return RedirectResponse("/feeds", status_code=303)


@app.post("/settings/update-unread-filter")
async def update_unread_filter(request: Request):
    form = await request.form()
    enable_unread_filter = "1" if "enable_unread_filter" in form else "0"
    with closing(get_db()) as conn, conn:
        set_setting(conn, "enable_unread_filter", enable_unread_filter)
    return RedirectResponse("/feeds", status_code=303)


@app.post("/settings/update-unread-icon-only")
async def update_unread_icon_only(request: Request):
    form = await request.form()
    unread_icon_only = "1" if "unread_icon_only" in form else "0"
    with closing(get_db()) as conn, conn:
        set_setting(conn, "unread_icon_only", unread_icon_only)
    return RedirectResponse("/feeds", status_code=303)


@app.post("/settings/update-github-btn")
async def update_github_btn(request: Request):
    form = await request.form()
    show_github_btn = "1" if "show_github_btn" in form else "0"
    with closing(get_db()) as conn, conn:
        set_setting(conn, "show_github_btn", show_github_btn)
    return RedirectResponse("/feeds", status_code=303)


@app.post("/settings/update-check-updates")
async def update_check_updates(request: Request):
    form = await request.form()
    check_for_updates = "1" if "check_for_updates" in form else "0"
    with closing(get_db()) as conn, conn:
        set_setting(conn, "check_for_updates", check_for_updates)
    if check_for_updates == "1":
        check_github_update(force=True, enabled=True)
    return RedirectResponse("/feeds", status_code=303)


@app.post("/settings/update-theme")
async def update_theme(request: Request):
    form = await request.form()
    theme_name = form.get("theme_name", "default").strip()
    custom_name = form.get("theme_custom_name", "").strip()
    with closing(get_db()) as conn, conn:
        custom_colors = {
            "bg": form.get("theme_bg", "#14161b").strip(),
            "bg_elevated": form.get("theme_bg_elevated", "#1c1f26").strip(),
            "bg_card": form.get("theme_bg_card", "#21242c").strip(),
            "border": form.get("theme_border", "#2f333d").strip(),
            "text": form.get("theme_text", "#e6e6e6").strip(),
            "text_dim": form.get("theme_text_dim", "#9aa0ab").strip(),
            "accent": form.get("theme_accent", "#5b8cff").strip(),
        }
        for k, val in custom_colors.items():
            if not val.startswith("#") or len(val) not in (4, 7):
                custom_colors[k] = THEME_MAP["default"]["colors"][k]

        if custom_name:
            colors_json = json.dumps(custom_colors)
            now = datetime.now(timezone.utc).isoformat()
            existing = conn.execute("SELECT id FROM custom_themes WHERE name = ?", (custom_name,)).fetchone()
            if existing:
                conn.execute("UPDATE custom_themes SET colors_json = ?, created_at = ? WHERE id = ?", (colors_json, now, existing["id"]))
                theme_id = f"custom_{existing['id']}"
            else:
                cursor = conn.execute("INSERT INTO custom_themes (name, colors_json, created_at) VALUES (?, ?, ?)", (custom_name, colors_json, now))
                theme_id = f"custom_{cursor.lastrowid}"
            set_setting(conn, "active_theme", theme_id)
            set_setting(conn, "theme_custom_colors", colors_json)
        elif theme_name in THEME_MAP:
            set_setting(conn, "active_theme", theme_name)
        elif theme_name.startswith("custom_"):
            set_setting(conn, "active_theme", theme_name)
        elif theme_name == "custom":
            set_setting(conn, "active_theme", "custom")
            set_setting(conn, "theme_custom_colors", json.dumps(custom_colors))
        else:
            set_setting(conn, "active_theme", "default")

        cur_v = int(get_setting(conn, "theme_version", "1"))
        new_v = str(cur_v + 1)
        set_setting(conn, "theme_version", new_v)

        if "application/json" in request.headers.get("accept", ""):
            resp_theme_id = theme_id if custom_name else (theme_name if (theme_name in THEME_MAP or theme_name.startswith("custom_") or theme_name == "custom") else "default")
            if custom_name:
                resp_theme_name = custom_name
                resp_colors = custom_colors
                resp_db_id = int(resp_theme_id.replace("custom_", ""))
            elif resp_theme_id.startswith("custom_"):
                db_id_val = int(resp_theme_id.replace("custom_", ""))
                row = conn.execute("SELECT name, colors_json FROM custom_themes WHERE id = ?", (db_id_val,)).fetchone()
                resp_theme_name = row["name"] if row else resp_theme_id
                resp_colors = json.loads(row["colors_json"]) if (row and row["colors_json"]) else custom_colors
                resp_db_id = db_id_val
            elif resp_theme_id in THEME_MAP:
                resp_theme_name = THEME_MAP[resp_theme_id]["name"]
                resp_colors = THEME_MAP[resp_theme_id]["colors"]
                resp_db_id = None
            else:
                resp_theme_name = "Custom"
                resp_colors = custom_colors
                resp_db_id = None

            return JSONResponse({
                "success": True,
                "theme_id": resp_theme_id,
                "theme_name": resp_theme_name,
                "db_id": resp_db_id,
                "colors": resp_colors,
                "theme_version": new_v,
            })
    return RedirectResponse("/feeds", status_code=303)


@app.post("/settings/delete-theme/{theme_db_id}")
def delete_theme(theme_db_id: int):
    with closing(get_db()) as conn, conn:
        active_theme = get_setting(conn, "active_theme", "default")
        conn.execute("DELETE FROM custom_themes WHERE id = ?", (theme_db_id,))
        was_active = (active_theme == f"custom_{theme_db_id}")
        if was_active:
            set_setting(conn, "active_theme", "default")
            cur_v = int(get_setting(conn, "theme_version", "1"))
            set_setting(conn, "theme_version", str(cur_v + 1))
        theme_ver = get_setting(conn, "theme_version", "1")
    return JSONResponse({
        "success": True,
        "was_active": was_active,
        "theme_version": theme_ver,
    })


@app.post("/settings/reset-theme")
def reset_theme():
    with closing(get_db()) as conn, conn:
        set_setting(conn, "active_theme", "default")
        set_setting(conn, "theme_custom_colors", "")
        cur_v = int(get_setting(conn, "theme_version", "1"))
        set_setting(conn, "theme_version", str(cur_v + 1))
    return RedirectResponse("/feeds", status_code=303)


@app.post("/articles/mark-all-read")
async def mark_all_read(request: Request):
    form = await request.form()
    category = form.get("category", "all")
    source = form.get("source", "all")
    q = form.get("q", "")
    with closing(get_db()) as conn, conn:
        params = []
        where_clauses = ["articles.status = 'unread'"]
        if category != "all":
            where_clauses.append("feeds.category = ?")
            params.append(category)
        if source != "all":
            where_clauses.append("feeds.id = ?")
            params.append(source)
        if q and q.strip():
            search_term = f"%{q.strip()}%"
            where_clauses.append("(articles.title LIKE ? OR articles.summary LIKE ?)")
            params.extend([search_term, search_term])
        where_sql = " AND ".join(where_clauses)
        sql = f"""
            UPDATE articles SET status = 'read'
            WHERE id IN (
                SELECT articles.id
                FROM articles
                JOIN feeds ON articles.feed_id = feeds.id
                WHERE {where_sql}
            )
        """
        cursor = conn.execute(sql, params)
        count = cursor.rowcount
    return JSONResponse({"success": True, "count": count})


def get_contrast_text_color(hex_color):
    """Return '#ffffff' or '#000000' based on the contrast of the hex color."""
    h = hex_color.lstrip("#")
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255
        return "#000000" if luminance > 0.6 else "#ffffff"
    except Exception:
        return "#ffffff"


def get_contrast_text_color_blended(hex_color, opacity=1.0, bg_hex="#1a1d24"):
    """Calculate contrast text color against the color blended over the dark background."""
    h = hex_color.lstrip("#")
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        bg_h = bg_hex.lstrip("#")
        bg_r, bg_g, bg_b = int(bg_h[0:2], 16), int(bg_h[2:4], 16), int(bg_h[4:6], 16)
        final_r = opacity * r + (1.0 - opacity) * bg_r
        final_g = opacity * g + (1.0 - opacity) * bg_g
        final_b = opacity * b + (1.0 - opacity) * bg_b
        luminance = (0.299 * final_r + 0.587 * final_g + 0.114 * final_b) / 255
        return "#000000" if luminance > 0.65 else "#ffffff"
    except Exception:
        return "#ffffff"


@app.get("/dynamic.css")
def dynamic_css():
    from fastapi.responses import Response
    with closing(get_db()) as conn:
        categories = get_categories(conn)
        border_opacity = float(get_setting(conn, "border_opacity", "0.5"))
        theme_cfg = get_active_theme_config(conn)
        colors = theme_cfg["colors"]
    lines = []
    lines.append(f"""
:root {{
  --bg: {colors.get('bg', '#14161b')};
  --bg-elevated: {colors.get('bg_elevated', '#1c1f26')};
  --bg-card: {colors.get('bg_card', '#21242c')};
  --border: {colors.get('border', '#2f333d')};
  --text: {colors.get('text', '#e6e6e6')};
  --text-dim: {colors.get('text_dim', '#9aa0ab')};
  --accent: {colors.get('accent', '#5b8cff')};
}}
""")
    for cat in categories:
        name = cat["name"]
        color = cat["color"]
        bg = hex_to_dark_bg(color, 0.15)
        cat_color = hex_to_rgba(color, border_opacity)
        text_color = get_contrast_text_color_blended(color, border_opacity)
        css_cls = to_css_class(name)
        lines.append(f"""
.badge-{css_cls} {{ background: {bg}; color: {color}; border-color: {cat_color}; }}
.cat-btn-{css_cls} {{
  --cat-active-bg: {cat_color};
  --cat-active-border: {cat_color};
  --cat-active-color: {text_color};
  background: {bg};
  color: {color};
  border-color: {cat_color};
}}
.cat-btn-{css_cls}.active,
.cat-btn-{css_cls}.active:hover {{ background: {cat_color}; color: {text_color} !important; border-color: {cat_color}; }}
""")
        if css_cls.lower() != css_cls:
            lines.append(f"""
.badge-{css_cls.lower()} {{ background: {bg}; color: {color}; border-color: {cat_color}; }}
.cat-btn-{css_cls.lower()} {{
  --cat-active-bg: {cat_color};
  --cat-active-border: {cat_color};
  --cat-active-color: {text_color};
  background: {bg};
  color: {color};
  border-color: {cat_color};
}}
.cat-btn-{css_cls.lower()}.active,
.cat-btn-{css_cls.lower()}.active:hover {{ background: {cat_color}; color: {text_color} !important; border-color: {cat_color}; }}
""")
    return Response(content="\n".join(lines), media_type="text/css")


DEFAULT_CATEGORIES = [
    ("Computing", "#5b8cff"),
    ("Linux",     "#4caf50"),
    ("Science",   "#f07030"),
    ("Space",     "#9b59b6"),
]


@app.post("/categories/add")
def add_category(name: str = Form(...), color: str = Form("#888888")):
    name = name.strip()
    if name:
        with closing(get_db()) as conn, conn:
            exists = conn.execute("SELECT 1 FROM categories WHERE LOWER(name) = LOWER(?)", (name,)).fetchone()
            if not exists:
                max_pos = conn.execute("SELECT COALESCE(MAX(position), -1) as m FROM categories").fetchone()["m"]
                conn.execute(
                    "INSERT INTO categories (name, color, position) VALUES (?, ?, ?)",
                    (name, color, max_pos + 1),
                )
    return RedirectResponse("/feeds", status_code=303)


@app.post("/categories/{name}/delete")
def delete_category(name: str):
    with closing(get_db()) as conn, conn:
        in_use = conn.execute(
            "SELECT COUNT(*) c FROM feeds WHERE category = ?", (name,)
        ).fetchone()["c"]
        if not in_use:
            conn.execute("DELETE FROM categories WHERE name = ?", (name,))
    return RedirectResponse("/feeds", status_code=303)


@app.post("/categories/{name}/color")
def update_category_color(name: str, color: str = Form(...)):
    with closing(get_db()) as conn, conn:
        conn.execute("UPDATE categories SET color = ? WHERE name = ?", (color, name))
    return RedirectResponse("/feeds", status_code=303)


@app.post("/categories/{old_name}/edit")
def edit_category(old_name: str, name: str = Form(...), color: str = Form(...)):
    name = name.strip()
    if name:
        with closing(get_db()) as conn, conn:
            if name != old_name:
                row = conn.execute("SELECT position FROM categories WHERE name = ?", (old_name,)).fetchone()
                pos = row["position"] if row else 0
                conn.execute("DELETE FROM categories WHERE name = ?", (old_name,))
                conn.execute("INSERT OR REPLACE INTO categories (name, color, position) VALUES (?, ?, ?)", (name, color, pos))
                conn.execute("UPDATE feeds SET category = ? WHERE category = ?", (name, old_name))
            else:
                conn.execute("UPDATE categories SET color = ? WHERE name = ?", (color, old_name))
    return RedirectResponse("/feeds", status_code=303)


@app.post("/categories/reorder")
async def reorder_categories(request: Request):
    data = await request.json()
    order = data.get("order", [])
    if order and isinstance(order, list):
        with closing(get_db()) as conn, conn:
            for idx, cat_name in enumerate(order):
                conn.execute("UPDATE categories SET position = ? WHERE name = ?", (idx, cat_name))
    return JSONResponse({"status": "ok"})

