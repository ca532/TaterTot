import json
import os
import sys
from typing import Dict

import gspread
from google.oauth2.service_account import Credentials

DEFAULTS = {
    "weekly_topic": "Finance",
    "weekly_source_list_name": "",
    "weekly_keywords": "",
}
TOPIC_DEFAULTS = {
    "finance": {
        "topic_name": "Finance",
        "keywords": "",
    },
    "luxury": {
        "topic_name": "Luxury",
        "keywords": "",
    },
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

    topic = values["weekly_topic"].strip() or "Finance"
    topic_cfg = read_topic_config(topic)
    pipeline_topic = infer_pipeline_topic(topic_cfg["topic_name"], topic_cfg["keywords"])
    source_list_name = values["weekly_source_list_name"].strip() or topic_cfg["topic_name"]
    keywords = values["weekly_keywords"].strip() or topic_cfg["keywords"]

    return {
        "weekly_topic": topic_cfg["topic_name"],
        "weekly_pipeline_topic": pipeline_topic,
        "weekly_source_list_name": source_list_name,
        "weekly_keywords": keywords.replace("\r", " ").replace("\n", " ").strip(),
    }


def _clean_keywords(raw: str) -> str:
    out = []
    seen = set()
    for part in (raw or "").replace("\r", " ").replace("\n", " ").split(","):
        value = part.strip()[:60]
        key = value.lower()
        if value and key not in seen:
            seen.add(key)
            out.append(value)
    return ", ".join(out)


def infer_pipeline_topic(topic_name: str, keywords: str = "") -> str:
    text = " ".join([topic_name or "", keywords or ""]).lower()
    luxury_terms = {
        "luxury", "jewellery", "jewelry", "fashion", "watch", "watches", "horology",
        "diamond", "diamonds", "cartier", "tiffany", "bulgari", "chanel", "dior",
        "haute couture", "timepiece", "gem", "gems", "royal", "red carpet",
    }
    return "luxury" if any(term in text for term in luxury_terms) else "finance"


def read_topic_config(topic_name: str) -> Dict[str, str]:
    wanted = (topic_name or "finance").strip().lower()
    ss = _spreadsheet()

    try:
        ws = ss.worksheet(os.getenv("TOPIC_CONFIG_SHEET", "Topic Config"))
        rows = ws.get_all_records()
    except Exception:
        return TOPIC_DEFAULTS.get(wanted, TOPIC_DEFAULTS["finance"])

    for row in rows:
        name = str(row.get("topic_name", "")).strip()
        if name.lower() != wanted:
            continue
        active = str(row.get("active", "TRUE")).strip().upper() != "FALSE"
        if not active:
            break
        return {
            "topic_name": name,
            "keywords": _clean_keywords(str(row.get("keywords", "")).strip()),
        }

    return TOPIC_DEFAULTS.get(wanted, TOPIC_DEFAULTS["finance"])


def print_github_env() -> None:
    cfg = read_config()
    print(f"TOPIC={cfg['weekly_pipeline_topic']}")
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
