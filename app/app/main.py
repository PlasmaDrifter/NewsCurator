import sqlite3
import time
import threading
import hashlib
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import feedparser
from fastapi import FastAPI, Form, Request
from fastapi.responses import RedirectResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "news.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="News Curator")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

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
            return f"{dt.strftime('%H:%M')} - {month_str} {dt.day}"
        try:
            val_clean = value.replace("Z", "+00:00")
            dt = datetime.fromisoformat(val_clean)
            dt = dt.astimezone(ZoneInfo("America/Los_Angeles"))
            month_str = MONTH_ABBR.get(dt.month, dt.strftime("%b"))
            return f"{dt.strftime('%H:%M')} - {month_str} {dt.day}"
        except Exception:
            pass
    except Exception:
        pass
    return str(value)[:25]

templates.env.filters["format_date"] = format_date

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
        minutes = diff.total_seconds() / 60.0
        if minutes < 0:
            minutes = 0.0
        return f"{int(round(minutes))} m"
    except Exception:
        return ""

templates.env.filters["format_fetched_minutes"] = format_fetched_minutes


DEFAULT_FEEDS = [
    # General computing / tech
    ("Ars Technica", "https://feeds.arstechnica.com/arstechnica/index", "computing"),
    ("The Verge", "https://www.theverge.com/rss/index.xml", "computing"),
    ("Hacker News (front page)", "https://hnrss.org/frontpage", "computing"),
    ("TechCrunch", "https://techcrunch.com/feed/", "computing"),
    # Linux
    ("Phoronix", "https://www.phoronix.com/rss.php", "linux"),
    ("It's FOSS", "https://itsfoss.com/feed/", "linux"),
    ("LWN.net Headlines", "https://lwn.net/headlines/rss", "linux"),
    ("OMG! Ubuntu", "https://www.omgubuntu.co.uk/feed", "linux"),
    # Science
    ("Science Daily", "https://www.sciencedaily.com/rss/top/science.xml", "science"),
    ("Phys.org", "https://phys.org/rss-feed/", "science"),
    ("Nature News", "https://www.nature.com/nature.rss", "science"),
    # Space
    ("NASA Breaking News", "https://www.nasa.gov/news-release/feed/", "space"),
    ("Space.com", "https://www.space.com/feeds/all", "space"),
    ("SpaceNews", "https://spacenews.com/feed/", "space"),
    # Defense
    ("Covert Shores", "http://www.hisutton.com/feed.xml", "defense"),
    ("Defense One", "https://www.defenseone.com/rss/all/", "defense"),
    ("ISW", "https://news.google.com/rss/search?q=site%3Aunderstandingwar.org&hl=en-US&gl=US&ceid=US%3Aen", "defense"),
]




def get_db():
    conn = sqlite3.connect(str(DB_PATH), timeout=30.0)
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.row_factory = sqlite3.Row
    return conn


