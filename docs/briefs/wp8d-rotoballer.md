# TNCasino WP8d: RotoBaller as a seventh projection source

Written 2026-09-29 by the orchestrator session, after WP8c (PR 16, merge f0af278) and the shared
test fakes (4ade1df). For the cloud session, which writes its own engineer briefs from this one,
runs its engineers, pushes the branch `wp8d-rotoballer` cut from `main` and opens the pull
request. The orchestrator merges.

## 1. Where you are

The pipeline (`pipeline/`) scrapes projections from six sources (Sleeper, ESPN, FantasySharks,
FirstDown, FanDuel, FFToday), verifies each source's rows against Sleeper before they are used,
simulates the week and prices the markets. RotoBaller is the first source the project reads under
a written permission: its terms of use bar storing and reusing its data, and on 2026-09-28
RotoBaller sent the owner a "Limited Data Usage Authorization" letter for TNCasino. The letter is
not in the repository (the owner keeps it outside git); its conditions are quoted in section 4 and
turned into rules in section 7. The research that chose RotoBaller is
`docs/design/sources-2026-research.md`, section 5.1.

- Branch `wp8d-rotoballer`, cut from `main` at or after 4ade1df. Tests on `main`: 1202 pass and
  one is skipped, about 50 s. Lint: `ruff check .` and `ruff format --check .`, both clean.
- There is no `.env` and tests never need one. Never set `DATABASE_URL`. Never run
  `python -m scripts.publish`, `python -m pipeline run ... --steps publish` or `migrate-legacy`.
  Production is not reachable and must not be.
- Manual runs need a data directory of their own. Make one under your scratch directory and point
  every command at it with `PIPELINE_DATA_DIR`, with `PIPELINE_SEASON=2026` and
  `PIPELINE_LEAGUE_ID=1387602586542018560`. `python -m pipeline run --week 4 --steps league` fills
  it from Sleeper's API; the verification checks need its `nfl_players`. Never commit a `.db` file.
- Live fetches are for your investigation and the report. Tests run on fixtures only, never on the
  network. The sandbox reached `api.sleeper.app`, ESPN and `www.fftoday.com` but not
  `fantasysharks.com`, so the first thing to learn is whether it reaches `www.rotoballer.com`. If
  it does not, build from the committed fixtures, say so in the pull request, and the orchestrator
  runs the live checks from the owner's machine.
- Every request identifies itself as `USER_AGENT` from `pipeline/sources/base.py` and goes through
  its `get(url, spacing_s=...)`, which spaces requests to one host. No login, no account, no
  disguise, no scraping anything but the two URLs named here.

## 2. Read first

1. `CLAUDE.md`.
2. `docs/design/sources-2026-research.md` §5.1 in full (terms, robots, the two articles a week,
   columns, half-PPR points, the week-3 comparison, name cases, the build plan and its risks).
3. `docs/design/pipeline-v2.md` §7 (the sources, the verification checks, the 7.2 table you add a
   row to).
4. `pipeline/sources/base.py`, `verify.py`, `teams.py`, `fftoday.py` (the module to copy the
   shape of: a `ProjectionSource` whose `fetch` calls a pure `parse`, points rescored from a stat
   line, the week read from the page), `pipeline/names.py` (`split_full_name`),
   `pipeline/steps/scrape.py` (how a source is fetched, verified, kept or dropped),
   `pipeline/steps/match.py`'s docstring (how a row finds its Sleeper player), and
   `tests/pipeline/test_sources_html.py` with `tests/pipeline/conftest.py` (the `clock` and
   `serve` fakes every fetch test uses) and the fixtures under `tests/pipeline/fixtures/`.

## 3. Goal

RotoBaller's QB, RB, WR and TE projections enter the pipeline as the source `rotoballer` and pass
verification against Sleeper on a live week, within the letter's conditions. Acceptance:

- `python -m pipeline run --week N --steps league,scrape --sources sleeper,rotoballer` completes
  on the week RotoBaller currently shows, with `rotoballer.com` at `ok` (a `warn` needs its reason
  in the pull request). If the sandbox cannot reach the site, the orchestrator runs this line.
- The fixture tests pass without the network. `python -m pytest -q`, `python -m ruff check .` and
  `python -m ruff format --check .` are clean.
- No verification threshold changed. Nothing else in the pipeline changed. Nothing new shows a
  RotoBaller row to anyone.

## 4. Background

### The permission

RotoBaller's letter of 2026-09-28 grants TNCasino a revocable, non-exclusive, non-transferable
and non-sublicensable licence to use its data "solely for internal testing, educational, and
not-for-profit purposes in connection with the TNCasino platform". Its conditions:

