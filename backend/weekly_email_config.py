import json
import os
import sys
from typing import Dict

import gspread
from google.oauth2.service_account import Credentials

DEFAULTS = {
    "weekly_topic": "finance",
    "weekly_source_list_name": "",
    "weekly_keywords": "",
}


def _spreadsheet():
    sheet_id = os.getenv("GOOGLE_SHEET_ID")
    creds_json = os.getenv("GOOGLE_CREDENTIALS")

    if not sheet_id:
        raise RuntimeError("GOOGLE_SHEET_ID is required")

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    if os.path.exists("backend/credentials.json"):
        creds = Credentials.from_service_account_file("backend/credentials.json", scopes=scopes)
    elif os.path.exists("credentials.json"):
        creds = Credentials.from_service_account_file("credentials.json", scopes=scopes)
    elif creds_json:
        creds = Credentials.from_service_account_info(json.loads(creds_json), scopes=scopes)
    else:
        raise RuntimeError("GOOGLE_CREDENTIALS or credentials.json is required")

    client = gspread.authorize(creds)
    return client.open_by_key(sheet_id)


def read_config() -> Dict[str, str]:
    values = dict(DEFAULTS)
    ss = _spreadsheet()

    try:
        ws = ss.worksheet("Metadata")
    except Exception:
        return values

    rows = ws.get_all_values()
    for row in rows[1:]:
        if len(row) < 2:
            continue
        key = str(row[0]).strip()
        val = str(row[1]).strip()
        if key in values:
            values[key] = val

    topic = values["weekly_topic"].strip().lower()
    if topic not in {"finance", "luxury"}:
        topic = "finance"

    return {
        "weekly_topic": topic,
        "weekly_source_list_name": values["weekly_source_list_name"].strip(),
        "weekly_keywords": values["weekly_keywords"].replace("\r", " ").replace("\n", " ").strip(),
    }


def print_github_env() -> None:
    cfg = read_config()
    print(f"TOPIC={cfg['weekly_topic']}")
    print(f"SOURCE_LIST_NAME={cfg['weekly_source_list_name']}")
    print(f"KEYWORDS_OVERRIDE={cfg['weekly_keywords']}")


def main() -> int:
    if len(sys.argv) >= 2 and sys.argv[1] == "export-env":
        print_github_env()
        return 0

    print(json.dumps(read_config(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
