from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from gspread.exceptions import WorksheetNotFound

try:
    from google_storage import GoogleSheetsDB
    from publication_country import COUNTRY_HEADERS, COUNTRY_SHEET
except ImportError:
    from backend.google_storage import GoogleSheetsDB
    from backend.publication_country import COUNTRY_HEADERS, COUNTRY_SHEET


EXPECTED_SPREADSHEET_TITLE = "Article Pipeline Database"


COUNTRY_UPDATES = [
    ("thepeninsulaqatar.com", "The Peninsula", "Qatar", "QA"),
    ("enca.com", "eNCA", "South Africa", "ZA"),
    ("apnews.com", "AP News", "United States", "US"),
    ("dailysabah.com", "Daily Sabah", "Türkiye", "TR"),
    ("gulfnews.com", "Gulf News", "United Arab Emirates", "AE"),
    ("geo.tv", "Geo TV", "Pakistan", "PK"),
    ("winnipegfreepress.com", "Winnipeg Free Press", "Canada", "CA"),
    ("channelstv.com", "Channels TV", "Nigeria", "NG"),
    ("infobae.com", "Infobae", "Argentina", "AR"),
    ("lessentiel.lu", "L'essentiel", "Luxembourg", "LU"),
    ("azpnews.com", "AZP News", "Trinidad and Tobago", "TT"),
]


def build_record(
    lookup_key: str,
    publication: str,
    country: str,
    country_code: str,
    checked_at: str,
) -> list[str]:
    return [
        lookup_key,
        publication,
        country,
        country_code,
        "manual",
        "high",
        "TRUE",
        "resolved",
        f"manual_audit_{checked_at}",
        checked_at,
        "",
    ]


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        description="Upsert audited publication-country overrides."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply changes. Without this flag, only show the planned changes.",
    )
    parser.add_argument(
        "--sheet-id",
        default=os.getenv("GOOGLE_SHEET_ID", "").strip(),
        help="Article Pipeline Database Sheet ID (defaults to GOOGLE_SHEET_ID).",
    )
    args = parser.parse_args()

    checked_at = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    expected = {
        key: build_record(key, publication, country, code, checked_at)
        for key, publication, country, code in COUNTRY_UPDATES
    }

    backend_dir = Path(__file__).resolve().parent
    credentials_path = backend_dir / "credentials.json"

    if not credentials_path.exists():
        raise RuntimeError(f"Credentials file not found: {credentials_path}")
    if not args.sheet_id:
        raise RuntimeError(
            "Google Sheet ID not found. Pass --sheet-id or set GOOGLE_SHEET_ID."
        )

    db = GoogleSheetsDB(
        credentials_path=str(credentials_path),
        sheet_id=args.sheet_id,
    )
    if db.spreadsheet.title != EXPECTED_SPREADSHEET_TITLE:
        raise RuntimeError(
            f"Refusing to use spreadsheet {db.spreadsheet.title!r}; expected "
            f"{EXPECTED_SPREADSHEET_TITLE!r}."
        )

    try:
        worksheet = db.spreadsheet.worksheet(COUNTRY_SHEET)
    except WorksheetNotFound as exc:
        raise RuntimeError(
            f"Required worksheet {COUNTRY_SHEET!r} does not exist."
        ) from exc

    values = worksheet.get_all_values()

    if not values or values[0] != COUNTRY_HEADERS:
        raise RuntimeError("Publication Country Registry headers are invalid")

    rows_by_key: dict[str, list[int]] = {}
    for row_number, row in enumerate(values[1:], start=2):
        key = (row[0] if row else "").strip().lower()
        if key:
            rows_by_key.setdefault(key, []).append(row_number)

    updates: list[dict] = []
    inserts: list[list[str]] = []
    duplicate_rows: list[int] = []

    for key, record in expected.items():
        matching_rows = rows_by_key.get(key, [])
        if matching_rows:
            canonical_row = matching_rows[0]
            updates.append({
                "range": f"A{canonical_row}:K{canonical_row}",
                "values": [record],
            })
            duplicate_rows.extend(matching_rows[1:])
            print(f"UPDATE row {canonical_row}: {key}")
            for duplicate_row in matching_rows[1:]:
                print(f"DELETE duplicate row {duplicate_row}: {key}")
        else:
            inserts.append(record)
            print(f"INSERT: {key}")

    if not args.apply:
        print()
        print("Dry run only. Re-run with --apply to modify the worksheet.")
        return

    if updates:
        worksheet.batch_update(updates, value_input_option="RAW")

    # Delete bottom-up so earlier row numbers remain valid.
    for row_number in sorted(duplicate_rows, reverse=True):
        worksheet.delete_rows(row_number)

    if inserts:
        worksheet.append_rows(inserts, value_input_option="RAW")

    # Read the worksheet back and verify every target record.
    final_values = worksheet.get_all_values()
    final_by_key: dict[str, list[list[str]]] = {}

    for row in final_values[1:]:
        padded = row + [""] * (len(COUNTRY_HEADERS) - len(row))
        normalized = padded[:len(COUNTRY_HEADERS)]
        key = normalized[0].strip().lower()
        if key in expected:
            final_by_key.setdefault(key, []).append(normalized)

    errors = []
    for key, expected_record in expected.items():
        records = final_by_key.get(key, [])
        if len(records) != 1:
            errors.append(f"{key}: expected one row, found {len(records)}")
        elif records[0] != expected_record:
            errors.append(f"{key}: stored values do not match expected values")

    if errors:
        raise RuntimeError("Verification failed:\n" + "\n".join(errors))

    print()
    print(f"Verified {len(expected)} manual country overrides.")


if __name__ == "__main__":
    main()
