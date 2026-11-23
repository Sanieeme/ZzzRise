import os
import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

def extract_sessions():
    url = os.getenv("DATABASE_URL", "sqlite:///./zzrise.db")
    options = {"check_same_thread": False} if url.startswith("sqlite") else {}
    engine = create_engine(url, connect_args=options)
    query = text("""
        SELECT id, started_at, ended_at, target_minutes, start_method, status
        FROM sleep_sessions
        WHERE status = 'completed' AND ended_at IS NOT NULL
        ORDER BY started_at
    """)
    try:
        with engine.connect() as conn:
            return pd.read_sql(query, conn)
    except Exception as exc:
        if "no such table" in str(exc).lower() or "does not exist" in str(exc).lower():
            return pd.DataFrame(columns=["id", "started_at", "ended_at", "target_minutes", "start_method", "status"])
        raise
