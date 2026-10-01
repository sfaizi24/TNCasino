---
name: run-pipeline
description: Run a week of the 2026 season with `python -m pipeline` on the owner's machine — Wednesday's full run, source verdicts and publish, then the Friday and Saturday reruns — and report what went up. Arguments are the week, optionally `friday` or `saturday`, optionally source names to rescrape.
disable-model-invocation: true
---

This runbook takes one week of the season from the first command on Wednesday to the publish, and then through the Friday and Saturday reruns. Everything runs on the owner's machine with `python -m pipeline`, from the repository root.

`$ARGUMENTS`:
- A number: the week, `N` below. Required.
- `friday` or `saturday`: run that day's rerun instead of Wednesday's run.
- Source names such as `firstdown,fftoday`: rescrape only those sources for a week that has already run (see "When a source failed").

Examples: `/run-pipeline 5`, `/run-pipeline 5 friday`, `/run-pipeline 5 firstdown,rotoballer`.

A full run takes well over an hour (80 minutes on 2026-09-30, 66 of them in the playoffs step). Start it as a background command and read its output when it exits.

## Before the first run

`.env` at the repository root on the owner's machine needs:
- `PIPELINE_LEAGUE_ID`, or the discovery pair `LEAGUE_ID` (an earlier season's league) and `SLEEPER_USERNAME`, from which the season's league is found. `PIPELINE_SEASON` is optional; without it the season comes from Sleeper.
- `FLEAFLICKER_LEAGUE_ID`: the owner's Fleaflicker league. Without it the Fleaflicker source fails.
- `DATABASE_URL`: production PostgreSQL. Only the publish step reads it, and it must be set even for a dry run.
- An SSH key for `root@143.198.183.213`: publish uploads the charts with `scp` and never waits at a password prompt.

Once per machine, for FanDuel's headless browser:

```bash
playwright install chromium
```

Sanity check: it resolves the season and league and lists every step's latest status for the week (all `-` before the first run).

```bash
python -m pipeline status --week N
```

## The sources

`run --sources` takes the name; `review --source` takes the website.

| name | website | what to know |
|---|---|---|
| `sleeper` | `sleeper.com` | The yardstick every other source is checked against. A full run cannot go on without it. |
| `espn` | `espn.com` | JSON API. |
| `fantasysharks` | `fantasysharks.com` | 60 seconds between requests (its robots.txt), so about five minutes. No QB. |
| `firstdown` | `firstdown.studio` | Posts the week midweek; until then it fails with "FirstDown has not posted week N". |
| `fanduel` | `fanduel.com` | Loads its pages in headless Chromium. |
| `fftoday` | `fftoday.com` | Posts on Wednesday; until then it fails with `the page reads "No Player Found!"`. QB, RB, WR, TE only. |
| `rotoballer` | `rotoballer.com` | The week's article is found through the news sitemap; until it is posted the source fails with "RotoBaller has not posted week N" and is dropped for that run. |
| `fleaflicker` | `fleaflicker.com` | Needs `FLEAFLICKER_LEAGUE_ID`. Fails if the league's scoring differs from the pinned rules, or if the league is not projecting week N yet. |

Each source's rows go through eight checks before they are stored. A source's status is its worst check. A source that fails is dropped: its rows for the week are deleted and the scrape step turns `warn`.

- `fetch` (shown only when it fails): the fetch raised; the detail is the exception's first line.
- `position_agreement`: rows whose position differs from Sleeper's for the same player (shifted columns, a running back at QB). Over 2% fails, over 0.5% warns.
- `duplicate_positions`: one player listed under two positions. More than two players fails.
- `position_counts`: rows per position outside QB 20–80, RB 40–150, WR 50–200, TE 20–130, K 15–40, DEF 20–36. Catches a truncated or padded list.
- `value_agreement`: per position, correlation `r` and median absolute difference `MAD` against Sleeper. QB, RB, WR, TE fail below r 0.85 or above MAD 4.0; K and DEF only warn. Catches the wrong scoring or the wrong column.
- `team_codes`: rows with an unrecognised team. Over 5% fails, any warns.
- `week_stamp`: the page says it is for another week. `n/a` for sources that name no week.
- `freshness`: over 90% of players have the same points as the source's previous week, a stale copy.
- `top_players`: a top-3 player at a position is missing from Sleeper's player database at that position.

## Wednesday

```bash
python -m pipeline run --week N
```

It runs every step but publish, in order: league, scrape, clean, match, stats, accuracy, calibrate, lineups, simulate, odds, playoffs, validate. The first line names the run, `Run 2026w05-20261007T141203 for season 2026 week 5 (league ...)`. A failed step stops the run.

### Read the output

- **Each source's block** in the scrape step: its check table, then its review tables, the top 15 players at QB, RB, WR and at TE, K, DEF with team and points. After the last source comes a table of every source with its status, rows and seconds. These print long before the run ends; read them while the later steps run.
- **The run summary table** at the end: each step `ok`, `warn` or `failed`, its duration and its first warning or error, then `Run <run_id>: <status>`.
- **The simulate step's line** `betting window closes at <time>`: Thursday's kickoff, in UTC.
- **On a rerun, the simulate step's line** `N starters locked at their real points`: the owners' starters from games already final. Wednesday's run locks none.

Expected warnings, which need no action:
- `accuracy: no projections for week N-1` and `calibrate: no week graded by the accuracy step yet` in the first week the pipeline runs a season (week 4 in 2026). Both steps grade the previous week.
- `scrape: dropped firstdown.studio (fetch)`, and the same for `fftoday.com` and `rotoballer.com`, when the site has not posted the week yet. On a Wednesday morning this is normal.
- `playoffs: playoffs have started; no futures markets` once the playoff weeks begin.

A real failure looks like:
- A `fetch` failure with any other message: a timeout, an HTTP error, a parse error ("has no projections table", "has columns ... expected ...").
- `position_counts` failing, such as `outside the expected range: WR 31 (expected 50-200)`.
- `week_stamp` failing, such as `expected week 5, found week 4 (212 rows)`.
- `value_agreement` failing, such as `QB r=0.62 MAD=5.10 n=31 (fail)`.
- Any step `failed` in the summary table.

### Verdicts

For every source that ran and was stored (status `ok` or `warn`), eyeball its review tables: this week's stars at the top, teams current, nobody on bye or ruled out near the top, points on a PPR scale. Then record a verdict. Record them before publishing: the dashboard shows the verdicts that were in place at the publish.

```bash
python -m pipeline review --week N --source espn.com --verdict ok --note "top 15 plausible; value_agreement ok"
```

If the tables show something the checks missed (a position full of another position's players, half-PPR numbers, last week's injured stars on top), reject the source. A reject deletes its rows for the week; then rerun from clean. Never reject `sleeper.com`, and if a reject would leave fewer than three sources, stop and tell the owner.

```bash
python -m pipeline review --week N --source rotoballer.com --verdict reject --note "WR table is TEs"
python -m pipeline run --week N --from clean
```

### When a source failed

Read its check table first.

- **The site changed** (a parse error, or a check such as `position_counts` failing on a page that did load): fix the parser in `pipeline/sources/<name>.py`, refresh its fixture in `tests/pipeline/fixtures/<name>/`, and run `python -m pytest tests/pipeline/test_sources_html.py tests/pipeline/test_sources_api.py`. Then rescrape it and rerun from clean.
- **The site has not posted yet** (FirstDown, FFToday, RotoBaller on a Wednesday morning): rescrape it later with the same two commands. Publishing first and rescraping later is fine; the rescrape then needs its own publish.

```bash
python -m pipeline run --week N --steps scrape --sources firstdown,fftoday
python -m pipeline run --week N --from clean
```

A `--sources` run scrapes only the named sources, lists the others as `kept` with the rows they already have, and fails if any named source fails. Give each rescraped source a verdict.

- **Fewer than three usable sources, or no Sleeper**: the scrape step fails with `a full run needs 3 usable sources including sleeper.com; usable: ...; dropped: ...` and the run stops. Fix a source or wait for one to post, rescrape the missing sources as above, then `--from clean`.

### Publish

```bash
python -m pipeline run --week N --steps publish --dry-run
```

It prints every table with the rows it would upload and writes nothing (its `dry run: nothing was written to ...` warning is expected). Check that the week's odds tables, `team_lineups`, `sleeper_matchups` and `simulation_runs` have rows, and that no table you expect is listed as skipped. Then:

```bash
python -m pipeline run --week N --steps publish
```

It uploads `backend/data/images/*.png` to the droplet, appends the week's new run to `simulation_totals`, then stages every table in production PostgreSQL, checks each one's row count and swaps them all in at once. `--no-charts` skips the chart upload.

Publish before Thursday's kickoff. The window closes at the first kickoff after the run was made, not after it was published, so a run published late opens nothing.

### Dashboard and admin

- Open `/admin/pipeline?week=N`. Every step `ok`, or `warn` with only the warnings above. The Sources panel shows each source's checks and your verdicts. The Runs panel lists the week's runs, the publish run included.
- Open `/admin` and set the week's betting period with its lock time at **Sunday's first kickoff**, entered in UTC. Never Thursday's: the lock is the hard close and the kill switch, and once it passes the week stays locked.

The week is open when the betting period exists and is not locked, and the latest published run's `window_closes_at` is still in the future. At Thursday's kickoff betting pauses on its own; the next publish reopens it until its own window closes.

## Friday

Once Thursday's game is final:

```bash
python -m pipeline run --week N --steps league,lineups,simulate,odds,playoffs,validate
```

There is no scrape. Wednesday's projections stand, and the league step brings the injury news. If time is short, leave out playoffs:

```bash
python -m pipeline run --week N --steps league,lineups,simulate,odds,validate
```

Publish keeps the latest run of each table, so the weekly odds move to Friday's run while the futures tables keep Wednesday's. Then the dry run and the publish, as on Wednesday. The new run's window closes at the week's next kickoff, usually Sunday's first.

The league step brings Thursday's final status and the owners' starters with their points; the lineups step pins those starters at their real points and keeps their bench mates out of the lineup, and the simulate step fixes them in every simulation and logs `N starters locked at their real points`. `simulation_runs.n_locked` and the dashboard's simulate summary show the count. If a game is still in progress the run does not stop but warns `<away> at <home> is in progress ...; its players are simulated as unplayed`: wait for the final and rerun.

## Saturday

For late inactives, the full default run again, then verdicts and publish:

```bash
python -m pipeline run --week N
```

It rescrapes every source, including one you rejected on Wednesday. Reject it again if it is still wrong, and give a new verdict to any source whose status or top players changed. A verdict replaces the one before. Then the dry run and the publish, finished before Sunday's first kickoff.

The same locks apply: Thursday's game is final, so its starters are pinned at their real points as on Friday. Sources may drop Thursday's players by Saturday, which is fine: a pinned starter needs no projection.

## After the week

Nothing runs in the pipeline. Settlement is the settlement preview on `/admin`. Next Wednesday's run grades this week in its accuracy step.

## When things go wrong

- **A step failed**: the run stopped there and the summary lists `Not run: ...`. The traceback or `ERROR:` line is in the run's output. `python -m pipeline status --week N` shows every step's last status and note. Fix the cause, then `python -m pipeline run --week N --from <step>`.
- **validate failed**: its log lines read `[validate] <check>: fail - <detail>`. Publish would upload a broken week, so fix the cause and rerun from the step that made it.
- **Publish failed**: `DATABASE_URL is not set` means `.env` lacks it. A failed chart upload stops before any table is written; check the SSH key, or publish with `--no-charts` now and publish again once the key works. A failure while staging or swapping leaves production's tables as they were, because the swap is one transaction; the run's matrix may already be in `simulation_totals`, which is harmless. Rerun the publish.
- **A run is slow**: the playoffs step re-projects every future regular-season week and the playoff weeks, scraping each with Sleeper, ESPN and FantasySharks, and FantasySharks waits 60 seconds between requests. Leave playoffs out of a Friday rerun. `--sources sleeper,espn` narrows the playoffs step's sources too, at the cost of pricing the futures from fewer sources; tell the owner if you use it.
- **The dashboard shows a step as not run**: no published run of the week ran that step (it was not in any run's `--steps`), or the run that did has not been published. The dashboard reads production.
- **Betting shows closed after a publish**: check that the week has a betting period on `/admin` and that its lock time has not passed, and look for the simulate warning `no week N kickoff after this run`.

## Report to the owner

After each run, report:
- The sources used and the sources dropped, each with its reason (the failed check or fetch message).
- The verdicts recorded, with their notes.
- The run id published (the run that ran simulate) and when its window closes.
- Every warning, and whether it is an expected one.
- The playoffs step's runtime, or that it was left out.
