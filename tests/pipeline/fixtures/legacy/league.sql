-- league.db as the 2025 notebooks left it: the legacy DDL with a two-team league of made-up rows.
-- Nested Sleeper fields hold Python reprs ('None', single quotes), as str() wrote them.

CREATE TABLE leagues (
  league_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  season TEXT NOT NULL,
  season_type TEXT,
  sport TEXT,
  status TEXT,
  total_rosters INTEGER,
  roster_positions TEXT,
  scoring_settings TEXT,
  settings TEXT,
  previous_league_id TEXT,
  bracket_id TEXT,
  draft_id TEXT,
  avatar TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO leagues
  (league_id, name, season, season_type, sport, status, total_rosters, roster_positions, scoring_settings, settings,
   previous_league_id, bracket_id, draft_id, avatar, created_at, updated_at)
VALUES
  ('L2025', 'Test League', '2025', 'regular', 'nfl', 'complete', 2, '[''QB'', ''WR'', ''DEF'', ''BN'']',
   '{''rec'': 1.0, ''pass_td'': 4.0}', '{''playoff_week_start'': 15, ''playoff_teams'': 2, ''waiver_budget'': 250}',
   NULL, NULL, 'D2025', NULL, '2025-09-01 10:00:00', '2025-09-17 10:00:00');

CREATE TABLE users (
  user_id TEXT PRIMARY KEY,
  username TEXT,
  display_name TEXT,
  avatar TEXT,
  metadata TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO users (user_id, username, display_name, avatar, metadata, created_at, updated_at)
VALUES
  ('U1', NULL, 'alice', NULL, '{''allow_pn'': ''on'', ''team_name'': ''Team Alice''}', '2025-09-01 10:00:00',
   '2025-09-17 10:00:00'),
  ('U2', 'bob', 'bob', NULL, NULL, '2025-09-01 10:00:00', '2025-09-17 10:00:00');

CREATE TABLE rosters (
  roster_id INTEGER,
  league_id TEXT NOT NULL,
  owner_id TEXT,
  co_owners TEXT,
  team_name TEXT,
  starters TEXT,
  players TEXT,
  reserve TEXT,
  taxi TEXT,
  settings TEXT,
  metadata TEXT,
  wins INTEGER DEFAULT 0,
  losses INTEGER DEFAULT 0,
  ties INTEGER DEFAULT 0,
  fpts REAL DEFAULT 0,
  fpts_against REAL DEFAULT 0,
  fpts_decimal REAL DEFAULT 0,
  fpts_against_decimal REAL DEFAULT 0,
  total_moves INTEGER DEFAULT 0,
  waiver_position INTEGER,
  waiver_budget_used INTEGER DEFAULT 0,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (roster_id, league_id),
  FOREIGN KEY (league_id) REFERENCES leagues(league_id),
  FOREIGN KEY (owner_id) REFERENCES users(user_id)
);

INSERT INTO rosters
  (roster_id, league_id, owner_id, co_owners, team_name, starters, players, reserve, taxi, settings, metadata, wins,
   losses, created_at, updated_at)
VALUES
  (1, 'L2025', 'U1', 'None', NULL, '[''7547'', ''SEA'']', '[''7547'', ''SEA'', ''9999'']', 'None', 'None',
   '{''fpts'': 250, ''wins'': 2, ''losses'': 0}', '{''record'': ''WW'', ''streak'': ''2W''}', 2, 0,
   '2025-09-01 10:00:00', '2025-09-17 10:00:00'),
  (2, 'L2025', 'U2', 'None', NULL, '[''4984'']', '[''4984'']', '[]', 'None',
   '{''fpts'': 190, ''wins'': 0, ''losses'': 2}', NULL, 0, 2, '2025-09-01 10:00:00', '2025-09-17 10:00:00');

CREATE TABLE matchups (
  matchup_id TEXT PRIMARY KEY,
  league_id TEXT NOT NULL,
  week INTEGER NOT NULL,
  roster_id INTEGER NOT NULL,
  matchup_id_number INTEGER,
  starters TEXT,
  players TEXT,
  points REAL DEFAULT 0,
  custom_points REAL,
  players_points TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (league_id) REFERENCES leagues(league_id),
  FOREIGN KEY (roster_id, league_id) REFERENCES rosters(roster_id, league_id)
);

INSERT INTO matchups
  (matchup_id, league_id, week, roster_id, matchup_id_number, starters, players, points, custom_points, players_points,
   created_at, updated_at)
VALUES
  ('L2025_3_1', 'L2025', 3, 1, 1, '[''7547'', ''SEA'']', '[''7547'', ''SEA'', ''9999'']', 26.5, NULL,
   '{''7547'': 17.5, ''SEA'': 9.0, ''9999'': 0.0}', '2025-09-17 10:00:00', '2025-09-17 10:00:00');

CREATE TABLE transactions (
  transaction_id TEXT PRIMARY KEY,
  league_id TEXT NOT NULL,
  type TEXT NOT NULL,
  status TEXT,
  roster_ids TEXT,
  settings TEXT,
  metadata TEXT,
  adds TEXT,
  drops TEXT,
  draft_picks TEXT,
  waiver_budget TEXT,
  creator TEXT,
  created BIGINT,
  consenter_ids TEXT,
  status_updated BIGINT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (league_id) REFERENCES leagues(league_id)
);

INSERT INTO transactions
  (transaction_id, league_id, type, status, roster_ids, settings, metadata, adds, drops, draft_picks, waiver_budget,
   creator, created, consenter_ids, status_updated, created_at)
VALUES
  ('T1', 'L2025', 'waiver', 'complete', '[1]', '{''seq'': 0, ''waiver_bid'': 5}', 'None', '{''9999'': 1}', 'None',
   '[]', '[]', 'U1', 1758000000000, '[1]', 1758000000000, '2025-09-17 10:00:00');

CREATE TABLE nfl_players (
  player_id TEXT PRIMARY KEY,
  full_name TEXT,
  first_name TEXT,
  last_name TEXT,
  position TEXT,
  team TEXT,
  number INTEGER,
  age INTEGER,
  height TEXT,
  weight TEXT,
  college TEXT,
  years_exp INTEGER,
  birth_date TEXT,
  birth_city TEXT,
  birth_state TEXT,
  birth_country TEXT,
  high_school TEXT,
  status TEXT,
  active BOOLEAN,
  injury_status TEXT,
  injury_body_part TEXT,
  injury_notes TEXT,
  injury_start_date TEXT,
  practice_participation TEXT,
  depth_chart_position TEXT,
  depth_chart_order INTEGER,
  search_rank INTEGER,
  fantasy_positions TEXT,
  metadata TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO nfl_players
  (player_id, full_name, first_name, last_name, position, team, active, fantasy_positions, metadata, created_at,
   updated_at)
VALUES
  ('SEA', 'SEA Defense', 'SEA', 'Defense', 'DST', 'SEA', 1, '[''DEF'']', 'None', '2025-09-01 10:00:00',
   '2025-09-17 10:00:00'),
  ('7547', 'Amon-Ra St. Brown', 'Amon-Ra', 'St. Brown', 'WR', 'DET', 1, '[''WR'']', '{''rookie_year'': ''2021''}',
   '2025-09-01 10:00:00', '2025-09-17 10:00:00'),
  ('9999', 'Rookie Nobody', 'Rookie', 'Nobody', 'RB', NULL, 1, '[''RB'']', NULL, '2025-09-01 10:00:00',
   '2025-09-17 10:00:00');

CREATE TABLE nfl_schedules (
  schedule_id TEXT PRIMARY KEY,
  season TEXT NOT NULL,
  week INTEGER NOT NULL,
  team TEXT NOT NULL,
  opponent TEXT,
  is_home BOOLEAN,
  is_bye BOOLEAN DEFAULT FALSE,
  game_date TEXT,
  game_time TEXT,
  metadata TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(season, week, team)
);
CREATE INDEX idx_schedules_team_week ON nfl_schedules(team, week);
CREATE INDEX idx_schedules_bye ON nfl_schedules(is_bye);

INSERT INTO nfl_schedules
  (schedule_id, season, week, team, opponent, is_home, is_bye, game_date, game_time, metadata, created_at, updated_at)
VALUES
  ('2025_3_SEA', '2025', 3, 'SEA', 'ARI', NULL, 0, '2025-09-25', '20:15', 'None', '2025-09-01 10:00:00',
   '2025-09-01 10:00:00'),
  ('2025_8_SEA', '2025', 8, 'SEA', NULL, NULL, 1, NULL, NULL, 'None', '2025-09-01 10:00:00', '2025-09-01 10:00:00');

-- Starters have starting_status 1; the notebooks left mu and var NULL for players nobody projected.
CREATE TABLE projections_rosters (
  roster_id INTEGER,
  team_name TEXT,
  sleeper_player_id TEXT,
  first_name TEXT,
  last_name TEXT,
  position TEXT,
  nfl_team TEXT,
  week INTEGER,
  season TEXT,
  mu REAL,
  var REAL,
  starting_status INTEGER,
  timestamp TEXT,
  PRIMARY KEY (sleeper_player_id, week, season)
);

INSERT INTO projections_rosters
  (roster_id, team_name, sleeper_player_id, first_name, last_name, position, nfl_team, week, season, mu, var,
   starting_status, timestamp)
VALUES
  (1, 'Team Alice', '7547', 'Amon-Ra', 'St. Brown', 'WR', 'DET', 3, '2025', 18.0, 136.0, 1,
   '2025-09-17T10:03:00.000000'),
  (1, 'Team Alice', 'SEA', 'SEA', 'Defense', 'DST', 'SEA', 3, '2025', 8.2, 49.0, 1, '2025-09-17T10:03:00.000000'),
  (1, 'Team Alice', '9999', 'Rookie', 'Nobody', 'RB', NULL, 3, '2025', NULL, NULL, 0, '2025-09-17T10:03:00.000000'),
  (2, 'Team Bob', '4984', 'Josh', 'Allen', 'QB', 'BUF', 3, '2025', 21.0, 49.0, 0, '2025-09-17T10:03:00.000000');
