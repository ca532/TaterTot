import os
from datetime import datetime

from google_storage import GoogleSheetsDB
from trend_analyzer import compute_trends, iso_week_key


TREND_SHEET_NAME = os.getenv("TREND_SHEET_NAME", "Trend Signals")
TARGET_WEEK_KEY = os.getenv("TARGET_WEEK_KEY", "").strip()
TOPIC = os.getenv("TOPIC", "luxury").strip().lower()
WINDOW_START_DATE = os.getenv("WINDOW_START_DATE", "").strip()
WINDOW_END_DATE = os.getenv("WINDOW_END_DATE", "").strip()
BASELINE_WEEKS = int(os.getenv("BASELINE_WEEKS", "4"))
TREND_RUN_ID = os.getenv("TREND_RUN_ID", "").strip() or f"trend-{int(datetime.now().timestamp())}"
WINDOW_MODE = os.getenv("WINDOW_MODE", "").strip() or "current_month"


def ensure_trend_sheet(db: GoogleSheetsDB):
    try:
        ws = db.spreadsheet.worksheet(TREND_SHEET_NAME)
    except Exception:
        ws = db.spreadsheet.add_worksheet(title=TREND_SHEET_NAME, rows=2000, cols=12)
        ws.update("A1:K1", [[
            "trend_run_id",
            "week_key",
            "keyword",
            "count_current",
            "baseline_4wk",
            "pct_change",
            "trend_score",
            "publication_count",
            "supporting_urls",
            "status",
            "window_mode",
        ]])
    return ws


def load_articles(db: GoogleSheetsDB):
    records = db.articles_sheet.get_all_records()
    out = []
    for r in records:
        out.append({
            "id": r.get("ID") or r.get("id"),
            "title": r.get("Title") or r.get("title"),
            "url": r.get("URL") or r.get("url"),
            "publication": r.get("Publication") or r.get("publication"),
            "journalist": r.get("Journalist") or r.get("journalist"),
            "summary": r.get("Summary") or r.get("summary"),
            "collectedDate": r.get("Collected Date") or r.get("collectedDate") or r.get("collected_date"),
        })
    return out


def upsert_run_rows(ws, trend_run_id: str, rows, window_mode: str):
    all_vals = ws.get_all_values()
    if len(all_vals) > 1:
        to_delete = []
        headers = all_vals[0]
        idx = {h: i for i, h in enumerate(headers)}
        ridx = idx.get("trend_run_id", 0)
        for i, row in enumerate(all_vals[1:], start=2):
            if row and ridx < len(row) and row[ridx] == trend_run_id:
                to_delete.append(i)
        for idx in reversed(to_delete):
            ws.delete_rows(idx)

    payload = [[
        trend_run_id,
        r.week_key,
        r.keyword,
        r.count_current,
        r.baseline_4wk,
        r.pct_change,
        r.trend_score,
        r.publication_count,
        r.supporting_urls,
        r.status,
        window_mode,
    ] for r in rows]

    if payload:
        ws.append_rows(payload, value_input_option="USER_ENTERED")


def upsert_metadata_key(db: GoogleSheetsDB, key: str, value: str):
    try:
        try:
            ws = db.spreadsheet.worksheet("Metadata")
        except Exception:
            ws = db.spreadsheet.add_worksheet(title="Metadata", rows=50, cols=3)
            ws.update("A1:C1", [["Key", "Value", "Updated"]])

        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        values = ws.get_all_values()
        row_idx = None
        for i, row in enumerate(values[1:], start=2):
            if row and str(row[0]).strip() == key:
                row_idx = i
                break

        if row_idx:
            ws.update(f"B{row_idx}:C{row_idx}", [[str(value), ts]])
        else:
            ws.append_row([key, str(value), ts], value_input_option="USER_ENTERED")
    except Exception as e:
        print(f"Warning: metadata upsert failed for {key}: {e}")


def main():
    print(
        "[TREND_RUN_START] "
        f"run_id={TREND_RUN_ID} topic={TOPIC} window_mode={WINDOW_MODE} "
        f"target_week_key={TARGET_WEEK_KEY or '-'} start={WINDOW_START_DATE or '-'} "
        f"end={WINDOW_END_DATE or '-'} baseline_weeks={BASELINE_WEEKS}"
    )
    db = GoogleSheetsDB()
    ws = ensure_trend_sheet(db)
    articles = load_articles(db)
    print(f"[TREND_ARTICLES] total_articles_loaded={len(articles)}")

    week_key = TARGET_WEEK_KEY or iso_week_key(datetime.now())
    rows = compute_trends(
        articles=articles,
        target_week_key=week_key,
        topic=TOPIC,
        min_mentions=3,
        min_lift=1.5,
        min_publications=2,
        top_n=20,
        window_start_date=WINDOW_START_DATE,
        window_end_date=WINDOW_END_DATE,
        baseline_weeks=BASELINE_WEEKS,
    )
    print(f"[TREND_COMPUTE_RESULT] run_id={TREND_RUN_ID} week_key={week_key} trend_rows={len(rows)}")

    upsert_run_rows(ws, TREND_RUN_ID, rows, WINDOW_MODE)
    print(f"[TREND_SHEET_WRITE] run_id={TREND_RUN_ID} sheet={TREND_SHEET_NAME} rows_written={len(rows)}")
    upsert_metadata_key(db, "latest_trend_run_id", TREND_RUN_ID)
    upsert_metadata_key(db, "latest_trend_week_key", week_key)
    upsert_metadata_key(db, "latest_trend_window_mode", WINDOW_MODE)
    upsert_metadata_key(db, "latest_trend_window_start", WINDOW_START_DATE or "")
    upsert_metadata_key(db, "latest_trend_window_end", WINDOW_END_DATE or "")
    upsert_metadata_key(db, "latest_trend_topic", TOPIC)
    print(
        "[TREND_METADATA_WRITE] "
        f"latest_trend_run_id={TREND_RUN_ID} latest_trend_week_key={week_key} "
        f"window_mode={WINDOW_MODE} start={WINDOW_START_DATE or '-'} end={WINDOW_END_DATE or '-'} topic={TOPIC}"
    )
    print(
        "[TREND_RUN_SUMMARY] "
        f"run_id={TREND_RUN_ID} week_key={week_key} topic={TOPIC} window_mode={WINDOW_MODE} "
        f"start={WINDOW_START_DATE or '-'} end={WINDOW_END_DATE or '-'} baseline_weeks={BASELINE_WEEKS} rows_written={len(rows)}"
    )
    print(f"Window: start={WINDOW_START_DATE or 'current-month-start'} end={WINDOW_END_DATE or 'current-month-end'} baseline_weeks={BASELINE_WEEKS}")
    print(f"Trend analysis complete for {week_key}: {len(rows)} rows written to '{TREND_SHEET_NAME}' (run_id={TREND_RUN_ID})")


if __name__ == "__main__":
    main()
