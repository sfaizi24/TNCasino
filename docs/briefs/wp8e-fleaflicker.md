# TNCasino WP8e: Fleaflicker as an eighth projection source

Written 2026-09-29 by the orchestrator session, after WP8d (PR 17, merge 90a8711). For the cloud
session, which writes its own engineer briefs from this one, runs its engineers, pushes the branch
`wp8e-fleaflicker` cut from `main` and opens the pull request. The orchestrator merges.

**Gate.** This package starts only when section 4's "The owner's league" says the league exists
and section 10 carries that league's scoring rules. Both were filled in on 2026-09-29, so the gate
is open. The league id itself stays out of the repository: the owner gives it to the cloud session
in its prompt, and a session without it builds from fixtures and leaves the live run to the
orchestrator (task 5).

## 1. Where you are

The pipeline (`pipeline/`) scrapes projections from seven sources (Sleeper, ESPN, FantasySharks,
FirstDown, FanDuel, FFToday, RotoBaller), verifies each source's rows against Sleeper before they
are used, simulates the week and prices the markets. Fleaflicker is the second source read under a
written permission and the first read through an API its operator documents and has permitted. Its
robots.txt disallows `/api/`, so on 2026-09-28 Fleaflicker sent the owner a "Limited API and Data
Usage Authorization" letter for TNCasino. The letter is not in the repository (the owner keeps it
outside git); its conditions are quoted in section 4 and turned into rules in section 7. The
research that chose Fleaflicker is `docs/design/sources-2026-research.md`, section 5.2; its open
questions were answered by the orchestrator's probes, reported in section 4.

- Branch `wp8e-fleaflicker`, cut from `main`. Tests on `main` at 29bd278: 1227 pass and one is
  skipped, about 50 s; check the count on the commit you cut from. Lint: `ruff check .` and
  `ruff format --check .`, both clean.
- There is no `.env` and tests never need one. Never set `DATABASE_URL`. Never run
  `python -m scripts.publish`, `python -m pipeline run ... --steps publish` or `migrate-legacy`.
  Production is not reachable and must not be.
- Manual runs need a data directory of their own. Make one under your scratch directory and point
  every command at it with `PIPELINE_DATA_DIR`, with `PIPELINE_SEASON=2026` and
  `PIPELINE_LEAGUE_ID=1387602586542018560`. `python -m pipeline run --week 4 --steps league` fills
  it from Sleeper's API; the verification checks need its `nfl_players`. Never commit a `.db` file.
- Live fetches are for your investigation and the report. Tests run on fixtures only, never on the
  network. The sandbox reached `api.sleeper.app`, ESPN, `www.fftoday.com` and
  `www.rotoballer.com` but not `fantasysharks.com`, so the first thing to learn is whether it
  reaches `www.fleaflicker.com`. If it does not, build from the fixtures you write, say so in the
  pull request, and the orchestrator runs the live checks from the owner's machine.
- Every request identifies itself as `USER_AGENT` from `pipeline/sources/base.py` and goes through
  its `get(url, headers=..., spacing_s=...)`, which spaces requests to one host. No login, no
  account, no disguise, no request to anything but the two operations named here.
- **The repository is public.** Nothing Fleaflicker serves may be committed, pasted into a commit
  message, a pull request, a brief or a doc: not a payload, not a player's projected points, not a
  row. Fixtures are written by hand (section 6, task 3). Reports carry aggregates only: counts,
  r, MAD, medians, the verification table. WP8d committed real RotoBaller pages as fixtures and had
  to replace them; do not repeat that.

## 2. Read first

1. `CLAUDE.md`.
2. `docs/design/sources-2026-research.md` §5.2 (short: robots, the API documentation page, the
   verdict "ask, then probe"). Section 4 of this brief is what the probe found.
3. `docs/design/pipeline-v2.md` §4 (settings and the environment variables the pipeline reads),
   §7 (the sources, the verification checks, the 7.2 table you add a row to).
