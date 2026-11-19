from pipeline.extract import extract_sessions
from pipeline.transform import transform_sessions
from pipeline.load import load_outputs

def main():
    raw = extract_sessions()
    clean, daily = transform_sessions(raw)
    load_outputs(clean, daily)
    print(f"ETL complete: extracted={len(raw)}, clean={len(clean)}, daily summaries={len(daily)}")

if __name__ == "__main__":
    main()
