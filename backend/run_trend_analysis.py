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


def ensure_trend_sheet(db: GoogleSheetsDB):
    try:
        ws = db.spreadsheet.worksheet(TREND_SHEET_NAME)
    except Exception:
        ws = db.spreadsheet.add_worksheet(title=TREND_SHEET_NAME, rows=2000, cols=12)
        ws.update("A1:I1", [[
            "week_key",
            "keyword",
            "count_current",
            "baseline_4wk",
            "pct_change",
            "trend_score",
            "publication_count",
            "supporting_urls",
            "status",
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


def upsert_week_rows(ws, week_key: str, rows):
    all_vals = ws.get_all_values()
    if len(all_vals) > 1:
        to_delete = []
        for i, row in enumerate(all_vals[1:], start=2):
            if row and row[0] == week_key:
                to_delete.append(i)
        for idx in reversed(to_delete):
            ws.delete_rows(idx)

    payload = [[
        r.week_key,
        r.keyword,
        r.count_current,
        r.baseline_4wk,
        r.pct_change,
        r.trend_score,
        r.publication_count,
        r.supporting_urls,
        r.status,
    ] for r in rows]

    if payload:
        ws.append_rows(payload, value_input_option="USER_ENTERED")


def main():
    db = GoogleSheetsDB()
    ws = ensure_trend_sheet(db)
    articles = load_articles(db)

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

    upsert_week_rows(ws, week_key, rows)
    print(f"Window: start={WINDOW_START_DATE or 'current-month-start'} end={WINDOW_END_DATE or 'current-month-end'} baseline_weeks={BASELINE_WEEKS}")
    print(f"Trend analysis complete for {week_key}: {len(rows)} rows written to '{TREND_SHEET_NAME}'")


if __name__ == "__main__":
    main()