4. `pipeline/sources/base.py`, `verify.py`, `teams.py` (`normalize_team`, `ALIASES`,
   `DEF_NAMES`), `espn.py` (the module to copy the shape of: a JSON source whose `fetch` calls a
   pure `parse`, DEF rows named from `DEF_NAMES[team]`, `external_id` from the site's player id),
   `sleeper.py`, `pipeline/steps/scrape.py` (how a source is fetched, verified, kept or dropped;
   a `fetch` that raises fails the source, not the step), `pipeline/steps/match.py`'s docstring
   (how a row finds its Sleeper player), and `tests/pipeline/test_sources_api.py` (the JSON
   sources' tests) with `tests/pipeline/conftest.py` (the `clock` and `serve` fakes every fetch
   test uses). The ESPN and Sleeper fixtures under `tests/pipeline/fixtures/` are real captures
   from sites without a letter; Fleaflicker's cannot be.

## 3. Goal

Fleaflicker's QB, RB, WR, TE, K and D/ST projections for the week in play enter the pipeline as
the source `fleaflicker`, read from the owner's league in TNCasino's own scoring, and pass
verification against Sleeper on a live week, within the letter's conditions. Acceptance:

- `FLEAFLICKER_LEAGUE_ID=<id> python -m pipeline run --week N --steps league,scrape --sources sleeper,fleaflicker`
  completes on the week Fleaflicker currently projects, with `fleaflicker.com` at `ok`
  (`value_agreement` only warns on K and DEF; a `warn` needs its numbers in the pull request). If
  the sandbox cannot reach the site, the orchestrator runs this line.
- The fixture tests pass without the network. `python -m pytest -q`, `python -m ruff check .` and
  `python -m ruff format --check .` are clean.
- No verification threshold changed. Nothing else in the pipeline changed. Nothing new shows a
  Fleaflicker row to anyone.

## 4. Background

### The permission

Fleaflicker's letter of 2026-09-28 grants TNCasino, in its own words:

1. "a limited, non-exclusive, non-transferable, revocable license to access Fleaflicker developer
   APIs and ingest raw statistical, roster, league, and performance data. This data may be used
   internally by Licensee to power non-commercial features, calculations, analytics, and platform
   functionality for TNCasino."
2. "Under no circumstances shall Licensee sell, lease, sublicense, distribute, publish, or make
   publicly available any raw, unprocessed data feeds, API response payloads, database exports, or
   raw JSON/XML outputs obtained from Fleaflicker. Any data presented to end users on TNCasino must
   be processed, aggregated, or transformed. Providing third-party programmatic access or bulk data
   downloads is strictly prohibited."
3. "Access is conditional upon adherence to Fleaflicker technical guidelines and reasonable rate
   limits. Licensee agrees not to circumvent API throttling, conduct denial-of-service operations,
   or extract data outside the agreed endpoints. Fleaflicker reserves the right to rate-limit or
   revoke API keys in the event of platform disruption."
4. Fleaflicker keeps all rights in the data; the letter gives no ownership.
5. Fleaflicker may modify or terminate the permission at any time on written notice or on a
   violation.

The letter asks for no attribution.

What that means here:

- The projections feed the blend and the simulations; nothing shows a Fleaflicker projection as
  such. The admin pipeline page's per-source verdicts and counts are aggregates and fine;
  per-player rows are not. The site stays non-commercial.
- Nothing raw leaves the pipeline's local databases. The repository is public, so a committed
  payload or a projected point in a fixture, a test value, a commit message, a pull request or a doc
  would be publication. Fixtures are hand-written in the documented shape with made-up numbers.
- Two documented operations only, `FetchPlayerListing` and `FetchLeagueRules`, on the owner's own
  league. No `Login`, no `FetchUserLeagues` (it takes an email address; the owner's never goes to
  Fleaflicker), no other operation, no page. Requests go one at a time through `get` at the default
  2 s spacing (robots.txt asks for a 1 s crawl delay; Fleaflicker's forum asks crawlers for one
  page at a time). A 429 or a 5xx fails the source for the week; nothing retries in a loop, nothing
  works around a limit.
- The permission is revocable. If the owner reports that it was withdrawn, the source leaves
  `SOURCE_NAMES` and its rows are deleted; that is a one-line change and a SQL statement, and this
  package leaves nothing that would make it harder.

### What the API serves

Facts from the orchestrator's read-only probes of 2026-09-30 01:07 to 01:10 UTC (seven requests
from the owner's machine, 3 s apart, with the pipeline's User-Agent and `Accept: application/json`,
all 200 except the first, which deliberately omitted `league_id` and got 404 `Not found`). The
probes used a public league that the ffscrapr package's documentation uses as its example, because
no league of the owner's existed yet; that league plays IDP with custom scoring, so its projected
points are not TNCasino's and nothing from those payloads is in the repository. Probe only the
owner's league.

