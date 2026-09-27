-- odds.db as the 2025 notebooks left it: the legacy DDL with a few made-up rows.
-- Week 3 was simulated twice (the later run is the one that counts) and week 4 once.

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
  ('seed_1738_20250917_100000', 3, 1, 'Team Alice', 'alice', 110.5, 0.5, '-100', 0.5, '-100', 0, '2025-09-17 10:04:00'),
  ('seed_1738_20250918_100000', 3, 1, 'Team Alice', 'alice', 111.5, 0.5, '-100', 0.5, '-100', 0, '2025-09-18 10:04:00'),
  ('seed_1738_20250918_100000', 3, 2, 'Team Bob', 'bob', 98.5, 0.5, '-100', 0.5, '-100', 0, '2025-09-18 10:04:00'),
  ('seed_1738_20250924_100000', 4, 1, 'Team Alice', 'alice', 105.5, 0.5, '-100', 0.5, '-100', 0, '2025-09-24 10:04:00');

CREATE TABLE betting_odds_matchup_ml (
  run_id TEXT NOT NULL,
  week INTEGER NOT NULL,
  matchup TEXT,
  team1_id INTEGER,
  team1_name TEXT,
  team1_win_prob REAL,
  team1_ml TEXT,
  team2_id INTEGER,
  team2_name TEXT,
  team2_win_prob REAL,
  team2_ml TEXT,
  ties INTEGER,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (run_id, week, team1_id, team2_id)
);

INSERT INTO betting_odds_matchup_ml
  (run_id, week, matchup, team1_id, team1_name, team1_win_prob, team1_ml, team2_id, team2_name, team2_win_prob,
   team2_ml, ties, created_at)
VALUES
  ('seed_1738_20250918_100000', 3, 'Team Alice vs Team Bob', 1, 'Team Alice', 0.55, '-122', 2, 'Team Bob', 0.44,
   '+127', 500, '2025-09-18 10:04:00');

CREATE TABLE betting_odds_matchup_ou (
  run_id TEXT NOT NULL,
  week INTEGER NOT NULL,
  matchup TEXT,
  team1_id INTEGER,
  team1_name TEXT,
  team2_id INTEGER,
  team2_name TEXT,
  line REAL,
  over_prob REAL,
  over_odds TEXT,
  under_prob REAL,
  under_odds TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (run_id, week, team1_id, team2_id)
);

INSERT INTO betting_odds_matchup_ou
  (run_id, week, matchup, team1_id, team1_name, team2_id, team2_name, line, over_prob, over_odds, under_prob,
   under_odds, created_at)
VALUES
  ('seed_1738_20250918_100000', 3, 'Team Alice vs Team Bob', 1, 'Team Alice', 2, 'Team Bob', 210.5, 0.5, '-100', 0.5,
   '-100', '2025-09-18 10:04:00');

CREATE TABLE betting_odds_highest_scorer (
  run_id TEXT NOT NULL,
  week INTEGER NOT NULL,
  team_id INTEGER NOT NULL,
  team_name TEXT,
  owner TEXT,
  count INTEGER,
  probability REAL,
  odds TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (run_id, week, team_id)
);

INSERT INTO betting_odds_highest_scorer
  (run_id, week, team_id, team_name, owner, count, probability, odds, created_at)
VALUES
  ('seed_1738_20250918_100000', 3, 1, 'Team Alice', 'alice', 27500, 0.55, '-122', '2025-09-18 10:04:00');

CREATE TABLE betting_odds_lowest_scorer (
  run_id TEXT NOT NULL,
  week INTEGER NOT NULL,
  team_id INTEGER NOT NULL,
  team_name TEXT,
  owner TEXT,
  count INTEGER,
  probability REAL,
  odds TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (run_id, week, team_id)
);

INSERT INTO betting_odds_lowest_scorer
  (run_id, week, team_id, team_name, owner, count, probability, odds, created_at)
VALUES
  ('seed_1738_20250918_100000', 3, 2, 'Team Bob', 'bob', 27500, 0.55, '-122', '2025-09-18 10:04:00');

