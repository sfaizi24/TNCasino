# TNCasino WP8b: source health for the first live week (2026 week 4)

Written 2026-09-29 by the orchestrator session, after B10b (PR 13, merge 09bcb71). For the cloud
session, which writes its own engineer briefs from this one, runs its engineers, pushes the branch
`wp8b-sources` cut from `main` and opens the pull request. The orchestrator merges.

Run this package now or later, never earlier: the sources had to roll over to week 4 first, which
they do after Monday night's week-3 game (2026-09-28).

## 1. Where you are

The pipeline (`pipeline/`) scrapes projections from six sources, verifies each source's rows before
they are used, simulates the week, prices the markets and publishes to production. `python -m
pipeline run --week 4` is what the owner will run on Wednesday 2026-09-30 for the first live week
on the new system. This package makes sure the scrapers are right on that day.

- Branch `wp8b-sources`, cut from `main` at or after 4e123d5. Tests on `main`: 1181 pass and one
  is skipped, about 40 s. Lint: `ruff check .` and `ruff format --check .`, both clean on `main`.
- There is no `.env` and tests never need one. Never set `DATABASE_URL`. Never run
  `python -m scripts.publish`, `python -m pipeline run ... --steps publish` or `migrate-legacy`.
  Production is not reachable and must not be.
