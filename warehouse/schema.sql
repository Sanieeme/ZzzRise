-- Analytics tables created by the Pandas loader in the starter pipeline.
CREATE TABLE IF NOT EXISTS fact_sleep_sessions (
  id INTEGER PRIMARY KEY, started_at TEXT, ended_at TEXT, target_minutes INTEGER,
  start_method TEXT, status TEXT, duration_minutes REAL, target_met BOOLEAN, sleep_date TEXT
);
CREATE TABLE IF NOT EXISTS fact_daily_sleep_summary (
  sleep_date TEXT PRIMARY KEY, session_count INTEGER, avg_duration_hours REAL, target_met_count INTEGER
);
