# TNCasino WP8c: FFToday as a sixth projection source

Written 2026-09-29 by the orchestrator session, after WP8b (PR 14, merge 8b501cc) and the
FantasySharks quarterback change (2d032d6). For the cloud session, which writes its own engineer
briefs from this one, runs its engineers, pushes the branch `wp8c-fftoday` cut from `main` and
opens the pull request. The orchestrator merges.

## 1. Where you are

The pipeline (`pipeline/`) scrapes projections from five sources (Sleeper, ESPN, FantasySharks,
FirstDown, FanDuel), verifies each source's rows against Sleeper before they are used, simulates
the week and prices the markets. FantasyPros left in WP8b. Sleeper is RotoWire's numbers and the
other four are each a single shop, so FFToday is the first new independent set of stat lines since
FirstDown. The research that chose it is `docs/design/sources-2026-research.md`, section 4; this
brief supersedes the draft in its section 8.

- Branch `wp8c-fftoday`, cut from `main` at or after 45795a8. Tests on `main`: 1186 pass and one
  is skipped, about 50 s. Lint: `ruff check .` and `ruff format --check .`, both clean on `main`.
- There is no `.env` and tests never need one. Never set `DATABASE_URL`. Never run
  `python -m scripts.publish`, `python -m pipeline run ... --steps publish` or `migrate-legacy`.
  Production is not reachable and must not be.
- Manual runs need a data directory of their own. Make one under your scratch directory and point
  every command at it with `PIPELINE_DATA_DIR`, with `PIPELINE_SEASON=2026` and
  `PIPELINE_LEAGUE_ID=1387602586542018560`. `python -m pipeline run --week 4 --steps league` fills
  it from Sleeper's API; the verification checks need its `nfl_players`. Never commit a `.db` file.
- Live fetches are for your investigation and the report. Tests run on fixtures only, never on the
  network. WP8b found the sandbox reaches `api.sleeper.app` and ESPN but not `fantasysharks.com`
  (a Cloudflare challenge on its egress), so the first thing to learn is whether it reaches
  `www.fftoday.com`. If it does not, build from the committed fixtures, say so in the pull request,
  and the orchestrator runs the live checks from the owner's machine.
- Every request identifies itself as `USER_AGENT` from `pipeline/sources/base.py` and goes through
  its `get(url, spacing_s=...)`, which spaces requests to one host. No login, no account, no
  disguise, no scraping anything but the projection pages named here.

## 2. Read first

1. `CLAUDE.md`.
2. `docs/design/sources-2026-research.md` §4 in full (terms, fetch recipe, parse, rescoring, the
   week-3 comparison, posting time, fixtures). §1 and §3.1 for why FFToday and not the others.
3. `docs/design/pipeline-v2.md` §7 (the sources, the verification checks, the 7.2 table you add a
   row to).
4. `pipeline/sources/base.py`, `verify.py`, `teams.py`, `firstdown.py` (the module to copy the
   shape of: a `ProjectionSource` whose `fetch` calls a pure `parse`), `pipeline/names.py`
   (`split_full_name`), `pipeline/steps/scrape.py` (how a source is fetched, verified, kept or
   dropped), and `tests/pipeline/test_sources_html.py` with its fixtures under
   `tests/pipeline/fixtures/`.

## 3. Goal

FFToday's QB, RB, WR and TE projections enter the pipeline as the source `fftoday` and pass
verification against Sleeper on a live week. Acceptance:

- `python -m pipeline run --week N --steps league,scrape --sources sleeper,fftoday` completes on
  the week FFToday currently shows, with `fftoday.com` at `ok` (a `warn` needs its reason in the
  pull request). If the sandbox cannot reach the site, the orchestrator runs this line instead.
- The fixture tests pass without the network. `python -m pytest -q`, `python -m ruff check .` and
  `python -m ruff format --check .` are clean.
- No verification threshold changed. Nothing else in the pipeline changed.

## 4. Background

FFToday publishes one server-rendered table per position with the full stat line. It has no
robots.txt and no terms page. Its PPR preset (`LeagueID=107644`) matches the league's scoring
exactly for RB, WR and TE, which gives the parser a built-in check: rescoring the stat columns
must reproduce the page's own `FPts`. Week 3 against Sleeper on 2026-09-27, every check ok:

| position | pairs with Sleeper | r | median absolute difference |
|---|---|---|---|
| QB | 32 | 0.90 | 1.19 |
| RB | 49 | 0.93 | 1.21 |
| WR | 50 | 0.94 | 1.76 (FFToday higher) |
| TE | 42 | 0.93 | 1.13 |

It posts the next week on Wednesday, between about 02:30 and 19:35 ET by the archive captures
(section 4.7, a hypothesis your live fetches can confirm), has no future weeks, no DEF, and lists
kickers without field-goal distances, so K is left out. A week it has not posted yet answers
HTTP 200 with a 304-byte page reading "No Player Found!", which must raise so the scrape step
records a fetch failure rather than storing nothing.

Five fixtures are committed with this brief under `tests/pipeline/fixtures/fftoday/`:

| file | what it is |
|---|---|
| `projections_2026_w3_rb.html` | RB page 1, PPR, 50 rows, ends in the "Next Page" link |
| `projections_2026_w3_wr.html` | WR page 1, PPR |
| `projections_2026_w3_te.html` | TE, PPR, one page |
| `projections_2026_w3_qb_halfppr.html` | QB, the site's default Half-PPR page (no `LeagueID`); the rescore will not match its `FPts` |
| `projections_2026_w4_not_posted.html` | the "No Player Found!" page |

The PPR QB page and the RB and WR second pages were never captured. Add them from your first live
fetch, and replace the half-PPR QB fixture with the PPR one if you can; if the sandbox cannot reach
the site, keep the half-PPR page and test the QB parse for shape only.

## 5. Ownership

- `pipeline/sources/fftoday.py` (new).
- `pipeline/sources/__init__.py`: append `"fftoday"` to `SOURCE_NAMES`. The scrape and playoffs
  tests derive their expectations from that list; registering a sixth name breaks none of them
  (checked on `main` today).
- `tests/pipeline/test_sources_html.py`: an `# FFToday` section, and fftoday added to `SOURCES`
  and `SOURCE_IDS` under `# Every source`. The repository keeps its HTML source tests in this one
  file; follow it.
- `tests/pipeline/fixtures/fftoday/`: the five committed files plus the ones you capture.
- `docs/design/pipeline-v2.md`: the 7.2 row at the end of this brief, and the `position_counts`
  row in 7.3, whose ranges (QB 20–50, RB 40–130, WR 50–170, TE 20–90) no longer match
  `COUNT_RANGES` in `pipeline/sources/verify.py` (QB 20–80, RB 40–150, WR 50–200, TE 20–130).