- Manual runs need a data directory of their own. Make one under your scratch directory and point
  every command at it with `PIPELINE_DATA_DIR`, with `PIPELINE_SEASON=2026` and
  `PIPELINE_LEAGUE_ID=1387602586542018560` (the owner's 2026 league). The `league` step fills it
  from Sleeper's API and ESPN's schedule endpoint (`nfl_players`, `leagues`, `rosters`, `matchups`,
  `nfl_schedules`); the verification checks need `nfl_players`. Never commit a `.db` file.
- Live fetches are for your investigation and the report. Tests run on fixtures only, never on the
  network. The sites: `api.sleeper.app`, ESPN's fantasy API (`lm-api-reads.fantasy.espn.com`),
  `fantasysharks.com`, `firstdown.studio`,
  `fantasypros.com`, and FanDuel's research pages, which `pipeline/sources/fanduel.py` drives with
  Playwright (`playwright install chromium` in the venv first). If the sandbox cannot reach a site
  or cannot install Chromium, say so in the report with the error, check what you can from the
  fixture and the code, and leave that source's live verdict to the orchestrator. Never work
  around a block.
- Do not log in to any website, create accounts, or present the scraper as anything other than
  what it is. No captcha or paywall workarounds. No sportsbooks.
- Quality bar (`CLAUDE.md`): this is a portfolio project, and the code must read as if a careful
  engineer wrote it by hand. A function you rewrite ends shorter and plainer than you found it.
  Everything the pipeline prints is ASCII.

## 2. Read first

1. `CLAUDE.md`.
2. `docs/design/pipeline-v2.md` §7 (sources, their 2026 state, the verification checks).
3. `docs/design/sources-2026-research.md` §6, §7, §9 and §10: what the research found about the
   six sources and their future weeks on 2026-09-27. §4 and §8 are the FFToday package that
   follows this one; do not build it here.
4. `pipeline/sources/` in full (`base.py`, `verify.py`, `teams.py`, the six sources),
   `pipeline/steps/scrape.py` (how a source is fetched, verified, kept or dropped, and how the
   previous week's rows reach `freshness`), `project_week` in `pipeline/steps/playoffs.py` (the
   future-week caller), and the tests and fixtures under `tests/pipeline/`.

## 3. Goal

Every one of the six projection sources either produces verified week-4 data on the day the
pipeline runs, or we know exactly why it cannot and the code says so. The same for the future-week
scraping the playoffs step does (Sleeper, ESPN and FantasySharks for weeks 5 to 14 and the playoff
weeks): each source either passes verification for future weeks or is switched off for them with
the evidence in a comment. Fix scraper bugs where a site changed; where a site is genuinely not
publishing yet, say so with evidence and leave the code alone. Do not loosen a verification
threshold to make a source pass.

## 4. Background: what the first live run found

WP7 ran `python -m pipeline run --week 4` on Sunday 2026-09-27 at 04:47 CDT, during week 3 and
before any week-3 game had been played. The scrape step failed with only sleeper.com and espn.com
usable (`MIN_SOURCES = 3`):

| Source | Week 4 result |
|---|---|
| fantasysharks | `value_agreement` fail: QB r = 0.46, MAD 5.24 against Sleeper |
| fantasypros | 0 rows |
| firstdown | `week_stamp` expected 4, found 3 |
| fanduel | `value_agreement` fail: QB r = 0.60 |
| espn | pass, one warning (Zach Ertz without a team) |
| sleeper | pass |

For the future weeks (the playoffs step scrapes every later week with every source whose
`supports_future_weeks` is true): fantasysharks was dropped for all ten weeks and espn for eight of
ten on `value_agreement` against Sleeper's projections for the same week. Each failed scrape still
costs about 9 s.

The likely reading, unproven until someone looks at the pages after week 3 is over: most sites
still showed week 3 on Sunday morning (FirstDown says so outright; FanDuel's and FantasySharks'
numbers correlating poorly with Sleeper's week-4 numbers is what a wrong-week page looks like;
FantasyPros' week-4 page may not have existed). That is precisely the hard-to-detect failure the
checks exist for, and they caught it. The future-week failures are a different question, and the
research doc's §6 answered part of it (item 1 below).

## 5. Ownership

`pipeline/sources/` (all six sources, `base.py`, `verify.py`, `teams.py`), their tests
(`tests/pipeline/test_sources_api.py`, `test_sources_html.py`, `test_verify.py`, `test_teams.py`)
and their fixtures under `tests/pipeline/fixtures/<source>/`. The two call sites and the deletions
named in section 7 are the only other code changes. `pipeline/steps/scrape.py` beyond that only if
the orchestration of sources has to change, and then say so in the report. Not the model, lineups,
odds or validate steps, not `app/`, not `scripts/`, not `backend/`.

## 6. Tasks

1. **Week 4, every source.** Run `python -m pipeline run --week 4 --steps league,scrape` against
   your data directory. For each source, read its `SourceReport` and then check the data by hand
   against the live page or API response: the week the page displays; ten named players per
   position (QB, RB, WR, TE, K, DEF) with their points, positions and teams; that the points
   column is PPR scoring and not standard or half; that injured or bye-week players are handled as
   the source shows them. Record each verdict with
   `python -m pipeline review --week 4 --source <website> --verdict ok|reject --note "..."` against
   your data directory so the command gets exercised, and put the same evidence in the report.
2. **Fix what is broken.** A changed layout or endpoint gets a parser fix and a refreshed fixture
   (save the real page or response the way the existing fixtures were saved: a few hundred rows,
   under 300 KB, the `<head>`, the week markers and the table kept; strip nothing the parser
   reads). A source that is right but flagged gets an evidence-based look at the check that
   flagged it: say what the check assumed and why it does not hold, and propose the narrowest
   change. A source that has simply not published week 4 yet gets no code change; the report says
   when it publishes (from its own page, an FAQ or its behaviour over the last two seasons).
3. **Future weeks.** For weeks 5, 8 and 12, fetch Sleeper's, ESPN's and FantasySharks' projections
   with `--week N --steps scrape --sources sleeper,espn,fantasysharks` (the scrape step skips the
   other sources for a future week) and compare a dozen named players across the three. Decide
   per source: (a) it publishes week-specific numbers and the scraper reads the right week; (b)
   the scraper asks for the wrong week (fix it); (c) the site serves one set of numbers for every
   future week or a rest-of-season total (set `supports_future_weeks = False` with a comment
   giving the evidence); (d) the numbers are week-specific but disagree with Sleeper's more than
   `value_agreement` allows (section 7 item 1 decides the future-week variant of the check: apply
   it and report the correlation and MAD per position). A source that returns identical rows for
   two different weeks is case (c) whatever the page claims.
4. **Verification.** Add to `verify.py` whatever the above shows is missing, in its existing
   `Check` / `SourceReport` shape. `verify.py` already has `freshness` (rows unchanged from the
   previous week's rows) and `week_stamp`; look at why they did not catch FanDuel and
   FantasySharks on Sunday before adding anything. In a data directory with no earlier week,
   `freshness` has nothing to compare against: say what it does then, and whether that is what
   let them through. The `has_week_stamp` idea from WP2b, for sources that print the week on the
   page, is the one candidate. Every new or changed check gets a test with a fixture that fails it.
5. **Cost.** If a source can serve several weeks in one request, say so; do not build it unless it
   is a clear and small win. Do not add retries, caching layers or parallel fetching.

## 7. Decisions from the sources research (2026-09-28), to apply here

`docs/design/sources-2026-research.md` probed the six sources and the candidates on the night of
2026-09-27. Its findings change this package as follows; where an item names a file outside
section 5, that item is the only reason to touch it.

1. **Future weeks: case (d) is decided, apply it.** ESPN's future weeks are honest week-by-week
   projections. RB, WR and TE pass `value_agreement` in weeks 5, 7 and 13; QB fails only on r
   (0.74 to 0.78) while its median absolute difference to Sleeper stays at 1.2 to 1.5, far inside
   the 4.0 limit. The stored week-4 run therefore dropped ESPN for nine of ten future weeks for
   nothing. For future weeks only, `value_agreement` treats QB the way it treats K and DEF today:
   r below 0.85 warns and the median absolute difference decides. The current week keeps the
   present rule. Thread a keyword argument (`future_week=False`) from `verify_source` through
   `scrape.scrape_source`; `project_week` in `pipeline/steps/playoffs.py` is the only future-week
   caller and passes True. Those two call sites are the only lines this item changes outside
   `pipeline/sources/`. Add fixture tests showing a current-week QB failure still fails and the
   same numbers warn for a future week. ESPN leaves players on bye out of future weeks (DEF
   excepted); that is correct and the count ranges allow it. Sleeper's future-week kickers carry
   one number for every week; that is a Sleeper fact, leave it and say so in the report.
2. **FantasyPros leaves.** Logged out it serves 10 rows per position, so `position_counts` fails
   every week; it averages sites we read directly; the model fit excludes it. Remove it. This is
   the one package where deleting files is allowed, for FantasyPros only:
   `pipeline/sources/fantasypros.py`, `tests/pipeline/fixtures/fantasypros/`, its tests and
   fixture loader in `tests/pipeline/test_sources_html.py` (also `SOURCES`, `SOURCE_IDS` and the
   website list there), its entry in `SOURCE_NAMES` (`pipeline/sources/__init__.py`), the comments
   naming it in `pipeline/sources/base.py` and `pipeline/sources/teams.py`, the `--exclude` help
   example in `pipeline/__main__.py` (name another website). The tests that hard-code the six
   sources (`tests/pipeline/test_scrape_step.py` near lines 208 and 235,
   `tests/pipeline/test_playoffs.py` near lines 453 and 459) should derive their expectations from
   `SOURCE_NAMES` or use another source, so the FFToday package after you does not trip over them.
   `docs/design/pipeline-v2.md` §7.2: replace the FantasyPros row with one line saying it was
   removed in 2026-09 and why; `docs/architecture/08-constraints-and-debt.md` line 42: drop the
   mention. Leave alone: the `excluded` lists in `pipeline/model/params/v2.json` and `v2.1.json`
   (fit records), doc 03's mention of the exclusion, tests that use "fantasypros.com" merely as a
   source string in their data, and the legacy `backend/`, `scripts/` and skills (cutover deletes
   them).
3. **Every source identifies itself.** ESPN, FantasySharks, FirstDown (and FantasyPros) send a
   Chrome User-Agent; Sleeper sends the `requests` default. One constant in
   `pipeline/sources/base.py`, `USER_AGENT = "TNCasino-pipeline/2026 (+https://tncasino.win)"`,
   imported by every requests-based source, replaces them. FanDuel drives a real browser through
   Playwright and stays as it is. If a site refuses the truthful header, keep it truthful, record
   the response code in the report and stop there; the orchestrator decides whether that source
   stays. Never disguise a request.
4. **Requests are spaced and robots crawl delays are honoured.** None of the requests-based
   sources pauses between page fetches today. At least 2 s between requests to one site, and a
   site's `Crawl-delay` when its robots.txt states one: FantasySharks says 60 s. With 60 s its six
   positions take six minutes for the current week and the playoffs step's future weeks about an
   hour. Make the wait visible in the run's output in the form the scrape step already uses for
   per-source progress, and put the delay beside the URL with the robots line quoted in a comment.
   This is a policy the owner set, not a technical limit.
5. **A dropped future-week source leaves its reason.** `project_week` records the failed checks'
   names only, so the warning reads "dropped X for weeks ...: value_agreement failed" and nobody
   learns which position or numbers failed. Include each failed check's `detail` in `failures`
   and the warning. That, and the flag from item 1, are the only changes to `playoffs.py`.
6. **What the doc found about the six, to test against week 4.** FantasySharks' QB points ran a
   median 4.63 above Sleeper's on the pages of 2026-09-27 and 2.01 after rescoring its stat lines
   with league scoring, r 0.78 either way, so it fails on r whichever way it is scored. On week-4
   data decide and report, with numbers, whether to rescore from the stat lines (if every position
   has them) and whether FantasySharks should list `positions` without QB rather than lose the
   whole source on QB alone; the orchestrator picks. Sleeper's `pts_ppr` is not league scoring
   (interceptions -1 not -2, no yards-allowed brackets for DEF); that is a model-side matter,
   leave the Sleeper scraper alone.
7. **After you.** A separate package adds FFToday once this one has merged (both touch
   `tests/pipeline/test_sources_html.py`). The docs drift in the doc lists (design 7.3 count
   ranges; doc 08's FFToday `LeagueID` and FantasySharks `scoring`) is fixed there or at cutover,
   not here. Do not touch `pipeline/model`.

## 8. Constraints

- Sleeper is the reference the other sources are checked against; do not change its scraper
  without evidence that it is wrong.
- Keep each source module in its current shape (`ProjectionSource`, `fetch(season, week)`,
  `Projection` rows); the scrape step and the playoffs step both depend on it.
- Tests pass without the network: fixtures only.
- `python -m pytest -q`, `ruff check .` and `ruff format --check .` clean before the pull request.
  Commit per source or per check, not one commit for everything.

## 9. The pull request

Its description carries, in this order: one table per source with the week-4 rows and checks
(pass, warn or fail with the numbers), the manual spot check (three named examples with the
page's numbers next to the scraper's), the verdict recorded, and the change made if any. One table
for the future weeks: source by week (5, 8 and 12) with pass or fail, the reason, and the case (a)
to (d) concluded. Then: files changed, tests added and the pytest total, the lint results, anything
that could not be verified from the sandbox (a blocked site, no Chromium), and what the
orchestrator has to decide: any source to drop for the season, any site that refused the truthful
User-Agent, the FantasySharks QB choice.