1. No commercial use: no monetized subscription, advertising, paid API or other revenue.
2. No redistribution of "the raw datasets, data dumps, schema structures, or unprocessed API
   outputs to any third party". "Any public-facing presentation or visualization derived from the
   data must be aggregated or transformed, ensuring the underlying raw data cannot be
   reverse-engineered or extracted."
3. Attribution "where applicable and appropriate in non-commercial outputs": the line
   "Data provided courtesy of RotoBaller."
4. RotoBaller may terminate or modify the authorization at any time.

What that means here: the pipeline stores RotoBaller rows in `projections` next to the other
sources and blends them into each player's mean and variance, which is internal use; the site
shows blended numbers, odds and aggregated charts, none of which lets a reader recover a RotoBaller
row. No test fixture, log line, chart or page may present RotoBaller's rows as such. The
committed fixtures are the week-3 articles trimmed to their table, which the tests need to prove
the parser; they are not redistributed anywhere and the repository is the owner's private
portfolio, which the orchestrator has judged within "internal testing". Do not add further
fixtures beyond what section 5 lists.

### What RotoBaller publishes

`robots.txt` disallows only `/wp-admin/`, `/tag/` and `/api/rbapps` and names four sitemaps,
among them `https://www.rotoballer.com/google-news-sitemap.xml`. One article a week carries the
projections as a single server-rendered table, one row per player, and a Sunday update adds K
and D/ST:

| week | article path | published (ET) | note |
|---|---|---|---|
| 2 | `/fantasy-football-projections-for-week-2-rb-wr-qb-te-2026/1932317` | Thu 17 Sep 08:40 | |
| 3 | `/fantasy-football-projections-for-week-3-rb-wr-qb-te-2026/1948517` | Wed 23 Sep 15:30 | 300 rows: QB 32, RB 76, WR 123, TE 69 |
| 3 update | `/updated-fantasy-football-projections-for-week-3-rb-wr-te-qb-d-st-k-2026/1952030` | Sun 27 Sep 10:41 | 400 rows, adds K 32 and DST 32 |

The article id in the path is not predictable, so the week's article is found through the news
sitemap: on 2026-09-27 it listed 491 entries covering the previous 48 hours (317 KB), each with
`<loc>`, `<news:publication_date>` and `<news:title>`. A Wednesday-afternoon article is in it for
our Wednesday-evening run; a Thursday-morning one, as in week 2, is not, and then RotoBaller is
simply dropped for that week (the scrape step needs three usable sources, not seven). The landing
page `/fantasy-football-projections` does not link the weekly articles, so it is no use.

The table's header row on the Wednesday article is

    Player Name, Team, Pos, Fan Points, Comp, Pass Yards, PASS TDs, INTs, Rush, Ru. Yards,
    Ru. TDs, Rec, Rec. Yards, Rec. TDs

and the Sunday update has the same columns without `Comp`, so columns are read by header name.
Each player cell is `<a class="rbPlayer nfl" data-id="{id}" href="/nfl/player/{id}/Josh+Allen">`,
which gives an `external_id`. Teams are already Sleeper codes (JAX, LAR, LV, WAS). Empty cells
mean zero. There are no injury tags. `Fan Points` is half PPR (the page's `<h1>` says so:
"Week 3 Fantasy Football Projections (Half PPR): RB, WR, TE, QB"), so points are rescored from
the stat columns with league scoring; the rescore less half a point per reception lands within
0.6 of `Fan Points` on every row of both week-3 articles, which is the parser's built-in check.
Kickers and defenses carry `Fan Points` only and come after Thursday's lock in any case, so the
source is QB, RB, WR and TE and skips every other `Pos` label (K, PK, DST, D/ST, DEF).

Week 3 against Sleeper on 2026-09-27, Wednesday article, every check ok:

| position | pairs with Sleeper | r | median absolute difference |
|---|---|---|---|
| QB | 31 | 0.94 | 0.78 |
| RB | 68 | 0.98 | 0.68 |
| WR | 114 | 0.97 | 0.81 |
| TE | 67 | 0.97 | 0.67 |

Names to know: "J. Michael Sturdivant" (GB, WR) splits as first "J." and last
"Michael Sturdivant" through `split_full_name`; "Bam Knight" and "Kenneth Gainwell" are the match
step's business; Travis Hunter is listed WR; the fullbacks Kyle Juszczyk, Hunter Luepke and
Alec Ingold are listed RB, which the match step covers through Sleeper's `fantasy_positions`;
"Lil'Jordan Humphrey" and "De'Von Achane" carry apostrophes; "Amon-Ra St. Brown" a hyphen and a
two-word last name; "Patrick Mahomes II", "Kenneth Walker III" and "Brian Thomas Jr." suffixes.