- **Endpoint.** `https://www.fleaflicker.com/api/{Operation}` with query parameters, answering
  JSON. The documentation (`https://www.fleaflicker.com/api-docs/index.html`, not disallowed)
  shows snake_case fields; the JSON is camelCase (`proPlayer`, `pointsProjected`), and because it is
  protobuf-JSON a field at its default value is omitted (`isNow` appears only when true;
  `externalIds[].type` is absent because SPORTRADAR is the default). 64-bit integers arrive as
  strings (`startEpochMilli`), 32-bit ones as numbers (`id`, `ordinal`).
- **Projections need a league.** `FetchPlayerListing` without `league_id` is a 404. With
  `sport=NFL&league_id={id}&sort=SORT_PROJECTIONS&sort_season={season}&sort_period={week}&filter.position.eligibility={label}&result_offset={offset}`
  it answers `{players, resultTotal, resultOffsetNext, eligibleFilterPositions, eligibleSorts,
  sortRanges, ...}` with 30 players a page, sorted by projected points, descending.
  `resultOffsetNext` names the next page's offset and is absent on the last page.
  `filter.position.eligibility` takes one label per request; the labels a league accepts are in
  `eligibleFilterPositions[].label` (the probe league's were ALL, QB, RB, WR, TE, RB/WR/TE, K and
  its defensive positions; D/ST is expected only in a league that starts one, see below).
- **Points come scored in the league's own rules, and only those.** Each player is
  `{proPlayer, requestedGames, requestedGamesPeriod, viewingProjectedPoints, viewingProjectedStats,
  displayGroup, transactionStatus, owner?, injury?, ...}`. The week's projection is
  `requestedGames[0].pointsProjected.value` (a float; `formatted` is its string). `statsProjected`
  is a four-column display summary per position group (a running back's shows rushing yards,
  touchdowns, receiving yards and the share of targets caught), not a stat line, so points cannot be
  rescored from it. That is why the owner's league must carry TNCasino's scoring (next subsection)
  and why the module checks the rules before it reads a number.
- **Week.** `requestedGamesPeriod.ordinal` on every player (also `requestedGames[0].period.ordinal`)
  is the week the numbers are for; `isNow` is present when it is the week in play. Rows stamp it as
  their `week`, so `week_stamp` compares it with the requested week.
- **No future weeks.** Asked for `sort_period` one week ahead, the listing returns every player at
  the position with no `pointsProjected` at all. `supports_future_weeks = False`. A position whose
  first page carries no `pointsProjected` on any row means the week asked for is not the week in
  play; raise rather than store nothing.
