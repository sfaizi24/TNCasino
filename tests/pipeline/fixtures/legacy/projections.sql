-- projections.db as the 2025 notebooks left it: the legacy DDL with a few made-up rows.

CREATE TABLE projections (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_website TEXT NOT NULL,
  week TEXT NOT NULL,
  player_first_name TEXT NOT NULL,
  player_last_name TEXT NOT NULL,
  position TEXT NOT NULL,
  projected_points REAL NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, team TEXT,
  UNIQUE(source_website, week, player_first_name, player_last_name, position)
);
CREATE INDEX idx_source_week ON projections(source_website, week);
CREATE INDEX idx_player_name ON projections(player_first_name, player_last_name);
CREATE INDEX idx_position ON projections(position);

INSERT INTO projections
  (id, source_website, week, player_first_name, player_last_name, position, projected_points, created_at, updated_at, team)
VALUES
  (11, 'fanduel.com', 'Week 3', 'Amon-Ra', 'St. Brown', 'WR', 15.0, '2025-09-17 10:00:00', '2025-09-17 10:00:00', 'DET'),
  (12, 'espn.com', 'Week 3', 'Amon-Ra', 'St. Brown', 'WR', 18.0, '2025-09-17 10:00:00', '2025-09-17 10:00:00', 'DET'),
  (13, 'sleeper.com', 'Week 3', 'Amon-Ra', 'St. Brown', 'WR', 21.0, '2025-09-17 10:00:00', '2025-09-17 10:00:00', 'DET'),
  (14, 'fantasypros.com', 'Week 3', 'Seattle Seahawks', 'Defense', 'DST', 8.2, '2025-09-17 10:00:00', '2025-09-17 10:00:00', 'SEA'),
  (15, 'espn.com', 'Week 12', 'Rookie', 'Nobody', 'RB', 1.5, '2025-11-19 10:00:00', '2025-11-19 10:00:00', NULL);

CREATE TABLE projections_with_sleeper (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  sleeper_player_id TEXT,
  match_method TEXT,
  source_website TEXT,
  week TEXT,
  player_first_name TEXT,
  player_last_name TEXT,
  position TEXT,
  team TEXT,
  projected_points REAL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(source_website, week, player_first_name, player_last_name, position)
);

INSERT INTO projections_with_sleeper
  (id, sleeper_player_id, match_method, source_website, week, player_first_name, player_last_name, position, team,
   projected_points, created_at)
VALUES
  (21, '7547', 'automatic', 'fanduel.com', 'Week 3', 'Amon-Ra', 'St. Brown', 'WR', 'DET', 15.0, '2025-09-17 10:01:00'),
  (22, '7547', 'automatic', 'espn.com', 'Week 3', 'Amon-Ra', 'St. Brown', 'WR', 'DET', 18.0, '2025-09-17 10:01:00'),
  (23, '7547', 'automatic', 'sleeper.com', 'Week 3', 'Amon-Ra', 'St. Brown', 'WR', 'DET', 21.0, '2025-09-17 10:01:00'),
  (24, 'SEA', 'dst_team_match', 'fantasypros.com', 'Week 3', 'Seattle Seahawks', 'Defense', 'DST', 'SEA', 8.2,
   '2025-09-17 10:01:00'),
  (25, NULL, NULL, 'espn.com', 'Week 12', 'Rookie', 'Nobody', 'RB', NULL, 1.5, '2025-11-19 10:01:00');

-- St. Brown's three sources (15, 18, 21) have a spread of 3, so sigma = sqrt((2 * 3)^2 + (1 * 10)^2) = sqrt(136).
CREATE TABLE player_week_stats (
  sleeper_player_id TEXT NOT NULL,
  player_name TEXT,
  position TEXT,
  week INTEGER NOT NULL,
  mu REAL,
  sigma REAL,
  var REAL,
  n_sources INTEGER,
  alpha REAL,
  beta REAL,
  pos_sigma REAL,
  computed_at TEXT,
  PRIMARY KEY (sleeper_player_id, week)
);

-- Two receivers share the name Mike Williams, so a lineup that names him cannot say which one it means.
INSERT INTO player_week_stats
  (sleeper_player_id, player_name, position, week, mu, sigma, var, n_sources, alpha, beta, pos_sigma, computed_at)