Three fixtures are committed with this brief under `tests/pipeline/fixtures/rotoballer/`:

| file | what it is |
|---|---|
| `projections_2026_w3.html` | the Wednesday article: `<title>`, canonical link, description, the NewsArticle JSON-LD, the `<h1>` and the table (83 KB, 300 rows) |
| `updated_projections_2026_w3.html` | the Sunday update, same trim (102 KB, 400 rows, no `Comp`, with K and DST rows) |
| `google_news_sitemap_2026-09-27.xml` | the news sitemap cut to its first three entries plus the one projections article it listed (the Sunday update) |

Everything else on the pages (scripts, styles, navigation, comments, adverts) was stripped.

## 5. Ownership

- `pipeline/sources/rotoballer.py` (new).
- `pipeline/sources/__init__.py`: append `"rotoballer"` to `SOURCE_NAMES`. The scrape and
  playoffs tests derive their expectations from that list; a seventh name breaks none of them.
- `tests/pipeline/test_sources_html.py`: a `# RotoBaller` section, and rotoballer added to
  `SOURCES` and `SOURCE_IDS` under `# Every source`. The repository keeps its HTML source tests in
  this one file; follow it. Fetch tests use the `serve` and `clock` fixtures from
  `tests/pipeline/conftest.py`.
- `tests/pipeline/fixtures/rotoballer/`: the three committed files. Add nothing else there.
- `frontend/templates/about.html`: one new paragraph directly under the "I pull player
  projections from five different sources" paragraph, reading exactly
  `Data provided courtesy of RotoBaller.` in the page's existing paragraph style. Change nothing
  else on the page; the "five" is the owner's copy and the orchestrator will raise it with the owner.
- `docs/design/pipeline-v2.md`: the 7.2 row at the end of this brief.
- `docs/architecture/08-constraints-and-debt.md`, the "Add more projection sources" item: a
  RotoBaller line saying it reads under the owner's letter of 2026-09-28 (non-commercial, no raw
  redistribution, attribution on the about page, revocable), found through the news sitemap, so a
  week posted after the Wednesday run is dropped for that week.

Nothing else: not `stats.py` or the model parameters, not the scrape or playoffs steps, not
`verify.py`, not `names.py`, not the other sources, no new route, page or chart.

## 6. Tasks

1. **Reachability.** One `get` of `https://www.rotoballer.com/google-news-sitemap.xml` from the
   sandbox. Record the status, size and time (UTC), and whether a projections article for the
   current week is listed. A 403 or a challenge page means "build from fixtures" for the rest of
   this brief.
2. **Module.** `RotoBallerSource` with `name = "rotoballer"`, `website = "rotoballer.com"`,
   `supports_future_weeks = False`, `positions` QB, RB, WR, TE, and two pure functions,
   `article_url(sitemap_xml, season, week)` and `parse(html, season, week)`.
   - `fetch` makes two requests a week through `get` at the default spacing: the news sitemap,
     then the article `article_url` picked. Nothing else, ever: not `/feed`, not the landing page,
     not player pages.
   - `article_url` takes the entries whose `<loc>` path matches
     `fantasy-football-projections-for-week-{week}-` and `-{season}/` (the plain article and the
     "updated-" one both do) and returns the one with the latest `<news:publication_date>`. None
     raises, with a message such as `RotoBaller has not posted week 4: no projections article in
     the news sitemap`, so the scrape step records a fetch failure rather than storing nothing.
   - `parse` reads the page's single table by header name. It requires `Player Name`, `Team`,
     `Pos`, `Fan Points` and the eight stat columns `Pass Yards`, `PASS TDs`, `INTs`, `Rush`,
     `Ru. Yards`, `Ru. TDs`, `Rec`, `Rec. Yards`, `Rec. TDs`; `Comp` is ignored whether present
     or not. A missing table or a missing required column raises with a message naming the page
     and the column (a layout change must fail loudly, never parse as zeros).
   - Week from the `<h1>` ("Week 3 Fantasy Football Projections", pattern `Week (\d+)`), stamped
     on every row so `week_stamp` compares the page's week with the requested one.
   - Rows whose `Pos` is not QB, RB, WR or TE are skipped. `external_id` from the player link's
     `data-id`; names through `split_full_name`; teams through `normalize_team`.
   - Points by rescoring the stat columns with league scoring, empty cells as zero:
     `0.04 * pass_yd + 4 * pass_td - 2 * pass_int + 0.1 * rush_yd + 6 * rush_td + rec + 0.1 * rec_yd + 6 * rec_td`,
     rounded to two decimals. Skip rows at or below 0, as the other sources do. Fumbles and
     two-point conversions are not on the page; the research accepted that.