- **Players.** `proPlayer.{id, nameFirst, nameLast, nameFull, nameShort, position,
  proTeamAbbreviation, proTeam {abbreviation, location, name}, nflByeWeek, injury?, news?,
  externalIds [{id}], positionEligibility, percentOwnedRatio, headshotUrl}`. Names are already
  split, so use `nameFirst` and `nameLast` as given, not `split_full_name`. `position` is a single
  label (QB, RB, WR, TE, K, D/ST in an offense-plus-defense league). Team codes seen: the 32
  current codes with `JAC` for Jacksonville, which `normalize_team` maps to `JAX`; a player without
  `proTeamAbbreviation` is a free agent (none appeared on the probed pages, a hypothesis to keep
  handled: no team, as ESPN's `proTeamId` 0). `externalIds[0].id` is the player's Sportradar id;
  note it in the pull request as a possible match-step improvement, do not request
  `external_id_type` and do not use it in this package.
- **Sizes** for the probe league, week 4: `resultTotal` QB 76, RB 122, K 34, each inside
  `verify.COUNT_RANGES`; WR and TE were not probed. Page 1 ran from about 28 to 13 projected points
  for QB, 23 to 9 for RB, 11 to 6 for K, with no zero rows, so a listing's tail is where zero rows
  would sit.
- **D/ST.** The probe league starts no D/ST, its labels stop at K, and the `D/ST` filter fell back
  to `ALL`: 1300 players of every position. So the owner's league must start a D/ST, and the module
  must refuse a page whose rows carry another position than the one asked for (one request catches
  the fallback). A defense row has `position` `D/ST`, its team in `proTeamAbbreviation`, and takes
  Sleeper's form through `DEF_NAMES[team]`, as ESPN's does. The label `D/ST` is the hypothesis;
  task 1 confirms it on the owner's league.
- **Rules.** `FetchLeagueRules?sport=NFL&league_id={id}` answers `{groups, rosterPositions,
  numStarters, numBench, maxRosterSize, maxActive, allScoringRuleTypes}`; `groups[]` (Passing,
  Rushing, Receiving, Misc, Kicking, Returning, Defense, Punting) each carry `scoringRules[]`, a rule
  being `{category {id, abbreviation, nameSingular, namePlural}, points {value, formatted},
  pointsPer? {value, formatted}, forEvery?, boundLower?, boundUpper?, rangeType?, isBonus?, applyTo
  [labels], applyToAll, description, template}`. `description` is a rendered sentence such as "1
  point for every 25 Passing Yards (0.04 per)"; bracket rules carry `boundLower` or `boundUpper`
  with `rangeType`. Category ids look global rather than per league (passing yards 3, passing
  TDs 5, interceptions 7, rushing yards 22, rushing TDs 24, receptions 41, receiving yards 42,
  receiving TDs 44, fumbles lost 27, field goals made 101, extra points 104, and so on); section 10
  settles them on the owner's league.
- **Rate.** About 24 requests a week: one for the rules and, at 30 a page, QB 3, RB 5, WR 7, TE 4,
  K 2, D/ST 2 pages. At 2 s spacing that is under a minute.

### The owner's league

The pipeline reads the league named by the environment variable `FLEAFLICKER_LEAGUE_ID` (the
owner's local `.env` and the droplet's `/opt/tncasino/.env`; never a value in the repository). The
owner creates it by hand on Fleaflicker: a free 2026 NFL league, private, that starts QB, RB, WR,
TE, K and D/ST (Sleeper's roster is QB, RB, RB, WR, WR, TE, FLEX, K, DEF and five bench spots; the
counts do not matter, the D/ST slot does) with scoring set to TNCasino's Sleeper league:

| Group | Setting | Points |
|---|---|---|
| Passing | yard | 0.04 |
| Passing | TD | 4 |
| Passing | interception | -2 |
| Passing | two-point conversion | 2 |
| Rushing | yard | 0.1 |
| Rushing | TD | 6 |
| Rushing | two-point conversion | 2 |
| Receiving | reception | 1 |
| Receiving | yard | 0.1 |
| Receiving | TD | 6 |
| Receiving | two-point conversion | 2 |
| Misc | fumble lost | -2 |
| Misc | own fumble recovered | 2 |
| Misc | fumble recovery TD | 6 |
| Kicking | field goal 0 to 39 yards | 3 |
| Kicking | field goal 40 to 49 yards | 4 |
| Kicking | field goal 50 yards and longer | 5 |
| Kicking | field goal missed | -1 |
| Kicking | extra point | 1 |
| Kicking | extra point missed | -1 |
| Defense | sack | 1 |
| Defense | interception | 2 |
| Defense | fumble forced | 1 |
| Defense | fumble recovered | 1 |
| Defense | safety | 2 |
| Defense | defensive or special-teams TD | 6 |
| Defense | blocked kick | 2 |
| Defense | points allowed 0 | 5 |
| Defense | points allowed 1 to 6 | 4 |
| Defense | points allowed 7 to 13 | 3 |
| Defense | points allowed 14 to 20 | 1 |
| Defense | points allowed 21 to 27 | 0 |
| Defense | points allowed 28 to 34 | -1 |
| Defense | points allowed 35 and more | -4 |
| Defense | yards allowed under 100 | 5 |
| Defense | yards allowed 100 to 199 | 3 |
| Defense | yards allowed 200 to 299 | 2 |
| Defense | yards allowed 300 to 349 | 0 |
| Defense | yards allowed 350 to 399 | -1 |
| Defense | yards allowed 400 to 449 | -3 |
| Defense | yards allowed 450 to 499 | -5 |
| Defense | yards allowed 500 to 549 | -6 |
| Defense | yards allowed 550 and more | -7 |

Fleaflicker expresses field-goal distance as extra points on top of a base, so 3 per field goal
plus 1 extra at 40 to 49 and 2 extra at 50 and longer. Where Fleaflicker offers no equivalent for a
line, the owner leaves it out and tells the orchestrator; if a D/ST line cannot be expressed, D/ST
leaves this package's positions (QB to K) and section 10 says so.

Once the league exists the orchestrator runs `FetchLeagueRules` on it once, confirms with one
listing page that its rows carry `pointsProjected`, and fills in:

| | |
|---|---|
| League | created by the owner on 2026-09-29; the id is in the owner's `.env`, not here |
| D/ST filter label | `D/ST` (confirmed on 2026-09-29: 32 defenses, every row projected) |
| Rules | section 10 |

## 5. Ownership

- `pipeline/sources/fleaflicker.py` (new), and if you keep the pinned rules out of the module,
  `pipeline/sources/fleaflicker_rules.json` next to it.
- `pipeline/sources/__init__.py`: append `"fleaflicker"` to `SOURCE_NAMES`. The scrape and
  playoffs tests derive their expectations from that list; an eighth name breaks none of them.
- `tests/pipeline/test_sources_api.py`: a `# Fleaflicker` section next to Sleeper's and ESPN's,
  including the registration, purity and canonical-row assertions the HTML sources get from the
  `# Every source` block in `test_sources_html.py`. Fetch tests use the `serve` and `clock` fixtures
  from `tests/pipeline/conftest.py`; the league id is set with `monkeypatch.setenv`.
- `tests/pipeline/fixtures/fleaflicker/`: hand-written JSON only (task 3).
- `docs/design/pipeline-v2.md`: the 7.2 row at the end of this brief, and one sentence in §4 after
  the discovery order saying the Fleaflicker source reads `FLEAFLICKER_LEAGUE_ID` itself at fetch
  time, since sources get no settings.
- `docs/architecture/07-deployment-and-ops.md`, the environment-variable table: a row for
  `FLEAFLICKER_LEAGUE_ID` (pipeline; the owner's Fleaflicker league; required for the source, and
  without it the source fails and a full run goes on with the others).
- `README.md`, the `.env` example under Configuration: `FLEAFLICKER_LEAGUE_ID=your_league_id`
  in the pipeline block.
- `docs/architecture/08-constraints-and-debt.md`, the "Add more projection sources" item: a
  Fleaflicker line saying it reads the owner's own league through the documented API under the
  owner's letter of 2026-09-28 (non-commercial, no raw or per-player exposure, documented endpoints
  and rate limits, revocable), that the league's scoring is checked against the pinned rules on
  every run, that the source serves no future weeks, and that its fixtures are synthetic because the
  repository is public.

