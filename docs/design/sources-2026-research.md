# Projection sources for 2026: research

Status: research for the orchestrator, 2026-09-28. Nothing here is built.
Author: researcher R1. Evidence: 69 live requests to 22 sites between 23:22 on Sunday 2026-09-27 and 00:11 on
Monday (ET), the saved responses, the repository's test fixtures, and read-only copies of the local databases. The
probe scripts stay in the research scratch folder; section 12 says what each one did.

---

## 1. Recommendation

Add, in this order:

1. **FFToday, now, for QB, RB, WR and TE.** It is the only new site that passes the entry test: free, no login,
   plain `requests`, and nothing in its robots.txt or on any terms page forbids automated reading. It publishes its
   own stat lines, which rescore exactly to league points. In week 3 it tracked Sleeper at r 0.90 to 0.94 (r is the
   correlation between two sources' points for the same players; 1.0 means they rank and space players
   identically). Current week only. Small effort.
2. **RotoBaller, only if RotoBaller agrees in writing.** It tracked Sleeper better than anything else I probed
   (r 0.94 to 0.98) and publishes stat lines. Its terms bar storing and reusing its content without prior written
   consent (§3.2) and license its data only under written agreements (§13), so until then it is out. Medium effort.
3. **Fleaflicker, only if Fleaflicker agrees.** Its help page, which I saw only through web search, says it posts
   free weekly projections for all six positions on Tuesdays, and it documents a JSON API. Its robots.txt disallows
   `/api/`, so I fetched no data. Effort unknown.

Decisions that come with this:

- **Future weeks.** No new free source publishes them. Keep ESPN: its future weeks are real week-by-week numbers,
  not a season average. But in the three future weeks I probed its QBs fail the r check while staying close on
  MAD, and the stored week-4 run dropped ESPN for 9 of its 10 future weeks, simulating them on Sleeper alone. The fix
  belongs to WP8b: for future weeks only, judge QB on the median gap and report its r as a warning (section 6). Add FantasySharks as the second only if WP8b fixes its QB points. Section 6.
- **Drop FantasyPros.** It averages other sites, which by web search include ESPN and numberFire (behind FanDuel),
  so the blend would count them twice. Logged out it shows 10 rows per position (the repository's fixture test
  asserts this), which fails the row-count check every week, so a full run drops it anyway. The v2.1 fit already
  leaves it out. Section 7.
- **Fit FFToday before it moves the odds much.** Its WR numbers run about 1.4 to 2.7 points above actual scores,
  inferred from its gaps to the three sources the model has already fitted. With no fitted correction it lifts each WR's
  blended projection by about 0.25 to 0.45 points in a full run. I would backfill 2025 weeks 10 to 16 from FFToday's
  own archive and refit, if one test request shows those pages are still online; otherwise add it now and refit
  after four 2026 weeks. Section 4.8.
- **Separately, before Wednesday's publish: v2.1's Sleeper QB correction is stale.** v2.1 subtracts 4.41 points
  from every Sleeper QB projection, because Sleeper ran that far above actual QB scores in 2025. In 2026 Sleeper's
  QBs sit only 0.45 above ESPN's, against 3.30 in 2025, so the right correction now looks like about 1.45. Today's
  blended QB projections are about 1.56 points too low in a Sleeper-plus-ESPN run and 0.55 to 0.66 too low with
  five or six sources. I would patch the number to 1.45 as a stopgap. Section 9.

What would change my mind:

- RotoBaller or Fleaflicker giving permission. RotoBaller would then go first.
- FFToday posting the next week too late for the Wednesday run, its second page or its PPR QB page looking
  different from what I saw, or FFToday publishing terms that forbid automated reading.
- A free source with future weeks that passes the entry test.
- A refit showing that FFToday makes the blend's misses larger.

One policy question sits under all of this. Read as strictly as I read the new sites, the terms of Sleeper, ESPN,
FantasySharks and FirstDown forbid automated access, FanDuel's robots.txt disallows the API path its scraper reads,
and FantasyPros allows personal use only (section 10). I applied the entry test to new sources only. Whether it
applies to the existing six is the owner's call.

## 2. How to read the numbers

Every comparison is against Sleeper for the same week and position, because `pipeline/sources/verify.py` uses Sleeper
as the yardstick.

| Term | Meaning |
|---|---|
| pairs | players both sources project, matched on normalised first name, last name and position (the key `value_agreement` uses) |
| r | correlation of the two sources' points over the pairs; 1.0 means they rank and space players identically; `value_agreement` fails QB, RB, WR and TE below 0.85 and only warns for K and DEF |
| MAD | median absolute difference: the typical gap in points for one player, ignoring its sign; the check fails above 4.0 |
| median difference | the median of (source minus Sleeper); positive means the source projects more |
| rescore | recompute league points from a site's stat lines (yards, touchdowns, receptions) with the league's full-PPR scoring |
| mu | the blended projection for a player: a weighted average of the sources after each source's fitted bias is subtracted (`pipeline/steps/stats.py`) |
| bias | how far a source ran above actual scores at a position, on average, over 2025 weeks 10 to 16 (the v2.1 fit) |
| full run | a run with every registered source, as opposed to a `--sources` subset |

## 3. Candidates

### 3.1 The three worth building

| | FFToday | RotoBaller | Fleaflicker |
|---|---|---|---|
| Transport | one HTML table per position, plain `requests`; RB and WR run to a second page | one HTML article per week with one table, found through the site's news sitemap | documented JSON API |
| Login | none for the preset scoring pages; custom scoring needs an account, which we do not need | none | unknown |
| Terms verdict | passes: no robots.txt (404) and no terms page (4.1) | fails without written permission (§3.2, §13) | unknown: robots.txt disallows `/api/` and `/terms` |
| Positions | QB, RB, WR, TE; K without field-goal distances (left out); no DEF | QB to TE in the midweek article; K and DEF only in a Sunday update, after bets lock | QB, RB, WR, TE, K, D/ST (help page, via web search) |
| Scoring or stat lines | stat lines plus a preset points column | stat lines for QB to TE plus half-PPR "Fan Points"; K and DEF have points only | unknown |
| Current week on Sunday night, 27 Sep | week 3; week 4 not posted at 23:28 ET | week 3 (Wed 23 Sep article, Sun 27 Sep update) | not fetched |
| Future weeks | no: weeks 4 and 7 return an error page | no | unknown |
| Rows per position | QB 32, RB 50 + page 2, WR 50 + page 2, TE 42, K 32, DEF 0 | Wed: QB 32, RB 76, WR 123, TE 69. Sun: QB 32, RB 89, WR 139, TE 76, K 32, DEF 32 | unknown |
| Update stamp | none; the page gives only the week | publish and modify times in the page metadata | "Tuesdays" (help page) |
| Where the numbers come from | its own forecast (hypothesis: no provider is credited) | its own, by a staff analyst (hypothesis) | not stated (hypothesis: in-house) |
| Verdict | build now | build after written permission | ask, then probe |

### 3.2 Out

"Checked" means I fetched the page or file. "Search" means I relied on web search results.