def get_categories(conn):
    rows = conn.execute("SELECT name, color FROM categories ORDER BY name").fetchall()
    return [dict(r) for r in rows]


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
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('border_opacity', '1.0')")
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('border_size', '2')")
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('retention_days', '14')")
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('refresh_interval', '30')")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS categories (
                name TEXT PRIMARY KEY,
                color TEXT NOT NULL DEFAULT '#888888'
            )
        """)
        # Migrate: add color column if it doesn't exist yet
        try:
            conn.execute("ALTER TABLE categories ADD COLUMN color TEXT NOT NULL DEFAULT '#888888'")
        except Exception:
            pass
        conn.execute("""
            CREATE TABLE IF NOT EXISTS feeds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                url TEXT NOT NULL UNIQUE,
                category TEXT NOT NULL DEFAULT 'computing',
                enabled INTEGER NOT NULL DEFAULT 1,
                last_fetched TEXT
            )
        """)
        try:
            conn.execute("ALTER TABLE feeds ADD COLUMN last_fetched TEXT")
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
        conn.execute("CREATE INDEX IF NOT EXISTS idx_articles_status ON articles(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_articles_published ON articles(published)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_articles_bookmarked ON articles(is_bookmarked)")

        # Seed default categories with colors
        for cat_name, cat_color in DEFAULT_CATEGORIES:
            conn.execute(
                "INSERT OR IGNORE INTO categories (name, color) VALUES (?, ?)",
                (cat_name, cat_color),
            )

        # Migration: seed any categories already in feeds table
        for row in conn.execute("SELECT DISTINCT category FROM feeds"):
            conn.execute(
                "INSERT OR IGNORE INTO categories (name, color) VALUES (?, ?)",
                (row["category"], "#888888"),
            )

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
    try:
        parsed = feedparser.parse(feed_row["url"])
    except Exception as e:
        print(f"Error fetching {feed_row['name']}: {e}")
        return 0

    conn.execute(
        "UPDATE feeds SET last_fetched = ? WHERE id = ?",
        (datetime.now(timezone.utc).isoformat(), feed_row["id"])
    )

    new_count = 0
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
        image_url = extract_image(entry)

        conn.execute(
            """INSERT OR IGNORE INTO articles
               (feed_id, guid, title, link, summary, image_url, published, fetched_at, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'unread')""",
            (
                feed_row["id"], guid, title, link, summary, image_url,
                published, datetime.now(timezone.utc).isoformat(),
            ),
        )
        new_count += 1
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
            print(f"Cleaned up {deleted} articles older than {days} days")
    return deleted


def refresh_all_feeds():
    with closing(get_db()) as conn, conn:
        feeds = conn.execute("SELECT * FROM feeds WHERE enabled = 1").fetchall()
        total_new = 0
        for feed in feeds:
            total_new += fetch_feed(conn, feed)
        print(f"Refresh complete: {total_new} new articles")
    return total_new


def background_refresher():
    while True:
        try:
            refresh_all_feeds()
            cleanup_old_articles()
        except Exception as e:
            print(f"Background refresh error: {e}")

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


def query_articles(conn, category="all", source="all", q="", bookmarked=False, offset=0, limit=60):
    params = []
    where_clauses = ["1=1"]

    if bookmarked:
        where_clauses.append("articles.is_bookmarked = 1")
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
def index(request: Request, category: str = "all", source: str = "all", q: str = "", bookmarked: int = 0):
    with closing(get_db()) as conn:
        articles, has_more = query_articles(conn, category=category, source=source, q=q, bookmarked=bool(bookmarked), offset=0, limit=60)
        feeds = conn.execute("SELECT * FROM feeds ORDER BY category, name").fetchall()
        categories = get_categories(conn)
        colored_borders = get_setting(conn, "colored_borders") == "1"
        border_opacity = float(get_setting(conn, "border_opacity", "1.0"))
        border_size = int(get_setting(conn, "border_size", "2"))

    return templates.TemplateResponse("index.html", {
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
        "colored_borders": colored_borders,
        "border_opacity": border_opacity,
        "border_size": border_size,
    })


@app.get("/api/articles")
def api_articles(category: str = "all", source: str = "all", q: str = "", bookmarked: int = 0, offset: int = 0, limit: int = 60):
    with closing(get_db()) as conn:
        border_opacity = float(get_setting(conn, "border_opacity", "1.0"))
        rows, has_more = query_articles(conn, category=category, source=source, q=q, bookmarked=bool(bookmarked), offset=offset, limit=limit)
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
                "feed_category_title": (r["feed_category"] or "").replace("-", " ").title(),
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


@app.post("/article/{article_id}/status")
def set_status(article_id: int, status: str = Form(...), redirect_to: str = Form("/")):
    with closing(get_db()) as conn, conn:
        conn.execute("UPDATE articles SET status = ? WHERE id = ?", (status, article_id))
    return RedirectResponse(redirect_to, status_code=303)


@app.post("/refresh")
def manual_refresh(redirect_to: str = Form("/")):
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



@app.get("/feeds", response_class=HTMLResponse)
def feeds_page(request: Request):
    with closing(get_db()) as conn:
        feeds = conn.execute("""
            SELECT feeds.*, COUNT(articles.id) AS article_count
            FROM feeds
            LEFT JOIN articles ON articles.feed_id = feeds.id
            GROUP BY feeds.id
            ORDER BY feeds.category, feeds.name
        """).fetchall()
        categories = get_categories(conn)
        colored_borders = get_setting(conn, "colored_borders") == "1"
        border_opacity = float(get_setting(conn, "border_opacity", "1.0"))
        border_size = int(get_setting(conn, "border_size", "2"))
        retention_days = get_setting(conn, "retention_days", "14")
        refresh_interval = get_setting(conn, "refresh_interval", "30")
        total_articles = conn.execute("SELECT COUNT(*) AS c FROM articles").fetchone()["c"]
    return templates.TemplateResponse("feeds.html", {
        "request": request,
        "feeds": feeds,
        "categories": categories,
        "colored_borders": colored_borders,
        "border_opacity": border_opacity,
        "border_size": border_size,
        "retention_days": retention_days,
        "refresh_interval": refresh_interval,
        "total_articles": f"{total_articles:,}",
    })


@app.post("/settings/update-borders")
async def update_borders(request: Request):
    form = await request.form()
    colored_borders = "1" if "colored_borders" in form else "0"
    border_opacity = form.get("border_opacity", "1.0")
    border_size = form.get("border_size", "2")
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


def get_contrast_text_color(hex_color):
    """Return '#ffffff' or '#000000' based on the contrast of the hex color."""
    h = hex_color.lstrip("#")
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255
        return "#000000" if luminance > 0.6 else "#ffffff"
    except Exception:
        return "#ffffff"


@app.get("/dynamic.css")
def dynamic_css():
    from fastapi.responses import Response
    with closing(get_db()) as conn:
        categories = get_categories(conn)
    lines = []
    for cat in categories:
        name = cat["name"]
        color = cat["color"]
        bg = hex_to_dark_bg(color, 0.15)
        text_color = get_contrast_text_color(color)
        lines.append(f"""