VALUES
  ('7547', 'Amon-Ra St. Brown', 'WR', 3, 18.0, 11.661903789690601, 136.0, 3, 2.0, 1.0, 10.0,
   '2025-09-17T10:02:00.000000+00:00'),
  ('SEA', 'SEA Defense', 'DST', 3, 8.2, 7.0, 49.0, 1, 2.0, 1.0, 7.0, '2025-09-17T10:02:00.000000+00:00'),
  ('4984', 'Josh Allen', 'QB', 3, 21.0, 7.0, 49.0, 1, 2.0, 1.0, 7.0, '2025-09-17T10:02:00.000000+00:00'),
  ('5001', 'Mike Williams', 'WR', 3, 9.0, 10.0, 100.0, 1, 2.0, 1.0, 10.0, '2025-09-17T10:02:00.000000+00:00'),
  ('5002', 'Mike Williams', 'WR', 3, 4.0, 10.0, 100.0, 1, 2.0, 1.0, 10.0, '2025-09-17T10:02:00.000000+00:00');

CREATE TABLE team_lineups (
  roster_id INTEGER,
  team_name TEXT,
  owner TEXT,
  record TEXT,
  slot TEXT,
  player_name TEXT,
  position TEXT,
  mu REAL,
  sigma REAL,
  var REAL,
  n_sources INTEGER,
  is_replacement INTEGER,
  week INTEGER,
  season TEXT,
  timestamp TEXT,
  PRIMARY KEY (team_name, week, slot)
);

-- Alice's empty QB slot went to a waiver pickup; nobody in week 3's stats is called BUF Defense.
INSERT INTO team_lineups
  (roster_id, team_name, owner, record, slot, player_name, position, mu, sigma, var, n_sources, is_replacement, week,
   season, timestamp)
VALUES
  (1, 'Team Alice', 'alice', '2-0', 'QB', 'Waiver Pickup', 'QB', 15.0, 8.0, 64.0, 3, 1, 3, '2025',
   '2025-09-17T10:03:00.000000'),
  (1, 'Team Alice', 'alice', '2-0', 'WR1', 'Amon-Ra St. Brown', 'WR', 18.0, 11.661903789690601, 136.0, 3, 0, 3,
   '2025', '2025-09-17T10:03:00.000000'),
  (1, 'Team Alice', 'alice', '2-0', 'DEF', 'SEA Defense', 'DST', 8.2, 7.0, 49.0, 1, 0, 3, '2025',
   '2025-09-17T10:03:00.000000'),
  (2, 'Team Bob', 'bob', '0-2', 'QB', 'Josh Allen', 'QB', 21.0, 7.0, 49.0, 1, 0, 3, '2025',
   '2025-09-17T10:03:00.000000'),
  (2, 'Team Bob', 'bob', '0-2', 'WR1', 'Mike Williams', 'WR', 9.0, 10.0, 100.0, 1, 0, 3, '2025',
   '2025-09-17T10:03:00.000000'),
  (2, 'Team Bob', 'bob', '0-2', 'DEF', 'BUF Defense', 'DST', 6.0, 7.0, 49.0, 1, 0, 3, '2025',
   '2025-09-17T10:03:00.000000');

CREATE TABLE team_projections_summary (
  roster_id INTEGER,
  team_name TEXT,
  owner TEXT,
  record TEXT,
  total_mu REAL,
  combined_sigma REAL,
  total_var REAL,
  waiver_pickups INTEGER,
  week INTEGER,
  season TEXT,
  timestamp TEXT,
  PRIMARY KEY (team_name, week)
);

INSERT INTO team_projections_summary
  (roster_id, team_name, owner, record, total_mu, combined_sigma, total_var, waiver_pickups, week, season, timestamp)
VALUES
  (1, 'Team Alice', 'alice', '2-0', 41.2, 15.78, 249.0, 1, 3, '2025', '2025-09-17T10:03:00.000000');

-- Stray tables the pipeline never reads: a stale copy of an odds table and an empty player_stats.
CREATE TABLE betting_odds_team_ou (
  run_id TEXT NOT NULL,
  week INTEGER NOT NULL,
  team_id INTEGER NOT NULL,
  team_name TEXT,
  owner TEXT,
  line REAL,
  over_prob REAL,
  over_odds TEXT,
  under_prob REAL,
  under_odds TEXT,
  push_count INTEGER,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (run_id, week, team_id)
);

INSERT INTO betting_odds_team_ou
  (run_id, week, team_id, team_name, owner, line, over_prob, over_odds, under_prob, under_odds, push_count, created_at)
VALUES
  ('seed_1738_20250917_100000', 3, 1, 'Team Alice', 'alice', 110.5, 0.5, '-100', 0.5, '-100', 0, '2025-09-17 10:04:00');

CREATE TABLE player_stats (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  week TEXT NOT NULL,
  player_first_name TEXT NOT NULL,
  player_last_name TEXT NOT NULL,
  position TEXT NOT NULL,
  team_owner TEXT,
  mu REAL NOT NULL,
  sigma REAL NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(week, player_first_name, player_last_name, team_owner)
);
CREATE INDEX idx_player_stats_week ON player_stats(week);
CREATE INDEX idx_player_stats_team ON player_stats(team_owner);