Nothing else: not `stats.py` or the model parameters, not the scrape or playoffs steps, not
`verify.py`, not `names.py`, not `settings.py`, not the other sources, no new route, page or
chart, nothing on the about page (no attribution was asked for).

## 6. Tasks

1. **Reachability.** From the sandbox, one `FetchLeagueRules` for the owner's league; if it
   answers, one QB listing page for the week in play and one page with the D/ST label from
   section 4. Record status, size and time (UTC) for each, whether the QB rows carry
   `pointsProjected` and which `ordinal` they stamp, and whether the D/ST page's rows are all D/ST.
   Three requests, then stop. A 403, a challenge page or a timeout means "build from fixtures" for
   the rest of this brief.
2. **Module.** `FleaflickerSource` with `name = "fleaflicker"`, `website = "fleaflicker.com"`,
   `supports_future_weeks = False`, `positions` QB, RB, WR, TE, K, DEF (or QB to K if section 10
   says so), and pure functions `parse(pages, season, week)` and `check_rules(payload)`.
   - `fetch` reads `FLEAFLICKER_LEAGUE_ID` from the environment when called (the runner has
     loaded `.env` by then). Unset or blank raises `RuntimeError` naming the variable and saying
     the source reads the owner's league, so the scrape step records a fetch failure.
   - Requests go through `get` with `headers={"Accept": "application/json"}` at the default
     spacing, one at a time: the rules first, then per position the listing pages in order.
     `sport=NFL` on both operations. Nothing else, ever.
   - `check_rules` reduces every rule in `groups[].scoringRules[]` to the fields that decide
     points (category id, `points.value`, `forEvery`, `boundLower`, `boundUpper`, `isBonus`,
     and `applyTo` unless `applyToAll`) and compares the set with the pinned rules from section 10.
     A difference raises with each differing rule's `description`, so a league whose scoring
     drifted (half a point per reception, six-point passing touchdowns, a first-down bonus) is
     dropped for the week instead of passing `value_agreement`, whose 4-point median tolerance
     would not notice.
   - Paging: start at offset 0, follow `resultOffsetNext` while it is present and the page's last
     row projects more than 0 (pages are sorted by projection, so the first zero ends the useful
     rows), and raise past ten pages for one position, which is more than any position needs and
     means the filter or the sort was ignored.
   - A page whose rows carry a `proPlayer.position` other than the one requested raises (the
     D/ST fallback). A position whose first page has no `pointsProjected` on any row raises:
     Fleaflicker projects only the week in play.
   - Rows: position from `proPlayer.position` through a `{"QB": "QB", ..., "K": "K", "D/ST":
     "DEF"}` map; `nameFirst` and `nameLast` as given, except a D/ST row, which takes
     `DEF_NAMES[team]`; team through `normalize_team(proTeamAbbreviation)`, a missing code being no
     team; points `round(requestedGames[0].pointsProjected.value, 2)`, skipping a row without
     `requestedGames` or `pointsProjected` and a row at or below 0; `week` from
     `requestedGamesPeriod.ordinal`; `external_id = str(proPlayer.id)`. A player already seen
     under another position listing (dual eligibility) is kept once, by `proPlayer.id`.
