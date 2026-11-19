from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "processed"
WAREHOUSE = ROOT / "warehouse" / "zzrise_analytics.db"

def load_outputs(clean, daily):
    OUT.mkdir(parents=True, exist_ok=True)
    WAREHOUSE.parent.mkdir(parents=True, exist_ok=True)
    clean.to_csv(OUT / "sleep_sessions_clean.csv", index=False)
    daily.to_csv(OUT / "daily_sleep_summary.csv", index=False)
    with sqlite3.connect(WAREHOUSE) as conn:
        clean.to_sql("fact_sleep_sessions", conn, if_exists="replace", index=False)
        daily.to_sql("fact_daily_sleep_summary", conn, if_exists="replace", index=False)