CREATE TABLE betting_odds_first_place (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  week INTEGER NOT NULL,
  team_id INTEGER NOT NULL,
  team_name TEXT NOT NULL,
  owner TEXT NOT NULL,
  probability REAL NOT NULL,
  american_odds TEXT NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(run_id, week, team_id)
);

INSERT INTO betting_odds_first_place
  (id, run_id, week, team_id, team_name, owner, probability, american_odds, created_at)
VALUES
  (1, 'standings_3_20250918_100500', 3, 1, 'Team Alice', 'alice', 0.6, '-150', '2025-09-18 10:05:00');

CREATE TABLE betting_odds_make_playoffs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  week INTEGER NOT NULL,
  team_id INTEGER NOT NULL,
  team_name TEXT NOT NULL,
  owner TEXT NOT NULL,
  probability REAL NOT NULL,
  american_odds TEXT NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(run_id, week, team_id)
);

INSERT INTO betting_odds_make_playoffs
  (id, run_id, week, team_id, team_name, owner, probability, american_odds, created_at)
VALUES
  (1, 'standings_3_20250918_100500', 3, 1, 'Team Alice', 'alice', 0.9, '-900', '2025-09-18 10:05:00');

CREATE TABLE standings_probability_matrix (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  week INTEGER NOT NULL,
  team_id INTEGER NOT NULL,
  team_name TEXT NOT NULL,
  owner TEXT NOT NULL,
  position INTEGER NOT NULL,
  probability REAL NOT NULL,
  count INTEGER NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(run_id, week, team_id, position)
);

INSERT INTO standings_probability_matrix
  (id, run_id, week, team_id, team_name, owner, position, probability, count, created_at)
VALUES
  (1, 'standings_3_20250918_100500', 3, 1, 'Team Alice', 'alice', 1, 0.6, 6000, '2025-09-18 10:05:00');

CREATE TABLE team_distribution_curves (
  week INTEGER NOT NULL,
  owner TEXT NOT NULL,
  x_values TEXT NOT NULL,
  density_values TEXT NOT NULL,
  cdf_values TEXT NOT NULL,
  mean REAL NOT NULL,
  p10 REAL NOT NULL,
  p50 REAL NOT NULL,
  p90 REAL NOT NULL,
  n_sims INTEGER NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (week, owner)
);

INSERT INTO team_distribution_curves
  (week, owner, x_values, density_values, cdf_values, mean, p10, p50, p90, n_sims, created_at)
VALUES
  (3, 'alice', '[90, 110, 130]', '[0.01, 0.02, 0.01]', '[0.1, 0.5, 0.9]', 111.0, 92.0, 110.0, 130.0, 50000,
   '2025-09-18 10:04:00'),
  (3, 'bob', '[80, 100, 120]', '[0.01, 0.02, 0.01]', '[0.1, 0.5, 0.9]', 99.0, 81.0, 98.0, 118.0, 50000,
   '2025-09-18 10:04:00'),
  (4, 'alice', '[85, 105, 125]', '[0.01, 0.02, 0.01]', '[0.1, 0.5, 0.9]', 106.0, 87.0, 105.0, 125.0, 50000,
   '2025-09-24 10:04:00');

CREATE TABLE team_matchup_margin_curves (
  week INTEGER NOT NULL,
  team_owner TEXT NOT NULL,
  opponent_owner TEXT NOT NULL,
  team_win_prob REAL NOT NULL,
  opponent_win_prob REAL NOT NULL,
  tie_prob REAL NOT NULL,
  left_x_values TEXT NOT NULL,
  left_y_values TEXT NOT NULL,
  right_x_values TEXT NOT NULL,
  right_y_values TEXT NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (week, team_owner, opponent_owner)
);

INSERT INTO team_matchup_margin_curves
  (week, team_owner, opponent_owner, team_win_prob, opponent_win_prob, tie_prob, left_x_values, left_y_values,
   right_x_values, right_y_values, created_at)
VALUES
  (3, 'alice', 'bob', 0.55, 0.44, 0.01, '[-20, 0]', '[0.01, 0.02]', '[0, 20]', '[0.02, 0.01]', '2025-09-18 10:04:00');
