from __future__ import annotations

import os
import time
import uuid
from datetime import datetime, timezone

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
            return {
                "client_id": cid,
                "client_name": str(r.get("client_name", "")).strip(),
                "client_description": str(r.get("client_description", "")).strip(),
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


def load_trend_evidence(db: GoogleSheetsDB, run_id: str):
    rid = (run_id or latest_trend_run_id(db)).strip()
    if not rid:
        return []

    try:
        ws = db.spreadsheet.worksheet(TREND_SHEET_NAME)
        rows = ws.get_all_records()
    except Exception:
        return []

    evidence = []
    for r in rows:
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
        })

        if len(evidence) >= MAX_PITCHES:
            break

    return evidence


def load_recent_coverage_evidence(db: GoogleSheetsDB):
    rows = db.articles_sheet.get_all_records()
    evidence = []

    for r in reversed(rows):
        title = r.get("TITLE") or r.get("Title") or r.get("title") or ""
        summary = r.get("SUMMARY") or r.get("Summary") or r.get("summary") or ""
        if not title or not summary:
            continue

        evidence.append({
            "title": str(title).strip()[:300],
            "publication": str(r.get("PUBLICATION") or r.get("Publication") or r.get("publication") or "").strip(),
            "summary": str(summary).strip()[:900],
            "url": str(r.get("URL") or r.get("url") or "").strip(),
        })

        if len(evidence) >= 12:
            break

    if not evidence:
        return []

    return [{
        "coverage_basis": "recent summarized articles",
        "articles": evidence,
        "supporting_urls": " | ".join([x["url"] for x in evidence if x.get("url")][:5]),
    }]


def write_pitch_rows(ws, client, mode, pitches):
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    payload = []

    for p in pitches:
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
        evidence_items = load_trend_evidence(db, TREND_RUN_ID)

    if REQUESTED_MODE == "trend_signals" and not evidence_items:
        upsert_metadata_key(db, "latest_pitch_status", "failed")
        raise RuntimeError("No trend signals found")

    if REQUESTED_MODE == "auto":
        mode = "trend_signals" if evidence_items else "recent_coverage"

    if mode == "recent_coverage":
        evidence_items = load_recent_coverage_evidence(db)
        if not evidence_items:
            upsert_metadata_key(db, "latest_pitch_status", "failed")
            raise RuntimeError("No recent summarized coverage found")

    print(f"[PITCH_EVIDENCE] mode={mode} items={len(evidence_items)}")
    model, tokenizer = load_model()

    pitches = []
    for evidence in evidence_items[:MAX_PITCHES]:
        pitch = generate_pitch(model, tokenizer, client, evidence, mode)
        pitch["_evidence"] = evidence
        pitches.append(pitch)

    write_pitch_rows(ws, client, mode, pitches)

    upsert_metadata_key(db, "latest_pitch_run_id", PITCH_RUN_ID)
    upsert_metadata_key(db, "latest_pitch_status", "complete")
    upsert_metadata_key(db, "latest_pitch_rows_written", str(len(pitches)))
    upsert_metadata_key(db, "latest_pitch_client_id", CLIENT_ID)
    upsert_metadata_key(db, "latest_pitch_mode", mode)

    print(f"[PITCH_RUN_COMPLETE] run_id={PITCH_RUN_ID} mode={mode} rows_written={len(pitches)}")


if __name__ == "__main__":
    main()
