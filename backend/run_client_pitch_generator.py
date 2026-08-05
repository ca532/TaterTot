from __future__ import annotations

import os
import re
import time
import uuid
from datetime import datetime, timedelta, timezone

from client_pitch_generator import PITCH_MODEL_NAME, generate_pitch, load_model
from google_storage import GoogleSheetsDB


CLIENT_CONFIG_SHEET = os.getenv("CLIENT_CONFIG_SHEET", "Clients")
CLIENT_PITCH_SHEET = os.getenv("CLIENT_PITCH_SHEET", "Client Pitches")
TREND_SHEET_NAME = os.getenv("TREND_SHEET_NAME", "Trend Signals")

CLIENT_PITCH_HEADERS = [
    "pitch_run_id",
    "generated_at",
    "mode",
    "client_name",
    "pitch_angle",
    "suggested_story",
    "subject_line",
    "supporting_evidence",
    "supporting_urls",
    "model_name",
]

PITCH_RUN_ID = os.getenv("PITCH_RUN_ID", "").strip() or f"pitch-{int(time.time())}-{uuid.uuid4().hex[:8]}"
CLIENT_ID = os.getenv("CLIENT_ID", "").strip()
REQUESTED_MODE = os.getenv("PITCH_MODE", "auto").strip()
TREND_RUN_ID = os.getenv("TREND_RUN_ID", "").strip()
MAX_PITCHES = max(1, min(int(os.getenv("MAX_PITCHES", "5")), 8))


def ensure_pitch_sheet(db: GoogleSheetsDB):
    try:
        ws = db.spreadsheet.worksheet(CLIENT_PITCH_SHEET)
    except Exception:
        ws = db.spreadsheet.add_worksheet(title=CLIENT_PITCH_SHEET, rows=1000, cols=len(CLIENT_PITCH_HEADERS))
    ws.update(range_name="A1:J1", values=[CLIENT_PITCH_HEADERS])
    return ws


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


def load_client(db: GoogleSheetsDB):
    if not CLIENT_ID:
        raise RuntimeError("CLIENT_ID is required")

    ws = db.spreadsheet.worksheet(CLIENT_CONFIG_SHEET)
    for r in ws.get_all_records():
        cid = str(r.get("client_id", "")).strip()
        active = str(r.get("active", "TRUE")).strip().upper() != "FALSE"
        if cid == CLIENT_ID and active:
            topic = str(r.get("topic", "")).strip()
            if not topic:
                raise RuntimeError(
                    f"Client '{cid}' has no configured topic. "
                    "Update the client before generating pitches."
                )
            return {
                "client_id": cid,
                "client_name": str(r.get("client_name", "")).strip(),
                "client_description": str(r.get("client_description", "")).strip(),
                "topic": topic,
            }

    raise RuntimeError(f"Client not found or inactive: {CLIENT_ID}")


def latest_trend_run_id(db: GoogleSheetsDB) -> str:
    try:
        ws = db.spreadsheet.worksheet("Metadata")
        for r in ws.get_all_records():
            if str(r.get("Key", "")).strip() == "latest_trend_run_id":
                return str(r.get("Value", "")).strip()
    except Exception:
        return ""
    return ""


def load_trend_evidence(db: GoogleSheetsDB, run_id: str, topic: str):
    wanted_topic = str(topic or "").strip().lower()
    if not wanted_topic:
        raise RuntimeError("Client topic is required")

    try:
        ws = db.spreadsheet.worksheet(TREND_SHEET_NAME)
        rows = ws.get_all_records()
    except Exception:
        return []

    matching_rows = [
        row
        for row in rows
        if str(row.get("topic", "")).strip().lower() == wanted_topic
    ]

    rid = str(run_id or "").strip()
    if not rid and matching_rows:
        rid = str(matching_rows[-1].get("trend_run_id", "")).strip()
    if not rid:
        return []

    evidence = []
    for r in matching_rows:
        if str(r.get("trend_run_id", "")).strip() != rid:
            continue
        if str(r.get("keyword", "")).strip() == "__NO_TRENDS__":
            continue
        if str(r.get("status", "")).strip() == "no_trends":
            continue

        evidence.append({
            "trend_label": str(r.get("keyword", "")).strip(),
            "current_count": r.get("count_current", ""),
            "historical_average": r.get("baseline_4wk", ""),
            "percent_change": r.get("pct_change", ""),
            "trend_score": r.get("trend_score", ""),
            "publication_count": r.get("publication_count", ""),
            "supporting_urls": str(r.get("supporting_urls", "")).strip(),
            "topic": str(r.get("topic", "")).strip(),
        })

        if len(evidence) >= MAX_PITCHES:
            break

    return evidence