| Candidate | Why it is out | Evidence |
|---|---|---|
| CBS Sports | terms §10 Acceptable Use (effective 27 May 2025) bars spidering, scraping and data mining | checked: terms; robots.txt allows the projections path |
| NFL.com | its fantasy game moved to ESPN for 2026; `api.fantasy.nfl.com/robots.txt` says `Disallow: *` for every user agent | checked: robots.txt. Search: the move (July 2026 announcements) |
| Yahoo | projections sit inside logged-in leagues, and its Fantasy API needs OAuth, which means a Yahoo login | checked: robots.txt, which bars only named AI crawlers and a few paths, not other readers. Search: the OAuth requirement. The missing public page is a hypothesis |
| Subvertadown | free K and DEF values are locked behind subscribe buttons; future weeks are paid | checked: pages; terms (17 Sep 2023) have no scraping clause |
| FantasyData | two season-total rows, then "SIGN UP TO SEE MORE" | checked |
| The Football Database | no projections page found; robots.txt has `Disallow: /fantasy/` | checked: robots.txt. Search: the page |
| Razzball | paid; robots.txt asks for a 10 s crawl delay | checked: robots.txt |
| Walter Football | rankings, not projections | checked: robots.txt. Search: the content |
| Pro Football Network | no projections tool found | checked: robots.txt. Search: the tool |
| Fantasy Football Analytics, Fantasy Nerds | averages of other sites; Fantasy Nerds also needs an API key | checked: robots.txt (Fantasy Football Analytics). Search: the rest |
| MyFantasyLeague | republishes FantasySharks | search |
| Footballguys, 4for4, PFF, FTN, RotoWire, Fantasy Points, Fantasy Alarm | paid | search |
| DraftSharks, FFCalc | rankings, not projections | search |
| Sharp Football Analysis | betting-adjacent | search |

## 4. FFToday: build now

### 4.1 Terms and robots

- robots.txt: the server returns 404, so nothing is disallowed.
- Terms: there is no terms page. The sign-up page's terms pop-up is switched off in its script (`__terms_show = 0`,
  with an empty frame), and signing up only asks the user to accept the privacy policy. We would not sign up
  anyway: the PPR preset needs no account.
- Privacy policy (updated 7 Feb 2023): the site logs pages, referring URLs and IP addresses to watch traffic, and
  says it may contact a visitor's internet provider if it suspects abuse of its resources. The pipeline's six
  spaced requests a week are fewer than one person clicking through the same pages.
- No data provider is credited; the pages say "Copyright 1998-2026 FFToday.com".

### 4.2 Fetch recipe

- URL: `https://www.fftoday.com/rankings/playerwkproj.php?Season={season}&GameWeek={week}&PosID={id}&LeagueID=107644`,
  with `PosID` 10 for QB, 20 RB, 30 WR, 40 TE.
- `LeagueID` picks FFToday's scoring preset: 107644 is PPR, 1 is Standard, 193033 is Half PPR (what the page shows
  when `LeagueID` is absent). The FFToday line in `docs/architecture/08` says `LeagueID=1`, which is Standard.
- Headers: a User-Agent that names the project and a contact URL, for example
  `TNCasino-pipeline/2026 (+https://tncasino.win)`. No cookies are needed.
- Pagination: 50 rows per page. The RB and WR pages end in a "Next Page" link, which adds
  `&order_by=FFPts&sort_order=DESC&cur_page=1`. Follow it once. QB (32 rows) and TE (42) fit on one page.
- Requests: six a week (QB, RB, RB page 2, WR, WR page 2, TE), 5 s apart, about 30 s in all.
- A week that is not posted yet returns HTTP 200 with a 304-byte page that says "No Player Found". The parser must
  raise on it, so the scrape step records a fetch failure instead of storing nothing.

