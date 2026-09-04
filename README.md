# News Curator

[![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115.0-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Podman](https://img.shields.io/badge/Podman-Quadlet-892CA0?logo=podman&logoColor=white)](https://podman.io)
[![SQLite](https://img.shields.io/badge/SQLite-WAL%20Mode-003B57?logo=sqlite&logoColor=white)](https://sqlite.org)
[![Jinja2](https://img.shields.io/badge/Templates-Jinja2-B41717?logo=jinja&logoColor=white)](https://palletsprojects.com/p/jinja/)

A fast, self-hosted, dark-themed **RSS news aggregator** dashboard built with **FastAPI**, **Jinja2**, and **SQLite**, running as a rootless **Podman Quadlet** container service.

This repository contains the complete web application source code, container definition, systemd Quadlet configuration, and application screenshots.

---

## Screenshots

### 1. Grid View (Card Dashboard)
Multi-column responsive card view with color-coded category borders, publication dates, source tags, and relative arrival times:
![News Curator Grid View](screenshots/grid-view.png)

### 2. Table View (Compact List)
Compact horizontal row list layout for quickly scanning through dozens of news articles:
![News Curator Table View](screenshots/table-view.png)

### 3. Manage Sources & Customization Panel
Source management dashboard for adding RSS feeds, toggling feeds on/off, creating custom categories, modifying palette colors, tuning card border size and opacity, and configuring article retention:
![News Curator Manage Sources](screenshots/manage-sources.png)

---

## Features

- **Dual Viewing Modes (Grid & Table)**:
  - Instant toggle between a modern responsive card grid and a dense table list layout.
  - Preference is automatically remembered across sessions via `localStorage`.
- **Dynamic Category Styling & Custom Borders**:
  - Assign custom HEX colors to individual categories (e.g., *Computing*, *Defense*, *Linux*, *Science*, *Space*, *World News*).
  - Global card border toggle with customizable border width (px) and border opacity slider.
- **Feed Aggregation & Background Polling**:
  - Non-blocking asynchronous background polling via `feedparser`.
  - Parses dates, links, summaries, and domains.
  - Displays relative fetch timestamps (`29 m`, `59 m`, `90 m`, etc.).
  - Manual on-demand refresh button.
- **Comprehensive Feed & Category Management**:
  - Add, edit, enable/disable, and delete RSS feed sources.
  - Live article counter per feed source and relative last-fetched indicator.
  - Inline category color picker and category renaming with automatic feed migration.
- **Full-Card Direct Navigation & Read Tracking**:
  - Clicking anywhere on an article card or table row opens the story in a new tab.
  - Automatically tracks read-state with subtle opacity dimming to keep unread content front and center.
- **Article Bookmarking & Dedicated Saved View**:
  - Upper-right bookmark tag on every article card and table row with subtle 60% idle transparency and bright full-color active state.
  - Independent bookmark click handling: toggles bookmark state asynchronously without unintentionally opening the article link.
  - Dedicated bookmarks button in the navigation header directly next to the search bar.
  - Full-opacity display mode in the bookmarked listings view (disabling read-state dimming).
  - Saved articles are permanently immune to automatic retention pruning.
- **Abbreviated Date Formatting**:
  - Publication dates formatted with standard 3-4 letter month abbreviations (e.g., Aug., Sept., Oct., June, July).
- **Integrated Top Bar Search**:
  - Centered search input directly in the navigation header.
  - Dynamically searches across article titles and summaries.
  - High-contrast, prominent clear ("x") button that reactively appears as you type to reset search terms in one click.
  - Seamlessly combines with category pills and source filters.
- **Dynamic Infinite Scrolling**:
  - Smooth asynchronous loading via modern `IntersectionObserver`.
  - Automatically fetches and appends the next batch of articles as you scroll down the page.
  - Seamlessly works in both Grid and Table view modes without page reloads or scroll resets.
- **Configurable Refresh Interval (15, 30, 60 Min or Custom)**:
  - Presets for 15 minutes, 30 minutes (Default), 60 minutes, or any custom minute duration.
  - Dynamically updates the background polling loop without service restarts.
- **Configurable Data Retention (7, 14, 30 Days)**:
  - Selectable retention policy (7 Days, 14 Days [Default], or 30 Days) in the Manage Sources settings panel.
  - Real-time display of the total saved article count.
  - Automatic background purging on startup and scheduled feed refresh cycles (with immunity for bookmarked articles).
- **Private & Lightweight**:
  - Rootless Podman container.
  - SQLite backend operating with WAL mode (`journal_mode = WAL`, `synchronous = NORMAL`) for high-concurrency read/write operations.
  - Zero cloud tracking or third-party telemetry.
- **Systemd Quadlet Integration**:
  - Native systemd service auto-generation via Podman Quadlet (`newscurator.container`).
  - Automatic restart on failure and auto-update support.

---

## Repository Structure

```text
NewsCurator/
├── README.md                      # Documentation & overview
├── newscurator.container          # Podman Quadlet systemd container definition
├── inspect.json                   # podman inspect output for the running container
├── .gitignore                     # Git ignore rules for python cache & artifacts
├── app/                           # FastAPI Application Source Code
│   ├── Containerfile              # Podman/Docker image build definition
│   ├── requirements.txt           # Python dependencies
│   ├── app/
│   │   └── main.py                # FastAPI backend routes, background feed parser, DB models, API
│   ├── static/
│   │   └── style.css              # Custom dark-theme styling, grid & table CSS, animations
│   └── templates/
│       ├── base.html              # Base Jinja2 layout, centered search box, and navigation bar
│       ├── feeds.html             # Manage Sources, Retention & Settings configuration UI
│       └── index.html             # News feed dashboard with dynamic infinite scroll
├── newscurator-data/              # Data persistence mount
│   └── news.db                    # SQLite database snapshot (feeds, articles, categories, settings)
└── screenshots/                   # Application screenshots
    ├── grid-view.png              # News dashboard in Card Grid view
    ├── table-view.png             # News dashboard in Table view
    └── manage-sources.png         # Source & category management panel
```

---

## Tech Stack & Requirements

- **Backend**: Python 3.12, FastAPI, Uvicorn, Feedparser, Jinja2
- **Database**: SQLite 3 (WAL mode)
- **Frontend**: Vanilla JavaScript (`IntersectionObserver`), Modern CSS3 with CSS variables (Dark Catppuccin-inspired theme)
- **Container Runtime**: Podman (rootless) + systemd Quadlets

---

## Quickstart & Deployment

### 1. Host Directory Layout

Choose a directory location on your host machine (for example, inside your home directory `~/newscurator` or `/opt/newscurator`):

```bash
# Set your preferred installation directory
export NEWSCURATOR_DIR="$HOME/newscurator"

# Create application and persistent database directories
mkdir -p "$NEWSCURATOR_DIR/app/data"
```

Copy the repository `app/` files into `$NEWSCURATOR_DIR/app/` and place your `news.db` database into `$NEWSCURATOR_DIR/app/data/news.db`.

---

### 2. Build the Podman Image

Build the container image locally:

```bash
cd "$NEWSCURATOR_DIR/app"
podman build -t localhost/newscurator:latest -f Containerfile .
```

---

### 3. Deploy with Podman Quadlet (Recommended)

Systemd Quadlets automatically manage rootless Podman containers as system services.

1. Ensure your user Quadlet directory exists:
   ```bash
   mkdir -p ~/.config/containers/systemd/
   ```

2. Copy or create `newscurator.container` in `~/.config/containers/systemd/newscurator.container`.

**Quadlet Definition (`~/.config/containers/systemd/newscurator.container`)**:
```ini
[Unit]
Description=News Curator
After=network-online.target

[Container]
Image=localhost/newscurator:latest
ContainerName=newscurator
PublishPort=5006:5006
Volume=%h/newscurator/app:/app:Z
Volume=%h/newscurator/app/data:/app/data:Z
AutoUpdate=local

[Service]
Restart=on-failure

[Install]
WantedBy=default.target
```

*(Note: `%h` is a standard systemd specifier that automatically expands to the current user's home directory. Adjust `%h/newscurator/app` if you placed the files in a custom directory).*

3. Reload systemd and start the service:
   ```bash
   # Reload systemd daemon to generate the Quadlet container service
   systemctl --user daemon-reload

   # Start and enable the service
   systemctl --user start newscurator.service
   systemctl --user enable newscurator.service

   # Verify running status
   systemctl --user status newscurator.service
   ```

---

### 4. Alternative: Run Directly via Podman CLI

If you prefer launching directly with the Podman CLI:

```bash
export NEWSCURATOR_DIR="$HOME/newscurator"

podman run -d \
  --name newscurator \
  --replace \
  --restart on-failure \
  -p 5006:5006 \
  -v "$NEWSCURATOR_DIR/app:/app:Z" \
  -v "$NEWSCURATOR_DIR/app/data:/app/data:Z" \
  localhost/newscurator:latest
```

The web dashboard will be available at: **`http://localhost:5006`** (or `http://<server-ip>:5006`).

---

## Database Schema

The application uses SQLite with four core tables:

- **`feeds`**: Registered RSS feed URLs, feed names, category associations, enabled state, and `last_fetched` ISO timestamps.
- **`articles`**: Ingested articles (`guid`, `title`, `link`, `summary`, `image_url`, `published`, `fetched_at`, `status`, `is_bookmarked`).
- **`categories`**: Category names and their custom HEX color codes.
- **`settings`**: User configuration key-value pairs (`colored_borders`, `border_opacity`, `border_size`, `retention_days`, `refresh_interval`).

---

## License

Created and maintained by [PlasmaDrifter](https://github.com/PlasmaDrifter). Distributed for personal and self-hosted use.
