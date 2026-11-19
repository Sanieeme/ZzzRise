import pandas as pd

def transform_sessions(raw):
    cols = ["id", "started_at", "ended_at", "target_minutes", "start_method", "status"]
    if raw.empty:
        clean = pd.DataFrame(columns=cols + ["duration_minutes", "target_met", "sleep_date"])
        daily = pd.DataFrame(columns=["sleep_date", "session_count", "avg_duration_hours", "target_met_count"])
        return clean, daily
    df = raw.copy()
    df["started_at"] = pd.to_datetime(df["started_at"], utc=True, errors="coerce")
    df["ended_at"] = pd.to_datetime(df["ended_at"], utc=True, errors="coerce")
    df["target_minutes"] = pd.to_numeric(df["target_minutes"], errors="coerce")
    df = df.dropna(subset=["id", "started_at", "target_minutes"])
    df = df[df["target_minutes"].between(1, 1440)]
    df["duration_minutes"] = (df["ended_at"] - df["started_at"]).dt.total_seconds() / 60
    df.loc[df["ended_at"].isna() | (df["duration_minutes"] < 0), "duration_minutes"] = pd.NA
    df["target_met"] = df["duration_minutes"] >= df["target_minutes"]
    df["sleep_date"] = df["started_at"].dt.date.astype(str)
    completed = df.dropna(subset=["duration_minutes"])
    if completed.empty:
        daily = pd.DataFrame(columns=["sleep_date", "session_count", "avg_duration_hours", "target_met_count"])
    else:
        daily = completed.groupby("sleep_date", as_index=False).agg(
            session_count=("id", "count"),
            avg_duration_hours=("duration_minutes", lambda x: round(x.mean() / 60, 2)),
            target_met_count=("target_met", "sum"))
    return df, daily
