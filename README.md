# TNCasino: Fantasy Football Analytics & Fake Betting Platform

---

## TLDR

TNCasino is FanDuel but with fake money, for betting on the outcomes of my fantasy league (named TNC).

Data comes from 8 different fantasy football projection sources. The data from those sources gets scraped, checked against Sleeper, heavily cleaned, and used to create the distribution parameters for the simulations. Simulations get run and the results of those simulations are used to create betting odds.

For how the whole system works under the hood, see the [architecture docs](docs/architecture/README.md).

**From Projections to Odds:**

For each team, player projections (μ, σ) are aggregated from multiple sources. Here's how the parameters are calculated:

**1. Mean Projection (μ):**
$$\mu = \text{mean}(\text{projections across all sources})$$

**2. Standard Deviation (σ):**
Uncertainty is modeled as a combination of source disagreement and inherent position variance:

$$\sigma = \sqrt{(\alpha \cdot s)^2 + (\beta \cdot \sigma_{\text{pos}})^2}$$

Where:
- **s** = sample standard deviation of projections across sources (ddof=1)
- **σ_pos** = baseline uncertainty by position (QB: 7.0, RB: 9.0, WR: 10.0, TE: 8.0, K: 4.0, DST: 7.0. Got these from some quick searches, will update later)
- **α** = 2.0 (weight for source disagreement)
- **β** = 1.0 (weight for baseline position variance)

**3. Variance:**
$$\text{var} = \sigma^2$$

**4. Monte Carlo Simulation** (50,000 iterations):
   - Sample every starter's score from a lognormal with that player's μ and σ, then sum them into team totals $T_1$ and $T_2$

**5. Win Probability**:
   $$P_1 = \frac{\text{count}(T_1 > T_2)}{50,000}$$

**6. American Odds**:
   $$\text{ML}_1 = \begin{cases} 
   -\frac{100P_1}{1-P_1} & \text{if } P_1 \geq 0.5 \\
   +\frac{100(1-P_1)}{P_1} & \text{if } P_1 < 0.5
   \end{cases}$$


That is the v1 baseline. The default parameters today, v2.3, were fitted on 2025 weeks 10 to 16: each source gets a weight and a per-position bias in μ, σ grows linearly with μ by position, a player can have a dud week, and teammates' scores are correlated. They live in `pipeline/model/params/`.

So:
- A QB that is projected for 13-15 points by every source will have a low mean but low variance
- A WR with projections of 10, 12, 18, and 21 will have a higher mean but may actually result in lower odds to win than if they were swapped out with the QB, depending on the team around them.

# **[Visit TNCasino.win](https://tncasino.win)**

## Betting Interface

![Matchups Moneyline](docs/images/matchups.png)

Each bet allows you to see the players on the teams in question, allowing for non-league members to place bets. The season-long leader in PnL was not a league member!


## Team Analytics & Statistical Distributions

![Team Analytics](docs/images/analytics.png)

The analytics page has interactive charts for any two teams: score distributions, win margin, lineup comparison, league standings, and position strength. There are a ton more charts I create every week that don't make it to the site yet.

## Leaderboard & Performance Tracking

![Leaderboard](docs/images/leaderboard.png)

The leaderboard displays all-time and weekly top performers, even the best and worst bets.

## Lessons learned

Not that I didn't know this, but data cleaning is very time-consuming! That's where most of the leg-work in this project went.
I typically make the odds on Tuesday or Wednesday to close at Thursday Night kickoff, but someone might not even pick up a replacement for their kicker who's on bye until Saturday.

To fix this, I created the concept of a "replacement player", the x (x is configurable based on your league size) best player at that position. If a team's "best" lineup (based on projection mean) has a player that is projected to score less than the "replacement player", then the replacement player's stats are inserted in for that position. 

This works surprisingly well lol

## Technical Architecture

Full details live in [docs/architecture](docs/architecture/README.md). The short version:

### Data Pipeline

`python -m pipeline run --week N` runs these steps in order; the publish step runs when named.

```
 1. league      → Sleeper league, rosters, matchups, players; NFL schedule from ESPN
 2. scrape      → 8 projection sources, each verified against Sleeper
 3. clean       → Names, positions, team codes, defenses made canonical
 4. match       → Every projection linked to a Sleeper player ID
 5. stats       → One distribution (μ, σ) per player from the model parameters
 6. accuracy    → Last week's projections and odds graded against what happened
 7. calibrate   → Season-to-date calibration of the model
 8. lineups     → Best lineup per team, holes filled from the waiver wire
 9. simulate    → 50,000 correlated simulations of every starter
10. odds        → Weekly markets and chart curves
11. playoffs    → Rest of the season simulated: first place, playoffs, last place, champion
12. validate    → Cross-table checks before anything is published
13. publish     → Charts and tables to production PostgreSQL
```

### Technology Stack

- **Backend**: Python, Flask, SQLAlchemy, Google OAuth
- **Databases**: SQLite (local pipeline), PostgreSQL (production)
- **Data Processing**: Pandas, NumPy, SciPy, PyArrow (Parquet draws)
- **Web Scraping**: requests, BeautifulSoup, Playwright
- **Statistical Modeling**: Custom Monte Carlo implementation
- **Frontend**: HTML/CSS/JavaScript, Chart.js, responsive design
- **Visualization**: Matplotlib/Seaborn (static images), Chart.js (site)
- **Hosting**: DigitalOcean droplet, gunicorn + nginx, Cloudflare