def _row_value(row: dict, *names):
    for name in names:
        value = row.get(name)
        if value not in (None, ""):
            return value
    return ""


def _parse_sheet_datetime(value) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None

    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        parsed = None

    if parsed is None:
        for fmt in (
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
            "%m/%d/%Y %H:%M:%S",
            "%m/%d/%Y",
        ):
            try:
                parsed = datetime.strptime(raw, fmt)
                break
            except ValueError:
                continue

    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def load_recent_coverage_evidence(db: GoogleSheetsDB, client: dict):
    rows = db.articles_sheet.get_all_records()
    client_topic = str(client.get("topic", "")).strip().lower()
    if not client_topic:
        raise RuntimeError("Client topic is required")

    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    client_terms = {
        term.lower()
        for term in re.findall(
            r"[A-Za-z0-9][A-Za-z0-9'-]+",
            client.get("client_description", ""),
        )
        if len(term) >= 4
    }
    valid = []

    for r in rows:
        title = str(_row_value(r, "TITLE", "Title", "title")).strip()
        summary = str(_row_value(r, "SUMMARY", "Summary", "summary")).strip()
        topic = str(_row_value(r, "TOPIC", "Topic", "topic")).strip()
        run_id = str(_row_value(r, "RUN_ID", "Run ID", "run_id")).strip()
        if topic.lower() != client_topic:
            continue

        published_at = _parse_sheet_datetime(
            _row_value(r, "PUBLISHED_DATE", "Published Date", "published_date")
        )
        collected_at = _parse_sheet_datetime(
            _row_value(
                r,
                "CREATED_AT",
                "Created At",
                "created_at",
                "TIMESTAMP",
                "Timestamp",
                "timestamp",
            )
        )
        article_date = published_at or collected_at
        if article_date is None or article_date < cutoff:
            continue

        try:
            score = float(_row_value(r, "SCORE", "Score", "score") or 0)
        except (TypeError, ValueError):
            score = 0.0

        if not title or not summary or score < 4.0:
            continue

        article_text = f"{title} {summary} {topic}".lower()
        overlap = sum(term in article_text for term in client_terms)
        if len(client_terms) >= 2 and overlap < 2:
            continue

        url = str(_row_value(r, "URL", "url")).strip()
        canonical_url = str(
            _row_value(r, "CANONICAL_URL", "Canonical URL", "canonical_url")
        ).strip()

        valid.append({
            "_article_date": article_date,
            "_run_date": collected_at or article_date,
            "_dedupe_key": canonical_url.lower() or url.lower() or title.lower(),
            "run_id": run_id,
            "title": title[:300],
            "publication": str(
                _row_value(r, "PUBLICATION", "Publication", "publication")
            ).strip(),
            "summary": summary[:900],
            "topic": topic,
            "score": score,
            "published_date": article_date.isoformat(),
            "url": canonical_url or url,
        })

    run_dates = {}
    for article in valid:
        article_run_id = article["run_id"]
        if not article_run_id:
            continue
        current = run_dates.get(article_run_id)
        if current is None or article["_run_date"] > current:
            run_dates[article_run_id] = article["_run_date"]

    if run_dates:
        latest_run = max(run_dates, key=run_dates.get)
        valid = [article for article in valid if article["run_id"] == latest_run]

    valid.sort(key=lambda item: (item["_article_date"], item["score"]), reverse=True)
    deduplicated = []
    seen = set()
    for article in valid:
        key = article["_dedupe_key"]
        if key in seen:
            continue
        seen.add(key)
        deduplicated.append(article)
        if len(deduplicated) >= 15:
            break

    packets = []
    for start in range(0, len(deduplicated), 3):
        articles = deduplicated[start:start + 3]
        if len(articles) < 3:
            continue

        clean_articles = [
            {
                key: value
                for key, value in article.items()
                if not key.startswith("_")
            }
            for article in articles
        ]
        packets.append({
            "coverage_basis": (
                "client-relevant summarized articles published or collected "
                "in the last seven days"
            ),
            "window_days": 7,
            "articles": clean_articles,
            "supporting_urls": " | ".join(
                article["url"] for article in clean_articles if article.get("url")
            ),
        })

    return packets


