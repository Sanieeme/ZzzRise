"""Run the ZzzRise batch ETL pipeline. Safe to schedule as a recurring job."""
from datetime import datetime, timezone

from pipeline.extract import extract_sessions
from pipeline.transform import transform_sessions
from pipeline.load import load_outputs


def main():
    started = datetime.now(timezone.utc)
    raw = extract_sessions()
    clean, daily = transform_sessions(raw)
    load_outputs(clean, daily)
    finished = datetime.now(timezone.utc)
    elapsed = (finished - started).total_seconds()
    print(
        "ETL complete: "
        f"extracted={len(raw)}, clean={len(clean)}, "
        f"daily_summaries={len(daily)}, duration_seconds={elapsed:.2f}"
    )


if __name__ == "__main__":
    main()