### Databases

The pipeline writes to four local SQLite databases in `backend/data/databases/`:
- **league.db**: Teams, rosters, matchups, NFL players, player stats, NFL schedule (from Sleeper and ESPN)
- **projections.db**: Multi-source player projections, per-player stats, lineups, and accuracy grades
- **odds.db**: Betting odds, precomputed chart curves, simulation runs, and calibration
- **pipeline.db**: A record of every run and step, and the verdicts on each source

The simulation draws are stored as Parquet files in `backend/data/sims/`. The publish step copies the tables the site needs into production PostgreSQL, which also holds users, bets, balances, and betting periods, and appends each run's score matrix there so bets can be priced and settled at the run they were placed on.

---

## 📈 Project Structure

```
├── backend/
│   └── data/                   # Local pipeline output (gitignored)
│       ├── databases/          # SQLite: league, projections, odds, pipeline
│       ├── sims/               # Simulation draws (Parquet)
│       └── images/             # Charts for the analytics page
├── pipeline/                   # The weekly pipeline (python -m pipeline)
│   ├── __main__.py             # CLI: run, status, review, backfill, fit-model
│   ├── runner.py               # Runs steps in order, records each run
│   ├── settings.py             # Season, week, league, paths
│   ├── sources/                # The 8 projection sources and their checks
│   ├── steps/                  # The 13 steps, league to publish
│   └── model/                  # Sampling, fitting, versioned parameters
├── frontend/
│   ├── templates/              # Flask HTML templates
│   │   ├── betting.html        # Matchup betting interface
│   │   ├── analytics.html      # Team research dashboard
│   │   ├── leaderboard.html    # Performance rankings
│   │   └── ...
│   └── static/
│       └── images/             # Web assets
├── app/                        # Flask application package
│   ├── __init__.py             # App factory + gunicorn entry point
│   ├── auth.py                 # Google OAuth + login manager
│   ├── database.py             # SQLAlchemy instance
│   ├── models.py               # Database models
│   └── routes/                 # Blueprints (pages, betting, odds, admin, account)
├── docs/architecture/          # How the system works today
├── tests/                      # Pytest suite (app on in-memory SQLite, pipeline on scratch SQLite)
└── requirements.txt            # Python dependencies
```

---

## Running this yourself

### Installation

```bash
# Clone the repository
git clone <repository-url>
cd "Claude Model"

# Install dependencies
pip install -r requirements.txt

# Install Playwright browser (for the FanDuel source)
playwright install chromium
```

### Configuration

Create a `.env` file:

```
# Pipeline
SLEEPER_USERNAME=your_username
LEAGUE_ID=your_league_id
FLEAFLICKER_LEAGUE_ID=your_league_id
# Optional: skips discovering the league from SLEEPER_USERNAME and LEAGUE_ID
PIPELINE_LEAGUE_ID=your_sleeper_league_id

# Web app
SECRET_KEY=any_random_string
DATABASE_URL=postgresql://user:password@localhost:5432/tncasino
GOOGLE_OAUTH_CLIENT_ID=your_client_id
GOOGLE_OAUTH_CLIENT_SECRET=your_client_secret
ADMIN_EMAILS=you@example.com

# Local dev only (lets Google OAuth work over http://localhost)
OAUTHLIB_INSECURE_TRANSPORT=1
OAUTHLIB_RELAX_TOKEN_SCOPE=1
```

### Running the Pipeline

Run the week:

```bash
python -m pipeline run --week 5
```

That fetches the league from Sleeper, scrapes the eight projection sources and checks each against Sleeper (a source that fails is dropped for the week), matches every projection to a Sleeper player, blends the sources into one distribution per player, builds each team's lineup, runs 50,000 simulations, prices the week's odds and the season's futures, and validates the result. Everything lands in local SQLite files under `backend/data/`.

Check how each step went, and record a verdict on any source that looks wrong:

```bash
python -m pipeline status --week 5
python -m pipeline review --week 5 --source espn.com --verdict ok --note "top players look right"
```

Publish what the site needs to PostgreSQL. With `DATABASE_URL` pointed at production, see what would be uploaded first, then publish for real:

```bash
python -m pipeline run --week 5 --steps publish --dry-run
python -m pipeline run --week 5 --steps publish
```

Then run the site:

```bash
python -m app
```

Tests run with `python -m pytest`.

email me for more information if you do want to do this yourself

---

## Future Enhancements

Potential improvements (if I continue developing):
- Machine learning models for player projection refinement
- Historical accuracy tracking of projections
- Advanced betting strategies (parlays, teasers)
- Real-time data updates after TNF so that we can bet until Sunday
- API endpoints for programmatic access

---

## Notes

**This project was built purely for fun as an analytical side project.** It combines my interests in:
- Fantasy football strategy
- Statistical modeling and simulation
- Web development and user experience
- Data engineering and ETL pipelines

The codebase reflects iterative development and experimentation rather than production-ready engineering. It's a demonstration of curiosity-driven learning and applying data science techniques to a domain I'm passionate about.

---

## License

This project is for personal/educational use.


*Built with Python, Flask, and a lot of curiosity about fantasy football statistics.*
