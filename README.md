# News Curator — Podman Backup & Deployment

[![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115.0-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Podman](https://img.shields.io/badge/Podman-Quadlet-892CA0?logo=podman&logoColor=white)](https://podman.io)
[![SQLite](https://img.shields.io/badge/SQLite-WAL%20Mode-003B57?logo=sqlite&logoColor=white)](https://sqlite.org)
[![Jinja2](https://img.shields.io/badge/Templates-Jinja2-B41717?logo=jinja&logoColor=white)](https://palletsprojects.com/p/jinja/)

A fast, self-hosted, dark-themed **RSS news aggregator** dashboard built with **FastAPI**, **Jinja2**, and **SQLite**, running as a rootless **Podman Quadlet** container service.

This repository serves as a complete backup and deployment blueprint containing the entire web application source code, container definition, systemd Quadlet configuration, live container inspection metadata, database snapshot, and application screenshots.

---

## Screenshots

### 1. Grid View (Card Dashboard)
Multi-column responsive card view with color-coded category borders, publication dates, source tags, and relative arrival times:
![News Curator Grid View](screenshots/grid-view.png)

### 2. Table View (Compact List)
Compact horizontal row list layout for quickly scanning through dozens of news articles:
![News Curator Table View](screenshots/table-view.png)

### 3. Manage Sources & Customization Panel
Source management dashboard for adding RSS feeds, toggling feeds on/off, creating custom categories, modifying palette colors, and tuning card border size and opacity:
![News Curator Manage Sources](screenshots/manage-sources.png)

---

## Features

- **Dual Viewing Modes (Grid & Table)**: Instant toggle between a modern responsive card grid and a dense table list layout. Preference is automatically remembered across sessions via `localStorage`.
- **Dynamic Category Styling & Custom Borders**:
  - Assign custom HEX colors to individual categories (e.g., *Computing*, *Defense*, *Linux*, *Science*, *Space*, *World News*).
  - Global card border toggle with customizable border width (px) and border opacity slider.
- **Feed Aggregation & Background Polling**:
  - Asynchronous background polling via `feedparser`.
  - Parses dates, links, summaries, and domains.
  - Displays relative fetch timestamps (`29 m`, `59 m`, `90 m`, etc.).
  - Manual on-demand refresh button.
- **Comprehensive Feed & Category Management**:
  - Add, edit, enable/disable, and delete RSS feed sources.
  - Live article counter per feed source and relative last-fetched indicator.
  - Inline category color picker and category renaming with automatic feed migration.
- **Read-State Tracking**:
  - Marking articles as read on click with subtle opacity dimming to focus on unread stories.
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
podman-backup-newscurator/
├── README.md                      # Documentation & backup overview
├── newscurator.container          # Podman Quadlet systemd container definition
├── inspect.json                   # podman inspect output for the running container
├── .gitignore                     # Git ignore rules for python cache & artifacts
├── app/                           # FastAPI Application Source Code
│   ├── Containerfile              # Podman/Docker image build definition
│   ├── requirements.txt           # Python dependencies
│   ├── app/
│   │   └── main.py                # FastAPI backend routes, background feed parser, DB models
│   ├── static/
│   │   └── style.css              # Custom dark-theme styling, grid & table CSS, animations
│   └── templates/
│       ├── base.html              # Base Jinja2 layout and navigation bar
│       ├── feeds.html             # Manage Sources & Settings configuration UI
│       └── index.html             # News feed dashboard (Grid and Table views)
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
- **Frontend**: Vanilla JavaScript, Modern CSS3 with CSS variables (Dark Catppuccin-inspired theme)
- **Container Runtime**: Podman (rootless) + systemd Quadlets

---

## Quickstart & Deployment

### 1. Host Directory Layout

Ensure the project directories exist on the host:
```bash
# Application code directory
mkdir -p /home/jmc/Source/newscurator/app

# Persistent database storage directory
mkdir -p /home/jmc/Storage/nvme2tb/newscurator/data
```

Copy the application source files to `/home/jmc/Source/newscurator/app` and restore the database to `/home/jmc/Storage/nvme2tb/newscurator/data/news.db`.

---

### 2. Build the Podman Image

Build the container image locally:
```bash
cd /home/jmc/Source/newscurator/app
podman build -t localhost/newscurator:latest -f Containerfile .
```

---

### 3. Deploy with Podman Quadlet (Recommended)

Copy `newscurator.container` into the user systemd Quadlet directory:
```bash
mkdir -p ~/.config/containers/systemd/
cp newscurator.container ~/.config/containers/systemd/
```

**Quadlet Definition (`~/.config/containers/systemd/newscurator.container`)**:
```ini
[Unit]
Description=News Curator
After=network-online.target

[Container]
Image=localhost/newscurator:latest
ContainerName=newscurator
PublishPort=5006:5006
Volume=/home/jmc/Source/newscurator/app:/app:Z
Volume=/home/jmc/Storage/nvme2tb/newscurator/data:/app/data:Z
AutoUpdate=local

[Service]
Restart=on-failure

[Install]
WantedBy=default.target
```

Reload systemd and start the service:
```bash
# Reload systemd to generate the Quadlet service
systemctl --user daemon-reload

# Start and enable the service
systemctl --user start newscurator.service
systemctl --user enable newscurator.service

# Check service status
systemctl --user status newscurator.service
```

---

### 4. Alternative: Run Directly via Podman CLI

If not using systemd Quadlets, start the container manually:
```bash
podman run -d \
  --name newscurator \
  --replace \
  --restart on-failure \
  -p 5006:5006 \
  -v /home/jmc/Source/newscurator/app:/app:Z \
  -v /home/jmc/Storage/nvme2tb/newscurator/data:/app/data:Z \
  localhost/newscurator:latest
```

The web dashboard will be available at: **`http://localhost:5006`** (or your server's IP address on port `5006`).

---

## Database Schema

The application uses SQLite with four core tables:

- **`feeds`**: Registered RSS feed URLs, feed names, category associations, enabled state, and `last_fetched` ISO timestamps.
- **`articles`**: Ingested articles (`guid`, `title`, `link`, `summary`, `published`, `fetched_at`, `status`).
- **`categories`**: Category names and their custom HEX color codes.
- **`settings`**: User configuration key-value pairs (`colored_borders`, `border_opacity`, `border_size`).

---

## Backup & Restoration

### Backup Database
To create a safe online database snapshot while the container is running:
```bash
sqlite3 /home/jmc/Storage/nvme2tb/newscurator/data/news.db ".backup ./news_backup.db"
```

### Restore Database
To restore the database:
```bash
# Stop the container
systemctl --user stop newscurator.service

# Replace the database file
cp ./news_backup.db /home/jmc/Storage/nvme2tb/newscurator/data/news.db

# Restart the service
systemctl --user start newscurator.service
```

---

## License

Created and maintained by [PlasmaDrifter](https://github.com/PlasmaDrifter). Distributed for personal and self-hosted use.