3. **Tests, fixtures only.** Position counts on both fixtures (Wednesday QB 32, RB 76, WR 123,
   TE 69; Sunday QB 32, RB 89, WR 139, TE 76 and no K or DEF rows); on every row of both
   fixtures the rescore less half a point per reception is within 0.6 of `Fan Points`; Josh
   Allen's Wednesday row rescores from his passing and rushing line; the Sunday layout without
   `Comp` parses the same as the Wednesday one; the week comes from the page, not the argument;
   a page without a table raises; a header missing `Rec. TDs` raises; `article_url` picks the
   Sunday update from the sitemap fixture for week 3 and raises for week 4; the suffix, apostrophe,
   hyphen and "J. Michael Sturdivant" cases above come through as expected; `external_id` is
   Josh Allen's `data-id`; a fetch test with `serve` and `clock` shows two requests in order,
   sitemap then article, with the pipeline's User-Agent; the shared `test_parse_is_pure` and
   `test_rows_are_canonical` cover rotoballer.
4. **Registry, docs and the about page** as listed under Ownership.
5. **Live runs** (only if task 1 found the site reachable).
   `python -m pipeline run --week N --steps league,scrape --sources sleeper,rotoballer` for the
   week RotoBaller currently lists. Wednesday 2026-09-30 is the day week 4 should appear: if you
   are working then, fetch the sitemap in the morning and again in the evening (ET) and record
   when the week-4 article appeared. Spot-check three named players per position, the page's stat
   line and `Fan Points` next to the parser's row. Say how the match step fares on
   J. Michael Sturdivant, Travis Hunter and the three fullbacks (`projections_with_sleeper` in
   your scratch data dir shows which rows found a player). Record
   `python -m pipeline review --week N --source rotoballer.com --verdict ok|reject --note "..."`
   in your scratch data dir.

## 7. Constraints

- The letter's conditions are code rules. Nothing in this package publishes, displays, logs at
  info level or charts a RotoBaller row as such; the admin pipeline page's per-source verdicts
  and counts are fine, per-player rows are not. The attribution line goes on the about page in
  this package so it is live the day the data is. If you find an existing page or route that would
  show per-source rows, do not change it: name it in the pull request.
- No login, no account, the truthful User-Agent, requests through `get` and spaced. Nothing
  fetched but the sitemap and the one article.
- Never loosen a verification threshold to make RotoBaller pass. If a check fails on the live
  week, report the numbers and leave the threshold alone.
- Keep the module in the `ProjectionSource` shape (`fetch(season, week)` returning `Projection`
  rows, pure `parse` and `article_url`); the scrape step and the playoffs step depend on it.
- Tests pass without the network: fixtures only.
- `python -m pytest -q`, `ruff check .` and `ruff format --check .` clean before the pull request.
  Commit per step, not one commit for everything.

## 8. The pull request

Its description carries, in this order: the reachability result (status, size, time UTC, whether
the current week's article was listed); the check table for each live week (every check with its
numbers, or "not run: sandbox cannot reach rotoballer.com"); the spot check, three named players
per position with the page's numbers next to the parser's; the match-step answer for the named
players; the verdict recorded; the time week 4 appeared, if seen; files changed, tests added and
the pytest total; the lint results; anything that could not be verified from the sandbox; and
anything the orchestrator has to decide.

## 9. Row for design 7.2

| key | website | transport | future weeks | notes |
|---|---|---|---|---|
| rotoballer | rotoballer.com | HTTP: the news sitemap `https://www.rotoballer.com/google-news-sitemap.xml`, then the week's article it lists (path `fantasy-football-projections-for-week-{week}-...-{season}/{id}`, latest by publication date) | no | Read under RotoBaller's letter of 2026-09-28: non-commercial, no raw redistribution, attribution on the about page, revocable. One table read by header name (`Comp` appears midweek only); `Fan Points` is half PPR, so points are rescored from the stat columns; week from the `<h1>`; `external_id` from the player link's `data-id`. QB, RB, WR, TE; K and D/ST come only in the Sunday update, after the lock, and are skipped. The sitemap covers about 48 hours, so a week posted after the Wednesday run (week 2 came Thursday morning) is dropped for that week. |