Why the second page matters (`probe_starter_depth.py`, the league's week-4 starters from `team_lineups`):

| Position | Page-1 rows | Lowest points on page 1 | League starters, week 4 | Starters below that floor |
|---|---|---|---|---|
| QB | 32 | 11.28 | 12 | 0 |
| RB | 50 | 4.80 | 29 | 0 |
| WR | 50 | 10.60 | 31 | 6 |
| TE | 42 | 1.50 | 12 | 0 |

Page 1 alone would miss 6 of the league's 31 starting WRs.

### 4.3 Parse

- Table: the row with class `tableclmhdr` holds the column names; player rows follow it.
- Columns, QB: Chg, Player, Team, Opp, then passing Comp, Att, Yard, TD, INT, then rushing Att, Yard, TD, then FPts.
- Columns, RB, WR and TE: Chg, Player, Team, Opp, then rushing Att, Yard, TD, then receiving Rec, Yard, TD, then
  FPts.
- The names Att, Yard and TD repeat, so check the header row against the expected list and then read cells by
  position.
- Week: the cell `td.update` reads "2026 Week 3". Parse it with `(\d{4}) Week (\d+)` and stamp every row with it, so
  the `week_stamp` check compares the page's own week with the requested one.
- external_id: FFToday's player id, from the `/stats/players/{id}/` link.
- Names through `split_full_name`, teams through `normalize_team` (FFToday writes JAC for JAX). 0 of 206 week-3 rows
  had an unrecognised team.
- Injury status is an image beside the name, not text, so no tags are glued to names.
- Chg holds an arrow on a few rows (RB 3, WR 3, QB 1, TE 2). I read these as revisions since posting (hypothesis).

Names that often trip matching, as FFToday writes them:

| Form | Examples | Result |
|---|---|---|
| Suffix | Michael Penix Jr., Luther Burden III, Brian Thomas Jr., Harold Fannin Jr. | all matched; `normalize_name` drops suffixes |
| Apostrophe | D'Andre Swift, De'Von Achane, Ja'Marr Chase, Ka'imi Fairbairn | all matched |
| Hyphen or two-word last name | Jacory Croskey-Merritt, Jaxon Smith-Njigba, Amon-Ra St. Brown | all matched |
| Initials | C.J. Stroud, J.K. Dobbins, D.J. Moore, T.J. Hockenson | all matched |
| Nickname | Kenneth Gainwell (Sleeper: Kenny, TB), Andres Borregales (Sleeper: Andy, NE) | missed by `value_agreement`, which needs the exact name; the match step's last-name-plus-first-initial rule finds both |
| Defense names | none: FFToday publishes no DEF | not applicable |
| Injury tags | none: status is an image | not applicable |

### 4.4 Rescoring

League points from FFToday's stat columns:

```
points = 0.04 * pass_yd + 4 * pass_td - 2 * int
       + 0.1 * rush_yd + 6 * rush_td
       + 1 * rec + 0.1 * rec_yd + 6 * rec_td
```

- RB, WR and TE on the PPR page: FFToday's own FPts equals this rescore exactly (largest gap 0.00 over 142 rows).
  That gives the parser a self-check for free.
- QB: I fetched only the Half-PPR default page. There, FPts minus the rescore equals 2 x INT to within 0.04
  (median gap 0.97, largest 1.62), so that preset does not charge interceptions. Rescoring from the stat columns
  sidesteps whatever the PPR preset does.
- FFToday projects no fumbles and no two-point conversions. Their size in Sleeper's week-3 stat lines
  (`probe_missing_terms.py`):

| Position | Players | Median points | Mean points |
|---|---|---|---|
| QB | 32 | -0.18 | -0.16 |
| RB | 95 | -0.02 | -0.03 |
| WR | 157 | 0.00 | 0.00 |
| TE | 98 | 0.00 | 0.00 |

  Leaving them out puts FFToday's QBs about 0.2 points high against a source that counts them. The other positions
  are unaffected.
- K: FFToday gives field goals and extra points made and missed, with no distances. The league pays 3, 4 or 5 points
  by distance, and FFToday's whole-number kicks allow only a few totals (5, 6, 8 or 9 points). Against Sleeper, r is
  0.79 rescored and 0.80 with FFToday's own points, which only warns. Leave K out.
- DEF: `PosID=99` returns no rows under the PPR preset, and the page's position menu lists QB, RB, WR, TE and K only.

### 4.5 Week 3 against Sleeper

FFToday served week 3 on Sunday night; Sleeper's week-3 payload was fetched four to six minutes after FFToday's
pages. Sleeper had 446 rows
(QB 32, RB 95, WR 157, TE 98, K 32, DEF 32); FFToday had 206 (page 1 only for RB and WR). `probe_verify_fftoday.py`
ran the checks the way `pipeline/steps/scrape.py` does, with `nfl_players` from the read-only `league.db`.

| Check | Status | Detail |
|---|---|---|
| position_agreement | ok | 0 of 204 matched rows disagree with Sleeper's position |
| duplicate_positions | ok | 0 of 206 |
| position_counts | ok | QB 32, RB 50, WR 50, TE 42 (K 32 if kept) |
| value_agreement | ok for QB to TE, warn for K | table below |
| team_codes | ok | 0 of 206 unrecognised |
| week_stamp | ok | all 206 rows are week 3 |
| freshness | n/a | no earlier FFToday rows |
| top_players | ok | all 15 top-3 players are in Sleeper's player list (12 without K) |

Value agreement, rescored:

| Position | Rows | Pairs | r | MAD | Median difference | Status |
|---|---|---|---|---|---|---|
| QB | 32 | 32 | 0.90 | 1.19 | -0.87 | ok |
| RB | 50 | 49 | 0.93 | 1.21 | -0.11 | ok |
| WR | 50 | 50 | 0.94 | 1.76 | +1.76 | ok |
| TE | 42 | 42 | 0.93 | 1.13 | +0.04 | ok |
| K | 32 | 31 | 0.79 | 1.43 | +0.11 | warn (left out) |

Where the gaps come from (`probe_fftoday_gap.py`; FFToday minus Sleeper, medians over pairs; each stat's gap in its
own units, then in points):

| Position | Total points | Stat gaps: units (points) |
|---|---|---|
| QB | -0.27 | pass yd +4.39 (+0.18), pass TD +0.03 (+0.12), INT -0.14 (+0.27), rush yd -2.47 (-0.25), rush TD -0.08 (-0.51) |
| RB | -0.20 | rush yd +2.51 (+0.25), rush TD -0.04 (-0.24), rec +0.04 (+0.04), rec yd +1.58 (+0.16), rec TD -0.07 (-0.42) |
| WR | +1.80 | rec +0.84 (+0.84), rec yd +7.39 (+0.74), rec TD +0.07 (+0.45) |
| TE | +0.05 | rec +0.17 (+0.17), rec yd +1.38 (+0.14), rec TD -0.04 (-0.27) |

These totals are medians of each player's total gap, so they differ a little from the table above, which compares
FFToday's rescore with Sleeper's `pts_ppr`.

- WR: FFToday expects almost one more catch and seven more yards per receiver. This is not a quirk of which WRs are
  on page 1: 47 of Sleeper's top 50 WRs are there, and their median gap is +1.73.
- QB: about 0.6 of the -0.87 comes from Sleeper, not FFToday. Sleeper's `pts_ppr` charges -1 per interception
  instead of the league's -2 (section 10), which lifts Sleeper's QBs by a median 0.60 in week 3.

### 4.6 What verification will say week to week

- position_counts: ok. With page 2, RB and WR should land between 50 and 100. Page 1 alone gives exactly 50 WRs,
  which is the bottom of the WR range: it would pass while missing starters, so the parser should not stop at page 1.
- value_agreement: QB (r 0.90) is closest to the 0.85 line. QB to TE fail rather than warn, so a bad week drops
  FFToday for that week and the other sources carry the run. That is the check working, not a reason to loosen it.
  FFToday posts once, midweek, and has no update stamp; Sleeper keeps updating. Late injury news will show up as
  disagreement. My week-3 comparison was harsher than a Wednesday run will be: FFToday's page most likely dates
  from midweek, while Sleeper's payload carried a Sunday-night update (section 11).
- week_stamp: meaningful, because the week comes from the page.
- freshness: n/a in the first week; afterwards it compares with FFToday's rows from the week before.
- team_codes and top_players: ok on week 3; nothing suggests they will change.
- A week not yet posted: the parser raises. A full run drops FFToday with a warning; a `--sources` run stops with
  "requested sources failed", as the scrape step does today for any failed fetch.
- Byes: unverified. Week 5 is the first 2026 bye week (KC and CAR, per ESPN's week-5 data). Hypothesis: FFToday
  leaves bye-week players off the page, which is what matters. Test on the week-5 page.

### 4.7 When FFToday posts (hypothesis)

The site gives no posting time. Evidence from the Wayback Machine (`probe_wayback_fftoday.py`) and from my own
fetches:

| When (ET) | What the page showed |
|---|---|
| Tue 21 Sep 2021, 07:14 | the default page on week 2, the week just played |
| Wed 1 Dec 2021, 19:35 | week 13, the week about to be played |
| Wed 28 Sep 2022, 02:24 | week 3, the week just played |
| Sun 12 Oct 2025, 23:38 | week 7 requested: error page |
| Tue 16 Dec 2025, 15:52 | week 16 requested: error page |
| Wed 17 Dec 2025, 05:13 | week 7 requested: still served, nine weeks later (30 QB rows) |
| Sun 27 Sep 2026, 23:28 | week 4 requested: error page |

Reading: the next week appears on Wednesday, between about 02:30 and 19:35 ET, and past weeks stay online. This
matters because the pipeline runs on Wednesday: a morning run may find week N+1 missing, and FFToday is then
dropped for that week. Test: request the week-4 QB page on Wednesday 30 September, once in the morning and once in
the evening ET.

Past seasons stay online too. The archive holds 272 captures of these pages taken between September 2025 and January
2026, and they include working pages for every season from 2014 to 2025 (`probe_wayback_archive.py`). That is why
the backfill in 4.8 looks feasible.

### 4.8 What FFToday does to the blend

The stats step gives any source missing from the model parameters weight 1 and no bias (`DEFAULT_SOURCE` in
`pipeline/steps/stats.py`). So FFToday would enter uncorrected.

FFToday's implied bias: another source's fitted v2.1 bias plus FFToday's gap to that source on shared week-3
players (`probe_fftoday_bias.py`). Mean, with the median in brackets:

| Through | QB | RB | WR | TE |
|---|---|---|---|---|
| Sleeper | +3.93 (+3.54) | +0.02 (-0.11) | +2.69 (+2.66) | +0.59 (+0.47) |
| FanDuel | +0.15 (+0.38) | +0.82 (+0.86) | +1.43 (+1.47) | +0.58 (+0.51) |
| FirstDown | +0.76 (+0.60) | -0.56 (-0.46) | +1.57 (+1.59) | -0.17 (-0.09) |

The QB figure through Sleeper inherits Sleeper's stale QB bias (section 9); with 1.45 in place of 4.41 it would be
about +0.97. The three agree on one thing: FFToday's WRs run 1.4 to 2.7 points high. FanDuel and FirstDown here are
the repository's trimmed week-3 fixtures.

Measured change in mu when FFToday is added at weight 1, week 3, with the other week-3 data I had (my Sleeper
payload and the repository's trimmed FanDuel and FirstDown fixtures):

| Position | Players | Median change | Mean change | Players with 1 / 2 / 3 other sources |
|---|---|---|---|---|
| QB | 32 | +0.52 | +0.64 | 1 / 10 / 21 |
| RB | 50 | -0.08 | -0.08 | 5 / 25 / 20 |
| WR | 50 | +0.68 | +0.65 | 1 / 29 / 20 |
| TE | 42 | +0.10 | +0.08 | 16 / 6 / 20 |

In a full run FFToday is one vote in about six (the four fitted sources' weights sum to 4.0, plus FantasySharks and
FFToday at 1 each), so each WR's mu rises by about (1.4 to 2.7) / 6, or 0.25 to 0.45 points. A team starts about 2.6
WRs, so its projected total rises by about 0.6 to 1.2 points. Head-to-head odds mostly cancel that; over/under lines
do not. WR sigma, the spread the simulation draws around mu, rises by about 0.1 (in v2.1, sigma grows in a straight line
with mu).

The options:

| Option | What it takes | Effect until a refit |
|---|---|---|
| (b) Backfill and refit | fetch FFToday for 2025 weeks 10 to 16 (42 requests, about four minutes at 5 s), store them, refit v2.2 with FFToday in | none: FFToday enters with its own weight and bias |
| (a) Add now, refit later | nothing extra; refit once 2026 has four weeks of FFToday rows and actual scores | WR mu +0.25 to +0.45 per player |
| (c) Collect only | a small change in `stats.py`: a weight of 0 in the parameters makes `np.average` fail for a player only FFToday projects | none, and no benefit either |

I would build (b) if one test request (`Season=2025&GameWeek=10&PosID=20&LeagueID=107644`) returns rows. If it does
not, (a): the lift is small next to a starting WR's sigma of about 7 to 10 points. (c) only if the owner wants no
effect at all before a refit. The backfill and refit are their own package, not part of the brief in section 8.

### 4.9 Fixture files

Saved under the R1 scratch folder, `research/r1/payloads/fftoday/`, for the orchestrator to copy to
`tests/pipeline/fixtures/fftoday/`. All are under 300 KB, so no trimming is needed.

| Scratch file | Bytes | Suggested fixture name | Note |
|---|---|---|---|
| `wk03_RB.html` | 73,207 | `projections_2026_w3_rb.html` | page 1, PPR |
| `wk03_WR.html` | 72,866 | `projections_2026_w3_wr.html` | page 1, PPR |
| `wk03_TE.html` | 65,824 | `projections_2026_w3_te.html` | PPR |
| `wk03_QB.html` | 62,136 | `projections_2026_w3_qb.html` | Half PPR (the default page); replace with the PPR page on the first live fetch |
| `wk04_QB.html` | 304 | `projections_2026_w4_not_posted.html` | the "No Player Found" page |

The RB and WR second pages were never fetched; the engineer captures them on the first live fetch.

### 4.10 Effort

Small: a day. The module follows `pipeline/sources/firstdown.py`, and the rescore has a built-in check (it must
equal FFToday's own PPR points for RB, WR and TE). The risks behind the size: the second page and the PPR QB page are
unseen, and the Wednesday posting time may clash with the run. Five scrape and playoffs tests hard-code six sources
and need the same small change (section 8).

## 5. RotoBaller and Fleaflicker: only with written permission

### 5.1 RotoBaller

Terms (page last modified 11 May 2026):

- §3.2 Limited License: personal, non-commercial use and one downloaded copy. No reproducing, derivative works,
  downloading beyond that, storing or mirroring without prior written consent.
- §13 B2B Licensing and Syndication: RotoBaller licenses its data and content to partners under written agreements.

Automated weekly reading and storing is the storing and reuse §3.2 bars. robots.txt does not forbid the pages:

```
User-agent: *
Disallow: /wp-admin/
Disallow: /wp-admin/admin-ajax.php
Disallow: /tag/
Disallow: /api/rbapps
```

Its sitemaps include `/google-news-sitemap.xml` and `/wp-content/uploads/sitemaps/nfl_tools_index.xml`.

What it publishes: one article per week with one table, and a Sunday update that adds K and DEF.

| Article | Path | Published (ET) | Modified (ET) |
|---|---|---|---|
| Week 2 | `/fantasy-football-projections-for-week-2-rb-wr-qb-te-2026/1932317` | Thu 17 Sep, 08:40 | 10:45 |
| Week 3 | `/fantasy-football-projections-for-week-3-rb-wr-qb-te-2026/1948517` | Wed 23 Sep, 15:30 | 14:22, earlier than published |
| Week 3 update | `/updated-fantasy-football-projections-for-week-3-rb-wr-te-qb-d-st-k-2026/1952030` | Sun 27 Sep, 10:41 | same |

- The Wednesday article has a "Comp" column the Sunday update lacks, so columns must be read by header name.
- Points are half PPR: "Fan Points" minus a half-PPR rescore of the stat lines has a median of -0.04 (Wednesday)
  and -0.01 (Sunday), largest 0.57 and 0.60. Against a full-PPR rescore the medians are -0.93 and -0.80. So we
  would rescore, as with FFToday.
- K and DST rows have Fan Points only. DST rows give the full team name plus a code ("Seattle Seahawks", SEA), so
  defenses map by code. No injury tags in names.
- The Sunday update kept most of Wednesday's numbers (MAD 0.00 between them) and still projects players Sleeper
  lists as Out.
- For our Wednesday run only the midweek article can be in time, and it came out on Thursday morning in week 2 and
  Wednesday afternoon in week 3. The Sunday update, with K and DEF, comes after bets lock on Thursday.

Against Sleeper, rescored (`probe_verify_rotoballer.py`):

| Position | Wed pairs | Wed r | Wed MAD | Wed median difference | Sun pairs | Sun r | Sun MAD | Sun median difference |
|---|---|---|---|---|---|---|---|---|
| QB | 31 | 0.94 | 0.78 | +0.12 | 32 | 0.94 | 0.78 | +0.11 |
| RB | 68 | 0.98 | 0.68 | -0.10 | 80 | 0.98 | 0.66 | -0.06 |
| WR | 114 | 0.97 | 0.81 | +0.20 | 131 | 0.98 | 0.80 | +0.29 |
| TE | 67 | 0.97 | 0.67 | +0.29 | 75 | 0.98 | 0.56 | +0.33 |
| K | | | | | 32 | 0.76 (warn) | 0.63 | -0.15 |
| DEF | | | | | 32 | 0.86 | 0.75 | -0.52 |

Every other check was ok on both articles (300 and 400 rows; top players 12 of 12 and 18 of 18).

Unmatched names were mostly players Sleeper did not project at all. The name and position cases worth knowing:

| Case | Examples | What happens |
|---|---|---|
| Nickname | Kenneth Gainwell | the match step's last-name-plus-first-initial rule finds him |
| Nickname | Bam Knight | the match step's hard-coded match finds him |
| First name with a space | J. Michael Sturdivant | `split_full_name` splits at the first space, giving "J." and "Michael Sturdivant"; Sleeper has "J. Michael" and "Sturdivant". Needs a parser or `names.py` fix |
| Listed at another position | Travis Hunter (WR here, DB in Sleeper's player list); Kyle Juszczyk, Hunter Luepke, Alec Ingold (RB here, FB there) | not matched: Sleeper lists them as DB or FB, and `nfl_players` does not include them |
| Apostrophe | Lil'Jordan Humphrey | matches `nfl_players`; unmatched only because Sleeper did not project him |

If permission comes: find the week's article in the news sitemap by its title pattern (one request for the sitemap,
one for the article), parse the single table by header names, rescore QB to TE, and treat K and DEF as Sunday-only.
Effort medium, two or three days. The risks: finding the article each week (the path carries an article id, and the
title wording drifts), header drift between articles, and 950 to 980 KB pages that must be trimmed below 300 KB for
fixtures. The saved week-3 payloads are `research/r1/payloads/rotoballer/wk03_projections.html` (957,050 bytes) and
`wk03_updated_projections.html` (979,907 bytes).

### 5.2 Fleaflicker

robots.txt:

```
User-agent: *
Disallow: /terms
Disallow: /privacy
Disallow: /claim-account
Disallow: /logout
Disallow: /api/
Disallow: /*/leagues/*/players/*
Crawl-delay: 1
```

- The API documentation page (`/api-docs/index.html`, not disallowed) names the endpoint
  `https://www.fleaflicker.com/api` and operations such as FetchPlayerListing, FetchLeagueBoxscore and
  FetchLeagueScoreboard. Score objects carry a "projected" value. It gives a contact address.
- I made no API calls and did not read the terms page, because robots.txt disallows both.
- Verdict: ask Fleaflicker (the contact address on the API page) whether the pipeline may read projections through
  the API. If yes, probe it. A JSON API suggests small effort, but whether the projections are per league or per
  scoring preset is unknown.

## 6. Future weeks

The playoffs step simulates weeks 5 to 14 with every source whose `supports_future_weeks` is set: Sleeper, ESPN and
FantasySharks. No new free source publishes future weeks. FFToday returns an error page for any week not yet posted,
RotoBaller writes one article per week, and Fleaflicker is unknown. So the two besides Sleeper have to come from the
existing six:

1. **ESPN.** Its future weeks are honest week-by-week numbers (below). The research brief's hypothesis, that they are
   near-duplicates of a season average, does not hold.
2. **FantasySharks, if WP8b fixes its QB points.** On the repository's trimmed week-4 fixture (about 40 rows per
   position), its QB points run a median 4.63 above Sleeper (MAD 4.81). Rescoring its stat lines with league scoring
   cuts that to 2.01 (MAD 2.13), but r stays at 0.78, still below 0.85 (`probe_verify_sharks.py`).

If FantasySharks cannot be fixed, the playoffs step runs on Sleeper and ESPN alone, and until WP8b's case (d) lands,
mostly on Sleeper alone: the stored week-4 run (Sleeper plus ESPN, 28 Sep 03:02 UTC) dropped ESPN on
`value_agreement` for 9 of its 10 future weeks, all but week 11 (`inspect_runs.py`). The run stores no per-week
check details, so which position failed in weeks 6, 8 to 10, 12 and 14 is unrecorded; in the weeks I probed it was
QB.

ESPN against Sleeper, both fetched on Sunday night (`probe_espn_future.py`):

| Week | Result | QB r | QB MAD | QB pairs | QB median difference | K r | DEF r |
|---|---|---|---|---|---|---|---|
| 4 | ok; DEF warns, one player without a team | 0.93 | 0.79 | 32 | -0.45 | 0.86 | 0.78 |
| 5 | fails on QB | 0.74 | 1.45 | 28 | -1.09 | 0.68 | 0.67 |
| 7 | fails on QB | 0.75 | 1.23 | 27 | -0.54 | 0.67 | 0.77 |
| 13 | fails on QB | 0.78 | 1.50 | 27 | -1.18 | 0.73 | 0.29 |

RB, WR and TE pass in every week, with r 0.94 to 0.96.

Why QB fails: QB projections sit close together. Their standard deviation (how far a typical QB sits from the
average QB) is about 2.2 to 2.7 points in every week, so gaps that are small in points still pull r down. In week 4
the typical gap (MAD) is 0.79 points; in future weeks it is 1.2 to 1.5, enough to push r under 0.85 while staying
far inside the 4.0 MAD limit. Setting aside the three largest gaps lifts r past 0.85 only in week 7, so the
disagreement is broad, not a few outliers. The largest gaps, ESPN minus Sleeper, were Willis -5.90 (week 7),
Mayfield -3.63 and Allen -3.48 (week 5), Willis -3.72 and Herbert -3.56 (week 13). QB spread and r without the
largest gaps (`probe_espn_qb_gaps.py`):

| Week | ESPN QB standard deviation | Sleeper QB standard deviation | r without the largest gap | r without the three largest |
|---|---|---|---|---|
| 4 | 2.42 | 2.43 | 0.94 | 0.94 |
| 5 | 2.28 | 2.72 | 0.76 | 0.73 |
| 7 | 2.20 | 2.70 | 0.82 | 0.89 |
| 13 | 2.39 | 2.49 | 0.79 | 0.81 |

The numbers move from week to week, less at ESPN than at Sleeper. Median absolute change for the same player:

| Weeks | ESPN WR | ESPN TE | ESPN QB | Sleeper WR | Sleeper TE | Sleeper QB |
|---|---|---|---|---|---|---|
| 4 to 5 | 0.10 | 0.07 | 0.88 | 0.24 | 0.14 | 1.56 |
| 4 to 7 | 0.14 | 0.07 | 1.35 | 0.34 | 0.13 | 1.54 |
| 4 to 13 | 0.16 | 0.08 | 0.94 | 0.32 | 0.13 | 1.41 |

- Byes: ESPN drops bye-week players at every position except DEF: its QB and K rows number 32, 30, 28 and 28 in
  weeks 4, 5, 7 and 13, and none of its QB, RB, WR, TE or K rows belongs to a team on bye (`probe_espn_byes.py`).
  It keeps bye-week defenses with points: KC 5.81 and CAR 5.05 in week 5; JAX 3.30, LAC 2.86, BUF 3.11 and
  WAS 4.77 in week 7; BAL 6.39, LV 4.52, NYJ 3.69 and IND 5.87 in week 13. That is harmless: the playoffs step
  benches any player whose team is on bye, defenses included (`lineups.unavailable_reason` returns "bye").
- Sleeper's own future kickers do not change: each of the 26 kickers it projects in both weeks 5 and 13 has the
  same points in both. Under WP8b's rule that is a season number. Its DEF numbers, by contrast, changed between
  those weeks for 73% of teams.
- ESPN accepted our truthful User-Agent.

My data supports WP8b's case (d), which WP8b owns: for future weeks only, judge QB on MAD and report QB r as a
warning. The current week keeps today's rule. ESPN's terms (Disney §2) forbid automated access, which is the policy
question in section 1.

## 7. Independence

Where each source's numbers come from:

| Source | Upstream | Basis |
|---|---|---|
| Sleeper | RotoWire | the `company` field on the new API host reads `rotowire` |
| FanDuel | numberFire | design 7.2 and the research brief |
| FantasyPros | an average of other sites, including ESPN, CBS, numberFire and FFToday | web search; the exact list is a hypothesis |
| ESPN | in-house | hypothesis |
| FantasySharks | its own; MyFantasyLeague republishes it | search |
| FirstDown | its own model, which seems to start from betting-market team totals | hypothesis, from its "Proj. Team Pts" column |
| FFToday | its own | hypothesis: no provider credited |
| RotoBaller | its own, by a staff analyst | hypothesis |
| Fleaflicker | not stated | hypothesis: in-house |

Week-3 pairs, pairs / r / MAD (`probe_independence.py`; RotoBaller is the Sunday update):

| Pair | QB | RB | WR | TE |
|---|---|---|---|---|
| Sleeper and FFToday | 32 / 0.90 / 1.19 | 49 / 0.93 / 1.21 | 50 / 0.94 / 1.76 | 42 / 0.93 / 1.13 |
| Sleeper and RotoBaller | 32 / 0.94 / 0.78 | 80 / 0.98 / 0.66 | 131 / 0.98 / 0.80 | 75 / 0.98 / 0.56 |
| Sleeper and FanDuel | 21 / 0.95 / 0.70 | 23 / 0.96 / 0.89 | 21 / 0.60 / 1.79 | 21 / 0.84 / 1.17 |
| Sleeper and FirstDown | 31 / 0.96 / 1.04 | 47 / 0.95 / 0.99 | 60 / 0.97 / 0.56 | 26 / 0.87 / 0.75 |
| FFToday and RotoBaller | 32 / 0.89 / 0.94 | 50 / 0.93 / 1.35 | 50 / 0.92 / 1.16 | 42 / 0.95 / 1.17 |
| FFToday and FanDuel | 21 / 0.84 / 1.47 | 20 / 0.68 / 1.66 | 20 / 0.55 / 2.73 | 20 / 0.56 / 1.07 |
| FFToday and FirstDown | 31 / 0.94 / 0.55 | 46 / 0.93 / 1.29 | 49 / 0.94 / 2.08 | 26 / 0.91 / 1.12 |
| RotoBaller and FanDuel | 21 / 0.85 / 1.04 | 23 / 0.97 / 1.07 | 21 / 0.52 / 2.58 | 21 / 0.73 / 1.23 |
| RotoBaller and FirstDown | 31 / 0.96 / 0.93 | 48 / 0.97 / 0.95 | 61 / 0.95 / 0.99 | 26 / 0.92 / 0.97 |
| FanDuel and FirstDown | 21 / 0.94 / 1.20 | 20 / 0.87 / 1.21 | 20 / 0.68 / 1.90 | 20 / 0.66 / 1.66 |
| RotoBaller Wednesday and Sunday (a known copy) | 31 / 1.00 / 0.00 | 73 / 1.00 / 0.00 | 117 / 0.97 / 0.00 | 68 / 0.99 / 0.00 |

The FanDuel fixture is trimmed to about 21 top players per position. Top players are bunched, which lowers r, so
FanDuel's r values understate its agreement. Week-4 pairs from the repository fixtures:

| Pair | QB | RB | WR | TE |
|---|---|---|---|---|
| Sleeper and ESPN | 32 / 0.93 / 0.79 | 86 / 0.94 / 1.03 | 148 / 0.96 / 0.78 | 78 / 0.96 / 0.74 |
| Sleeper and FantasySharks | 33 / 0.78 / 4.81 | 39 / 0.88 / 1.41 | 39 / 0.73 / 1.93 | 39 / 0.91 / 0.92 |
| ESPN and FantasySharks | 32 / 0.49 / 5.37 | 38 / 0.90 / 1.11 | 39 / 0.80 / 1.32 | 38 / 0.90 / 0.85 |

No two different sources behave like copies. A copy shows MAD 0.00, as RotoBaller's two articles do; the closest
pair of different sources, Sleeper and RotoBaller, is at 0.56 to 0.80. Whether they share inputs is a hypothesis.

FantasyPros should leave the blend:

- It averages sites we read directly, so they would count twice. Its exact mix is not published in what I read.
- Logged out it shows 10 rows per position (`test_fantasypros_reads_the_ten_players_a_logged_out_visitor_sees` in
  `tests/pipeline/test_sources_html.py`). That fails `position_counts` (QB needs 20 or more) every week, so a full
  run drops it already and it only costs requests.
- The v2.1 fit excludes it (`fitted_on.excluded_sources`), and its terms (§6) allow personal use only.

## 8. Draft brief: WP8c, add FFToday

The package number is a placeholder. The brief's headings sit two levels down so this doc's outline stays intact;
promote them when copying the brief into `briefs/`.

---

### WP8c: FFToday as a seventh projection source

Read `common.md` first; every rule there applies. Start after WP8b has merged: both packages touch
`tests/pipeline/test_sources_html.py` and the scrape-step tests.

#### Goal

FFToday's QB, RB, WR and TE projections enter the pipeline and pass verification against Sleeper on a live week.
Acceptance:

- `python -m pipeline run --week N --steps league,scrape --sources sleeper,fftoday` completes on a live week, with
  fftoday.com at `ok` (a `warn` needs its reason in the report).
- The fixture tests pass without the network. `python -m pytest -q`, `python -m ruff check .` and
  `python -m ruff format --check .` are clean.
- No verification threshold changed.

#### Ownership

- `pipeline/sources/fftoday.py` (new).
- `tests/pipeline/test_sources_html.py`: an `# FFToday` section, and fftoday added to `SOURCES` and `SOURCE_IDS`
  under `# Every source`. The research brief named `tests/sources/test_fftoday.py`; the repository keeps its HTML
  sources in this file, so follow the repository.
- `tests/pipeline/fixtures/fftoday/` (the orchestrator copies in the files from the research doc, 4.9).
- `pipeline/sources/__init__.py`: append `"fftoday"` to `SOURCE_NAMES`.
- `tests/pipeline/test_scrape_step.py` (the assertions at lines 125, 169, 210 and 231) and
  `tests/pipeline/test_playoffs.py` (line 395). These five tests hard-code six sources; an in-memory trial with a
  seventh name registered gave 5 failed, 49 passed. Derive their expectations from `SOURCE_NAMES` instead.
- `docs/design/pipeline-v2.md` 7.2: the row below.
- `docs/architecture/08-constraints-and-debt.md`, the "Add more projection sources" item (lines 13 to 16): mark
  FFToday added with `LeagueID=107644` (the line says 1, which is Standard), note that CBS is out on its terms (§10),
  and correct FantasySharks' `scoring=1` to the `scoring=2` the code uses.

Nothing else: not the model parameters, `stats.py`, the scrape or playoffs steps, or the other sources.

#### Background

FFToday publishes a server-rendered table per position with full stat lines. It has no robots.txt and no terms
page. Its PPR preset (`LeagueID=107644`) matches league scoring exactly for RB, WR and TE. Week 3, checked against
Sleeper on 2026-09-27: every check ok for QB to TE, with r 0.90 to 0.94 and MAD 1.13 to 1.76. It posts the next
week on Wednesday (a hypothesis from archive captures), has no future weeks, no DEF, and kickers without
field-goal distances. The research doc, `docs/design/sources-2026-research.md` section 4, has the details.

#### Tasks

1. **Data dir.** Copy `backend/data/databases/` from the main checkout into a scratch directory and set
   `PIPELINE_DATA_DIR` to the copy for every command.
2. **Module.** `FFTodaySource` with `name = "fftoday"`, `website = "fftoday.com"`, `supports_future_weeks = False`,
   `positions` QB, RB, WR, TE, plus a pure `parse`. Plain `requests` and BeautifulSoup, as in `firstdown.py`.
   - URL as in the 7.2 row; follow the "Next Page" link once for RB and WR.
   - 5 s between requests; a User-Agent naming TNCasino with a contact URL.
   - Raise on the "No Player Found" page and on a header row that is not the expected one.
   - Week from `td.update`; external_id from the player link; names through `split_full_name`, teams through
     `normalize_team`.
   - Points by rescoring the stat columns with league scoring (section 4.4 of the research doc); skip rows at 0 or
     below, as the other sources do.
3. **Tests, fixtures only.** Rescored points equal FFToday's own points for RB, WR and TE; the not-posted page
   raises; the week comes from the page; suffixes and apostrophes survive; the shared `test_parse_is_pure` and
   `test_rows_are_canonical` cover fftoday. Save the RB and WR second pages and the PPR QB page from your first live
   fetch as fixtures.
4. **Registry and docs** as listed under Ownership.
5. **Live runs.** On Tuesday, `python -m pipeline run --week 3 --steps league,scrape --sources sleeper,fftoday`.
   Then week 4 once FFToday posts it: check on Wednesday morning and evening ET and note the time it appeared.
   Record `python -m pipeline review --week N --source fftoday.com --verdict ok|reject --note "..."` in the scratch
   data dir.
6. **Optional, one request.** `Season=2025&GameWeek=10&PosID=20&LeagueID=107644`: report whether 2025 pages are
   still served. The backfill and refit are a separate package.

#### Constraints

- No login, no account; a truthful User-Agent; requests spaced.
- Never loosen a verification threshold to make FFToday pass.
- Keep the module in the `ProjectionSource` shape.
- Tests must pass without the network. Commit per step, not one commit for everything. Do not push.

#### Report

The check table for each live week; three named players per position with the page's numbers next to the parser's;
the verdict you recorded; the time week 4 appeared; files changed and tests added; anything you could not verify.

---

Proposed row for design 7.2:

| key | website | transport | future weeks | notes |
|---|---|---|---|---|
| fftoday | fftoday.com | HTTP HTML `https://www.fftoday.com/rankings/playerwkproj.php?Season={season}&GameWeek={week}&PosID={id}&LeagueID=107644`; RB and WR follow the "Next Page" link (`&order_by=FFPts&sort_order=DESC&cur_page=1`) | no | PosID 10 QB, 20 RB, 30 WR, 40 TE; LeagueID 107644 is FFToday's PPR preset. Points rescored from the stat columns; week from `td.update`; a week not yet posted returns a "No Player Found" page, which raises. No K (no field-goal distances), no DEF (none published). |

## 9. A separate finding: v2.1's Sleeper QB bias is stale

v2.1 subtracts 4.41 points from every Sleeper QB projection before blending. That number is right for 2025 and wrong
for 2026.

Sleeper minus each other source, QB, median gap over shared players (`probe_sleeper_qb_basis.py`):

| Season | ESPN | FanDuel | FantasyPros | FirstDown |
|---|---|---|---|---|
| 2025, weeks 10 to 16 | +3.30 (205 pairs) | +3.29 (209) | +3.10 (208) | +3.90 (115) |
| 2026 | +0.45 (32, week 4) | -0.28 (21, week-3 fixture) | not available | +1.04 (31, week-3 fixture) |

It is Sleeper that moved. Over shared players, ESPN's QB level barely changed (16.41 in 2025, 16.61 in week 4 of
2026) while Sleeper's fell (19.94 to 17.08). In 2025 the gap was also largest for low-projected QBs (+5.15 below 14
points, +2.10 above 20); in 2026 it is flat.

Implied 2026 Sleeper QB bias: the other source's fitted bias plus the 2026 gap.

| Through | Calculation | Implied bias |
|---|---|---|
| ESPN | 0.997 + 0.45 | 1.45 |
| FanDuel | 0.697 - 0.28 | 0.42 |
| FirstDown | 0.286 + 1.04 | 1.33 |

Effect on mu, checked against the stored week-4 run: in a Sleeper-plus-ESPN run the mean QB mu is
(1.111 x (17.08 - 4.41) + 0.993 x (16.61 - 0.997)) / 2.104 = 14.06, which matches what the run stored. With 1.45 it
would be 15.62, so QBs are 1.56 points low. With five or six sources the shortfall is 1.111 x (4.41 - 1.45) = 3.29
divided by a total weight of 5 to 6, or 0.55 to 0.66. Every team starts one QB, so head-to-head odds mostly cancel
it; team totals and over/under lines do not.

- Pick: patch Sleeper's QB bias to 1.45 before the Wednesday 30 September publish, as a new parameter version with
  a note, not as an edit to v2.1. 1.45 comes from the fullest comparison (ESPN, all 32 QBs, the same week the model
  will use).
- Runner-up: leave v2.1 and state the shift in the run notes.
- What would change my mind: the week-4 accuracy row for Sleeper QBs coming in near +4.4.
- Refitting on 2025 alone would reproduce 4.41, so a refit does not fix this unless it includes 2026 weeks.

Test: after Monday 5 October, read `prediction_accuracy` for (2026, week 4, sleeper.com, QB). Its bias is the mean
of projected minus actual. v2.1 implies about +4.4; I expect about +1.45. v2.1's QB sigma is about 7.5 points at a
typical projection, so with 32 to 40 QBs one week's mean carries about 1.2 to 1.3 points of noise: one week is
suggestive, three are decisive.

The cause is unknown. The 2025 numbers were Sleeper's own `pts_ppr` from its legacy host, `api.sleeper.app`
(`backend/scrapers/scraper_sleeper.py`); the 2026 numbers come from the new host, `api.sleeper.com`
(`pipeline/sources/sleeper.py`). Either the hosts differ, or RotoWire changed its QB method. Two other explanations
do not fit. The legacy scraper's own formula, used only when `pts_ppr` is missing, charges 4 per passing TD and -2
per interception, so it cannot have inflated QBs. And a 6-point passing TD would have widened the gap most for the
best QBs, who throw the most touchdowns, but in 2025 the gap was widest for the weakest (`probe_sleeper_qb_slope.py`).
Test: one request to each host for 2026 week 4, comparing QBs. The local databases hold no 2026 projections before
week 4, so I could not check earlier weeks.

## 10. The existing six: other observations

Terms, from the saved pages:

| Source | Clause | Date |
|---|---|---|
| Sleeper | §11 Prohibited Conduct and §11.3: no crawling, scraping or automated access without written consent | last updated 27 Aug 2026 |
| ESPN (Disney) | §2 License Grant and Restrictions: no robots, spiders, scripts or other automated means | last updated 24 May 2024 |
| FantasySharks | an unnumbered clause: no robot, spider or retrieval device | last revised 16 Jun 2015 |
| FirstDown | §3 Acceptable Use: no automated tools, scrapers or bots without written permission | last updated 27 Jul 2026 |
| FantasyPros | §6 Intellectual Property Information: personal use only; no copying or republishing without written permission; no automated-access clause | effective 1 Jul 2010 |
| FanDuel | not read; it is a sportsbook operator, and robots.txt disallows `/research/api`, the path its scraper intercepts | |

Sleeper's API documentation covers `api.sleeper.app/v1` (leagues, drafts, rosters) for non-commercial use, up to 1000
calls a minute. It never mentions projections; the `api.sleeper.com` projections endpoint is undocumented.

robots.txt:

| Host | What it says |
|---|---|
| www.fantasysharks.com | `Crawl-delay: 60` |
| www.fantasypros.com | `Crawl-delay: 5`; disallows `/ajax/`, `/api/`, `/json/`, `/xml/`, `/nfl/ranker/` |
| www.firstdown.studio | disallows `/admin/` and `/api/` |
| www.fanduel.com | disallows `/api`, `/research/api`, `/lineups/`, `/games/` and more |
| sleeper.com | `Allow: /` |
| api.sleeper.com | every rule commented out |
| lm-api-reads.fantasy.espn.com | answered 403 |

Other observations:

- Four sources (ESPN, FantasyPros, FantasySharks, FirstDown) send a Chrome browser User-Agent, Sleeper sends the
  `requests` default, and FanDuel runs headless Chromium. The first four present the scraper as a browser.
- No source spaces its requests. Honouring FantasySharks' 60 s crawl delay would make the playoffs step's 60
  FantasySharks requests take about an hour.
- Sleeper's `pts_ppr` is not league scoring. QBs: it charges -1 per interception, not -2, which lifts them by a
  median 0.58 in week 4 and 0.60 in week 3 (after that correction the remaining gap to a league rescore has median
  0.000, largest 0.050). DEF: it ignores the yards-allowed brackets, so 19 of 32 defenses are off by -2.05 to +3.01
  (median +0.04, mean +0.27). The fit partly absorbs the QB part through Sleeper's bias.
- Sleeper's per-row `updated_at` is a real batch time: Thu 24 Sep 23:45 UTC in the repository's week-4 fixture, Sun
  27 Sep 23:46 UTC in my weeks 4, 5 and 13, and 28 Sep 03:31 UTC for week 3, three minutes before my fetch.
- FantasySharks (trimmed fixture): QB points a median 4.63 above Sleeper, 2.01 after rescoring (r 0.78); DEF a median
  2.46 above, r 0.56; two players with blank points.
- The playoffs step stores no per-week check details, so a future-week failure leaves only a warning line.
- Doc drift: design 7.3 still lists the old count ranges (QB 20 to 50, RB 40 to 130, WR 50 to 170, TE 20 to 90);
  `verify.py` has QB 20 to 80, RB 40 to 150, WR 50 to 200, TE 20 to 130. `docs/architecture/08` still gives FFToday
  `LeagueID=1` and FantasySharks `scoring=1`.

## 11. Hypotheses and how to test them

| Hypothesis | How to test |
|---|---|
| FFToday posts the next week on Wednesday, between about 02:30 and 19:35 ET | request the week-4 QB page on Wed 30 Sep, morning and evening |
| FFToday's second RB and WR pages have the same table | the engineer's first live fetch |
| FFToday's PPR QB page has the same columns as the default page | the same fetch |
| FFToday still serves 2025 pages | one request: `Season=2025&GameWeek=10&PosID=20&LeagueID=107644` |
| FFToday's Chg arrows mark revisions after posting | fetch one page twice, a day apart, and diff |
| FFToday leaves bye-week players off | the week-5 page (KC and CAR on bye) |
| Sleeper's late week-3 update (stamped 03:31 UTC Monday) makes FFToday's week-3 r look slightly worse than a Wednesday run will | compare FFToday with a Sleeper fetch made the day FFToday posts |
| ESPN's other dropped future weeks (6, 8 to 10, 12, 14) also failed on QB | have the playoffs step store its per-week check details, or fetch those weeks from both sources and run the checks |
| Sleeper's 2026 QB bias is about 1.45, not 4.41 | `prediction_accuracy` for 2026 weeks 4 to 6 |
| The Sleeper QB change comes from the host switch, not from RotoWire | one request per host for 2026 week 4 |
| RotoBaller and Sleeper share inputs | ask RotoBaller along with the permission request; the data alone cannot tell |
| FirstDown builds on betting-market team totals | its site's methodology notes, if any |
| Fleaflicker's projections are its own | ask along with the permission request |
| FFToday and RotoBaller forecasts are their own | no credit on either site; ask if it matters |
| The Football Database has no free projections page | browse its menu once by hand |
| Yahoo has no public projections page | browse once by hand, logged out |

## 12. Scripts and requests

All scripts live in the R1 scratch folder (`research/r1/`). Only the fetch scripts touch the network; everything
else reads saved files or read-only database copies.

| Script | What it did |
|---|---|
| `probe_http.py` | the fetch helper behind every live request: a User-Agent naming TNCasino with a contact URL, at most 12 requests per site, at least 5 s between requests to a site, crawl delays honoured, every request logged to `requests_log.csv` |
| `probe_robots.py`, `probe_terms.py` | fetched robots.txt files and terms pages |
| `probe_robots_local.py`, `probe_terms_local.py`, `terms_sections.py` | read the saved files: the relevant robots lines, the sentences about automated access and reuse, and the numbered section each sits in. Rules containing `*` are printed for a manual check, because Python's robots parser ignores wildcards |
| `fetch_fftoday.py` | fetched FFToday's pages |
| `parse_fftoday.py` | parses an FFToday page into `Projection` rows and raw stat lines |
| `probe_verify_fftoday.py` | ran `verify.py`'s checks on FFToday against Sleeper's week 3; served against rescored points; name forms |
| `probe_fftoday_gap.py` | per-stat gaps against Sleeper; the top-50 WR test |
| `probe_fftoday_bias.py` | FFToday's implied bias through each fitted source; the measured change in mu |
| `probe_starter_depth.py` | page-1 floors against the league's week-4 starters |
| `probe_missing_terms.py` | the points fumbles and two-point plays add in Sleeper's stat lines |
| `probe_wayback_fftoday.py`, `probe_wayback_archive.py` | FFToday's posting times and archive coverage from the Wayback Machine |
| `parse_rotoballer.py`, `probe_verify_rotoballer.py`, `probe_rotoballer_residuals.py`, `probe_rotoballer_text.py` | RotoBaller: parse, checks against Sleeper, the scoring behind Fan Points, the article text |
| `probe_espn_future.py`, `probe_espn_qb_gaps.py`, `probe_espn_byes.py` | ESPN's future weeks against Sleeper; QB spread and the largest gaps; bye-week rows by position |
| `probe_sleeper_scoring.py`, `probe_scoring.py`, `probe_scoring_detail.py` | Sleeper's `pts_ppr` against league scoring, and FantasySharks' served points against its own stat lines |
| `probe_verify_sharks.py` | FantasySharks' fixture against Sleeper, served and rescored |
| `probe_independence.py` | pairs, r and MAD for every pair of sources |
| `probe_sleeper_qb_basis.py`, `probe_sleeper_qb_slope.py`, `probe_sleeper_qb_spread.py` | Sleeper's QB gap to the other sources, 2025 against 2026, by projection band, and the 6-point passing-TD test |
| `probe_sleeper_stamps.py` | Sleeper's per-row update times |
| `inspect_dbs.py`, `inspect_runs.py`, `inspect_accuracy_steps.py` | read-only looks at the local databases and the week-4 run |
| `common_probe.py` | shared helpers: Sleeper rows for a week, `nfl_players`, running the checks |

Requests, all between 23:22 on Sunday 27 September and 00:11 on Monday (ET):

| Site | Requests | What |
|---|---|---|
| fftoday.com | 11 | robots.txt, privacy, sign-up, the default QB page, week 3 RB, WR, TE, K and DEF, weeks 4 and 7 QB |
| sleeper.com | 10 | robots.txt for three hosts, terms, API docs, projections for weeks 3, 4, 5, 7 and 13 |
| rotoballer.com | 8 | robots.txt, two sitemaps, the projections hub, terms, three articles |
| archive.org | 8 | Wayback captures and capture lists for FFToday |
| espn.com | 5 | robots.txt, projections for weeks 4, 5, 7 and 13 |
| subvertadown.com | 4 | robots.txt, sitemap, the weekly defense page, terms |
| 16 other sites | 23 | robots.txt and terms pages, FantasyData's projections page and Fleaflicker's API documentation; one or two each |