.badge-{name} {{ background: {bg}; color: {color}; border-color: {hex_to_dark_bg(color, 0.5)}; }}
.cat-btn-{name} {{ background: {bg}; color: {color}; border-color: {hex_to_dark_bg(color, 0.5)}; }}
.cat-btn-{name}.active {{ background: {color}; color: {text_color}; border-color: {color}; }}
""")
    return Response(content="\n".join(lines), media_type="text/css")


DEFAULT_CATEGORIES = [
    ("computing", "#5b8cff"),
    ("linux",     "#4caf50"),
    ("science",   "#f07030"),
    ("space",     "#9b59b6"),
]


@app.post("/categories/add")
def add_category(name: str = Form(...), color: str = Form("#888888")):
    name = name.strip().lower().replace(" ", "-")
    if name:
        with closing(get_db()) as conn, conn:
            conn.execute(
                "INSERT OR IGNORE INTO categories (name, color) VALUES (?, ?)",
                (name, color),
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
    name = name.strip().lower().replace(" ", "-")
    if name:
        with closing(get_db()) as conn, conn:
            if name != old_name:
                exists = conn.execute("SELECT 1 FROM categories WHERE name = ?", (name,)).fetchone()
                if not exists:
                    conn.execute("INSERT OR IGNORE INTO categories (name, color) VALUES (?, ?)", (name, color))
                    conn.execute("UPDATE feeds SET category = ? WHERE category = ?", (name, old_name))
                    conn.execute("DELETE FROM categories WHERE name = ?", (old_name,))
                else:
                    conn.execute("UPDATE categories SET color = ? WHERE name = ?", (color, name))
                    conn.execute("UPDATE feeds SET category = ? WHERE category = ?", (name, old_name))
                    conn.execute("DELETE FROM categories WHERE name = ?", (old_name,))
            else:
                conn.execute("UPDATE categories SET color = ? WHERE name = ?", (color, old_name))
    return RedirectResponse("/feeds", status_code=303)