3. **Tests, fixtures only.** Write the fixtures by hand in the shape section 4 documents: real
   player names, teams and positions are public facts and fine; ids, points, `resultTotal`,
   `startEpochMilli`, injury notes and Sportradar ids are made up, and no fixture is a captured
   payload or a copy of one. Keep them small: a QB listing of two pages (five rows and
   `resultOffsetNext`, then two rows and none), one page each for RB (including a free agent
   without a team and a bye-week player without `requestedGames`), WR, TE, K and D/ST, a page for
   the next week whose rows carry no `pointsProjected`, a page whose rows are of mixed positions,
   and a rules payload hand-written in the response shape whose reduction equals section 10. Tests: rows per position across
   the fixtures; a QB row's name, team, points and `external_id`; the D/ST row in Sleeper's form;
   the free agent with no team; the bye-week row and a zero row skipped; `JAC` normalised to
   `JAX`; `week` from `requestedGamesPeriod`; the next-week page raises; the mixed page raises; a
   rules payload with one rule changed raises naming that rule's description, and one with a rule
   added raises; a fetch test with `serve` and `clock` showing the rules request first, then the
   listing pages in position order with offsets 0 and 30 for QB, every request with the pipeline's
   User-Agent and the `Accept` header, spaced 2 s apart; a fetch without `FLEAFLICKER_LEAGUE_ID`
   raises before any request; `parse` is pure (the payload is unchanged and two calls agree); every
   row is canonical (source, season, a position in the six, a team in `CANONICAL_TEAMS`, points
   above 0, an `external_id`); and `load_source("fleaflicker")` is registered with the website and
   `supports_future_weeks` False.
4. **Registry and docs** as listed under Ownership.
5. **Live runs** (only if task 1 found the site reachable).
   `FLEAFLICKER_LEAGUE_ID=<id> python -m pipeline run --week N --steps league,scrape --sources sleeper,fleaflicker`
   for the week in play. Spot-check three named players per position on your side, the page's
   `pointsProjected` next to the parser's row, and report the names and that they matched, not the
   numbers. Say how the match step fares on the D/ST rows and on the players with suffixes
   (`projections_with_sleeper` in your scratch data dir shows which rows found a player). Record
   `python -m pipeline review --week N --source fleaflicker.com --verdict ok|reject --note "..."`
   in your scratch data dir.
6. **Optional, one request.** The QB listing for `sort_period` one week ahead on the owner's
   league, to confirm future weeks carry no `pointsProjected` there too.

## 7. Constraints

- The letter's conditions are code rules. Nothing in this package publishes, displays, logs at
  info level or charts a Fleaflicker row as such; the admin pipeline page's per-source verdicts
  and counts are fine, per-player rows are not. If you find an existing page or route that would
  show per-source rows, do not change it: name it in the pull request.
