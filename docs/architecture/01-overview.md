# 01 – Overview

## Components

| Component | Location | Runs where | Responsibility |
|---|---|---|---|
| Projection scrapers | `backend/scrapers/scraper_*.py` | Operator machine | Pull weekly player projections from 5 sources into `projections.db` |
| League scraper | `backend/scrapers/scraper_sleeper_league.py` | Operator machine | Pull league, rosters, matchups, players, stats from the Sleeper API into `league.db` |
| SQLite access classes | `backend/scrapers/database.py`, `database_league.py` | Operator machine | Schema creation and upserts for `projections.db` and `league.db` |
| Scrape CLI | `scripts/scrape.py`, `scripts/validate_scraping.py` | Operator machine | Run scrapers with per-source isolation and check data quality |
| Notebooks | `backend/notebooks/01–10_*.ipynb` | Operator machine (Jupyter) | Cleaning, matching, stats, lineups, simulation, odds, validation, accuracy analysis |
| Publisher | `scripts/publish.py` | Operator machine → prod DB | Copy analytics tables from SQLite into Postgres atomically |
| Web app | `app/` + `frontend/` | Production droplet | Serve odds/analytics, handle auth, bets, admin |
| PostgreSQL | Production droplet | Production droplet | App tables + published analytics tables in one database |

There is **no scheduler, queue, or background worker**. Every pipeline step is triggered by hand. The web app does no computation beyond SQL reads and small aggregations.

## Offline vs. online boundary

```mermaid
flowchart TB
    subgraph Offline["Offline — operator machine"]
        direction LR
        A[Scrape] --> B[Clean & match] --> C[Player stats] --> D[Lineups] --> E[Simulate & price] --> F[Playoff odds]
    end
    subgraph Boundary["Handoff"]
        P1[publish.py → Postgres]
        P2[scp PNGs → droplet]
    end
    subgraph Online["Online — production"]
        W[Flask reads analytics tables<br/>writes app tables]
    end
    Offline --> Boundary --> Online
```

The only contract between the two halves is the **set of Postgres table names and columns** listed in `scripts/publish.py` (`TABLE_MAP`) plus the PNG filenames. Nothing validates that contract except the row-count check in `publish.py` and the hand-written SQLite DDL in `tests/conftest.py`.

## Weekly operating cycle

```mermaid
sequenceDiagram
    actor Op as Operator (admin)
    participant Local as Local pipeline
    participant PG as Postgres
    participant App as Flask app
    actor User as Users

    Note over Op,App: Tue/Wed — open the week
    Op->>App: /admin → set betting period (week, lock_time)
    Op->>Local: python -m scripts.scrape --week N --validate
    Op->>Local: Run notebooks 01, 03–07, 09 (edit CURRENT_WEEK in each)
    Op->>Local: Notebook 08 (sanity checks)
    Op->>PG: python -m scripts.publish
    Op->>App: scp chart PNGs to droplet

    Note over User,App: Until lock_time (typically Thursday kickoff)
    User->>App: View odds, analytics
    User->>App: Place / remove bets
    App->>PG: bets, weekly_stats, users.balance

    Note over App: First bet/remove request after lock_time flips is_locked

    Note over Op,App: After games finish
    Op->>App: /admin → mark each pending bet won/lost
    Op->>App: /admin → settle week
    Note over App: get_current_week() now returns the next unsettled period
```

Timing notes:

- The lock is **lazy**: nothing flips `is_locked` at `lock_time`; the first `place_bet` / `remove_bet` call after that time does it (`check_betting_period_lock`).
- Settlement is **manual per bet**. The app has no knowledge of real game results; the admin decides won/lost for each bet.
- Notebook 10 (prediction accuracy) is run occasionally, not weekly, and produces no outputs.

## Technology

| Layer | Stack |
|---|---|
| Language | Python 3.13 |
| Web | Flask, Flask-Login, Flask-Dance (Google OAuth), Flask-WTF (CSRF), Flask-SQLAlchemy |
| Frontend | Jinja2 templates, vanilla JS, Chart.js 4.4.1 (CDN, analytics page only) |
| Data | pandas, NumPy, SciPy (`gaussian_kde`), matplotlib/seaborn |
| Scraping | requests, Selenium (Chrome), Playwright (Chromium) |
| Storage | SQLite (pipeline), PostgreSQL (production) |
| Serving | gunicorn behind nginx, Cloudflare DNS/SSL, DigitalOcean droplet |
| Quality | ruff (lint + format), pytest (88 tests), GitHub Actions |