def write_pitch_rows(ws, client, mode, pitches):
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    payload = []

    for p in pitches:
        required_fields = (
            "pitch_angle",
            "suggested_story",
            "subject_line",
            "supporting_evidence",
        )
        if any(not str(p.get(field, "")).strip() for field in required_fields):
            print("[PITCH_ROW_SKIPPED] reason=missing_required_fields")
            continue

        ev = p.get("_evidence", {})
        payload.append([
            PITCH_RUN_ID,
            generated_at,
            mode,
            client.get("client_name", ""),
            p.get("pitch_angle", ""),
            p.get("suggested_story", ""),
            p.get("subject_line", ""),
            p.get("supporting_evidence", ""),
            ev.get("supporting_urls", "") or ev.get("url", ""),
            PITCH_MODEL_NAME,
        ])

    if payload:
        ws.append_rows(payload, value_input_option="USER_ENTERED")


def main():
    print(
        "[PITCH_RUN_START] "
        f"run_id={PITCH_RUN_ID} client_id={CLIENT_ID} mode={REQUESTED_MODE} "
        f"trend_run_id={TREND_RUN_ID or '-'} max_pitches={MAX_PITCHES}"
    )

    db = GoogleSheetsDB()
    upsert_metadata_key(db, "latest_pitch_run_id", PITCH_RUN_ID)
    upsert_metadata_key(db, "latest_pitch_status", "running")
    upsert_metadata_key(db, "latest_pitch_rows_written", "0")

    ws = ensure_pitch_sheet(db)
    client = load_client(db)

    mode = REQUESTED_MODE
    evidence_items = []

    if REQUESTED_MODE in {"auto", "trend_signals"}:
        evidence_items = load_trend_evidence(
            db,
            TREND_RUN_ID,
            client["topic"],
        )

    if REQUESTED_MODE == "trend_signals" and not evidence_items:
        upsert_metadata_key(db, "latest_pitch_status", "failed")
        raise RuntimeError("No trend signals found")

    if REQUESTED_MODE == "auto":
        mode = "trend_signals" if evidence_items else "recent_coverage"

    if mode == "recent_coverage":
        evidence_items = load_recent_coverage_evidence(db, client)
        if not evidence_items:
            upsert_metadata_key(db, "latest_pitch_status", "failed")
            raise RuntimeError("No recent client-relevant summarized coverage found")

    print(f"[PITCH_EVIDENCE] mode={mode} items={len(evidence_items)}")
    model, tokenizer = load_model()

    pitches = []
    generation_failures = []
    for evidence_index, evidence in enumerate(
        evidence_items[:MAX_PITCHES],
        start=1,
    ):
        try:
            pitch = generate_pitch(model, tokenizer, client, evidence, mode)
        except Exception as exc:
            generation_failures.append(str(exc))
            print(
                f"[PITCH_GENERATION_FAILED] evidence_index={evidence_index} "
                f"error={type(exc).__name__}: {exc}"
            )
            continue

        pitch["_evidence"] = evidence
        pitches.append(pitch)

    if not pitches:
        upsert_metadata_key(db, "latest_pitch_status", "failed")
        upsert_metadata_key(db, "latest_pitch_rows_written", "0")
        raise RuntimeError(
            "No valid pitches were generated. "
            f"Failures: {' | '.join(generation_failures)}"
        )

    write_pitch_rows(ws, client, mode, pitches)

    upsert_metadata_key(db, "latest_pitch_run_id", PITCH_RUN_ID)
    upsert_metadata_key(db, "latest_pitch_status", "complete")
    upsert_metadata_key(db, "latest_pitch_rows_written", str(len(pitches)))
    upsert_metadata_key(db, "latest_pitch_client_id", CLIENT_ID)
    upsert_metadata_key(db, "latest_pitch_mode", mode)

    print(f"[PITCH_RUN_COMPLETE] run_id={PITCH_RUN_ID} mode={mode} rows_written={len(pitches)}")


if __name__ == "__main__":
    main()