- The repository is public: no payload, projected point or copied row in fixtures, tests,
  commits, docs or the pull request. Aggregates only. If a hand-written fixture ever needs a real
  number to make a test meaningful, it does not; make the number up.
- Two documented operations on the owner's league, `Accept: application/json`, the truthful
  User-Agent, requests through `get`, one at a time and spaced. No `Login`, no token, no
  `FetchUserLeagues`, no personal data in any request. A 429 or 5xx fails the week; nothing retries
  in a loop.
- Never loosen a verification threshold to make Fleaflicker pass. If a check fails on the live
  week, report the numbers and leave the threshold alone. Never loosen the rules check either: if
  the owner's league differs from section 10, say so and let the orchestrator settle which is right.
- Keep the module in the `ProjectionSource` shape (`fetch(season, week)` returning `Projection`
  rows, pure `parse` and `check_rules`); the scrape step and the playoffs step depend on it. The
  league id comes from the environment inside the module; `settings.py` and the sources' signature
  stay as they are.
- Tests pass without the network: fixtures only.
- `python -m pytest -q`, `ruff check .` and `ruff format --check .` clean before the pull request.
  Commit per step, not one commit for everything.

## 8. The pull request

Its description carries, in this order: the reachability result (status, size, time UTC for each
of the three requests, the `ordinal` the QB rows stamped, whether the D/ST page was all D/ST); the
check table for each live week (every check with its numbers, or "not run: sandbox cannot reach
fleaflicker.com"); the spot check as names and "matched", never the points; the match-step answer
for the D/ST rows and the suffix cases; the verdict recorded; the request count and elapsed time
of the live run; files changed, tests added and the pytest total; the lint results; the Sportradar
id note; anything that could not be verified from the sandbox; and anything the orchestrator has to
decide.

## 9. Row for design 7.2

| key | website | transport | future weeks | notes |
|---|---|---|---|---|
| fleaflicker | fleaflicker.com | HTTP JSON, the documented API on the owner's own league: `https://www.fleaflicker.com/api/FetchLeagueRules?sport=NFL&league_id={id}` once, then `https://www.fleaflicker.com/api/FetchPlayerListing?sport=NFL&league_id={id}&sort=SORT_PROJECTIONS&sort_season={season}&sort_period={week}&filter.position.eligibility={QB\|RB\|WR\|TE\|K\|D/ST}&result_offset={offset}` per position, 30 rows a page, following `resultOffsetNext` until the last row projects 0 | no | Read under Fleaflicker's letter of 2026-09-28: non-commercial, no raw or per-player exposure, documented endpoints and rate limits, revocable. League id from `FLEAFLICKER_LEAGUE_ID`. Points are `requestedGames[0].pointsProjected.value` in the league's own scoring (`statsProjected` is a display summary, so nothing is rescored), which is why the league's rules are compared with the pinned set every run and a difference drops the source for the week. Week from `requestedGamesPeriod.ordinal`; `external_id` from `proPlayer.id`; names from `nameFirst`/`nameLast`; D/ST rows take Sleeper's form from `DEF_NAMES`. Fixtures are synthetic: the repository is public. |

## 10. The owner's league's rules

Fetched by the orchestrator on 2026-09-29 from `FetchLeagueRules` on the owner's league, after the
owner set its scoring to section 4's table. This is the set the module pins: 42 rules, each
reduced to category id, points, for-every or bounds, the bonus flag and the positions it applies
to. A rule is a bonus when `isBonus` is set; its bounds are `boundLower` and `boundUpper`, both
inclusive, and a missing bound is open. A rule without `applyToAll` lists its positions in
`applyTo`.

| Group | Category | Id | Points | Rule | Applies to |
|---|---|---|---|---|---|
| Passing | Passing Yard | 3 | 1 | every 25 | QB, RB, WR, TE, K |
| Passing | Passing TD | 5 | 4 | each | QB, RB, WR, TE, K |
| Passing | 2 Pt Conversion Passing | 4 | 2 | each | QB, RB, WR, TE, K |
| Passing | Interception | 7 | -2 | each | QB, RB, WR, TE, K |
| Rushing | Rushing Yard | 22 | 1 | every 10 | QB, RB, WR, TE, K |
| Rushing | 2 Pt Conversion Rushing | 23 | 2 | each | QB, RB, WR, TE, K |
| Rushing | Rushing TD | 24 | 6 | each | QB, RB, WR, TE, K |
| Receiving | Catch | 41 | 1 | each | QB, RB, WR, TE, K |
| Receiving | Receiving Yard | 42 | 1 | every 10 | QB, RB, WR, TE, K |
| Receiving | 2 Pt Conversion Receiving | 43 | 2 | each | QB, RB, WR, TE, K |
| Receiving | Receiving TD | 44 | 6 | each | QB, RB, WR, TE, K |
| Misc | Fumble Lost | 27 | -2 | each | QB, RB, WR, TE, K |
| Misc | Offensive Fumble Recovery TD | 118 | 6 | each | QB, RB, WR, TE, K |
| Kicking | Field Goal Made | 101 | 3 | each | QB, RB, WR, TE, K |
| Kicking | Field Goal Made | 102 | 1 | 40 to 49, bonus | QB, RB, WR, TE, K |
| Kicking | Field Goal Made | 102 | 2 | 50 and more, bonus | QB, RB, WR, TE, K |
| Kicking | Field Goal Missed | 103 | -1 | each | QB, RB, WR, TE, K |
| Kicking | XP | 104 | 1 | each | QB, RB, WR, TE, K |
| Kicking | XP Missed | 105 | -1 | each | QB, RB, WR, TE, K |
| Returning | Kick Return TD | 63 | 6 | each | all |
| Returning | Punt Return TD | 67 | 6 | each | all |
| Defense | Interception | 84 | 2 | each | D/ST |
| Defense | Sack | 85 | 1 | each | D/ST |
| Defense | Fumble Forced | 86 | 1 | each | D/ST |
| Defense | Fumble Recovered | 87 | 2 | each | D/ST |
| Defense | Safety | 88 | 2 | each | all |
| Defense | Defensive TD | 89 | 6 | each | all |
| Defense | Blocked Kick | 117 | 2 | each | D/ST |
| Defense | Point Allowed | 94 | 5 | exactly 0, bonus | D/ST |
| Defense | Point Allowed | 94 | 4 | 1 to 6, bonus | D/ST |
| Defense | Point Allowed | 94 | 3 | 7 to 13, bonus | D/ST |
| Defense | Point Allowed | 94 | 1 | 14 to 20, bonus | D/ST |
| Defense | Point Allowed | 94 | -1 | 28 to 34, bonus | D/ST |
| Defense | Point Allowed | 94 | -4 | 35 and more, bonus | D/ST |
| Defense | Net Yard Allowed | 95 | 5 | up to 100, bonus | D/ST |
| Defense | Net Yard Allowed | 95 | 3 | 100 to 199, bonus | D/ST |
| Defense | Net Yard Allowed | 95 | 2 | 200 to 299, bonus | D/ST |
| Defense | Net Yard Allowed | 95 | -1 | 350 to 399, bonus | D/ST |
| Defense | Net Yard Allowed | 95 | -3 | 400 to 449, bonus | D/ST |
| Defense | Net Yard Allowed | 95 | -5 | 450 to 499, bonus | D/ST |
| Defense | Net Yard Allowed | 95 | -6 | 500 to 549, bonus | D/ST |
| Defense | Net Yard Allowed | 95 | -7 | 550 and more, bonus | D/ST |

Three lines differ from Sleeper's, accepted by the owner on 2026-09-29 because they move a
projection by well under a point: D/ST fumble recovered scores 2 rather than 1; there is no
own-fumble-recovery line for offensive players; and the "yards allowed under 100" bonus is bounded
at 100 rather than 99, so exactly 100 also takes the 100-to-199 bonus. Pin the league as it is,
not the Sleeper table.

The league's roster starts QB, RB, WR, WR, TE, RB/WR/TE, K and D/ST and offers the filter labels
`ALL`, `QB`, `RB`, `WR`, `TE`, `RB/WR/TE`, `K` and `D/ST`. On 2026-09-29 the `D/ST` filter returned
32 defenses, all with `pointsProjected` for week 4 (`ordinal` 4, `isNow` true), so the module's
positions are QB, RB, WR, TE, K and DEF.