- `docs/architecture/08-constraints-and-debt.md`, the "Add more projection sources" item: FFToday
  is added, with `LeagueID=107644` (the item says 1, which is Standard scoring); CBS is out on its
  terms of use (section 10, per the research's §3.2); FantasySharks' `scoring=1` in that item is
  the `scoring=2` the code sends; and FantasyPros is no longer the shape to copy. Rewrite the item
  rather than patching words into it.

Nothing else: not `stats.py` or the model parameters, not the scrape or playoffs steps, not
`verify.py`, not the other sources.

## 6. Tasks

1. **Reachability.** One `get` of the week-3 TE page (`Season=2026&GameWeek=3&PosID=40&LeagueID=107644`)
   from the sandbox. Record the status and size. A 403 or a challenge page means "build from
   fixtures" for the rest of this brief.
2. **Module.** `FFTodaySource` with `name = "fftoday"`, `website = "fftoday.com"`,
   `supports_future_weeks = False`, `positions` QB, RB, WR, TE, and a pure `parse(html, season, week)`.
   - URL: `https://www.fftoday.com/rankings/playerwkproj.php?Season={season}&GameWeek={week}&PosID={id}&LeagueID=107644`
     with `PosID` 10 QB, 20 RB, 30 WR, 40 TE. RB and WR have a second page: follow the page's own
     "Next Page" link once (it adds `&order_by=FFPts&sort_order=DESC&cur_page=1`) rather than
     assuming it exists. QB and TE fit on one page.
   - `get(url, spacing_s=5.0)`: six requests a week, five seconds apart.
   - Table: the row with class `tableclmhdr` holds the column names. The names Att, Yard and TD
     repeat, so compare the whole header row with the expected list for the position and read
     cells by index. Raise, with a message naming the page, when the header row is missing (the
     "No Player Found!" page) or differs from the expected one (a layout change).
   - Week from the `td.update` cell ("2026 Week 3", pattern `(\d{4}) Week (\d+)`), stamped on
     every row so `week_stamp` compares the page's week with the requested one.
   - `external_id` from the player link (`/stats/players/{id}/...`); names through
     `split_full_name`; teams through `normalize_team` (FFToday writes JAC for JAX).
   - Points by rescoring the stat columns with league scoring:
     `0.04 * pass_yd + 4 * pass_td - 2 * pass_int + 0.1 * rush_yd + 6 * rush_td + rec + 0.1 * rec_yd + 6 * rec_td`
     (research §4.4), rounded to two decimals. Skip rows at or below 0, as the other sources do.
     Fumbles are not on the page; the research accepted that.
3. **Tests, fixtures only.** Rescored points equal the page's `FPts` for every RB, WR and TE row;
   the not-posted page raises; a changed header raises; the week comes from the page, not the
   argument; a suffix (Brian Thomas Jr.), an apostrophe (De'Von Achane) and a hyphen
   (Jaxon Smith-Njigba) survive; JAC becomes JAX; the shared `test_parse_is_pure` and
   `test_rows_are_canonical` cover fftoday. A fetch test with a fake `get` shows the second page
   requested for RB and not for TE, at a spacing of 5 s.
4. **Registry and docs** as listed under Ownership.
5. **Live runs** (only if task 1 found the site reachable).
   `python -m pipeline run --week N --steps league,scrape --sources sleeper,fftoday` for the week
   FFToday currently shows. Wednesday 2026-09-30 is the day week 4 should appear: if you are
   working then, fetch the week-4 QB page in the morning and again in the evening (ET) and record
   when it appeared. Spot-check three named players per position, the page's stat line and `FPts`
   next to the parser's row. Record
   `python -m pipeline review --week N --source fftoday.com --verdict ok|reject --note "..."` in
   your scratch data dir.
6. **One extra request.** `Season=2025&GameWeek=10&PosID=20&LeagueID=107644`: report whether 2025
   pages are still served and how many rows. The 2025 backfill and the model refit that follow are
   a separate package; do not start them.

## 7. Constraints

- No login, no account, the truthful User-Agent, requests through `get` and spaced. Nothing
  fetched but the pages named here.
- Never loosen a verification threshold to make FFToday pass. If a check fails on the live week,
  report the numbers and leave the threshold alone.
- Keep the module in the `ProjectionSource` shape (`fetch(season, week)` returning `Projection`
  rows, a pure `parse`); the scrape step and the playoffs step depend on it.
- Tests pass without the network: fixtures only.
- `python -m pytest -q`, `ruff check .` and `ruff format --check .` clean before the pull request.
  Commit per step, not one commit for everything.

## 8. The pull request

Its description carries, in this order: the reachability result (status, size, time UTC); the
check table for each live week (every check with its numbers, or "not run: sandbox cannot reach
fftoday.com"); the spot check, three named players per position with the page's numbers next to
the parser's; the verdict recorded; the time week 4 appeared, if seen; the 2025 answer; files
changed, tests added and the pytest total; the lint results; anything that could not be verified
from the sandbox; and anything the orchestrator has to decide.

## 9. Row for design 7.2

| key | website | transport | future weeks | notes |
|---|---|---|---|---|
| fftoday | fftoday.com | HTTP HTML `https://www.fftoday.com/rankings/playerwkproj.php?Season={season}&GameWeek={week}&PosID={id}&LeagueID=107644`; RB and WR follow the page's "Next Page" link once (`&order_by=FFPts&sort_order=DESC&cur_page=1`) | no | PosID 10 QB, 20 RB, 30 WR, 40 TE; LeagueID 107644 is FFToday's PPR preset, which matches league scoring for RB, WR and TE. Points rescored from the stat columns; week from `td.update`; a week not yet posted returns a "No Player Found!" page, which raises. Posts on Wednesday. No K (no field-goal distances), no DEF (none published). |
