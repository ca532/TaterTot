import json
import os
import re
import threading
import time
import asyncio
import hashlib
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Optional, Literal
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from fastapi.middleware.cors import CORSMiddleware
import requests
import jwt
import gspread
from fastapi import FastAPI, Header, HTTPException, Response, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from jwt import ExpiredSignatureError, PyJWTError
from google.oauth2.service_account import Credentials
from backend.client_coverage_search import get_serpapi_capacity, run_keyword_coverage_report
from backend.publication_metadata_pipeline import run_publication_metadata_pipeline

app = FastAPI()


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    return response

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "https://repo-trigger.onrender.com",
        "https://ca532.github.io",
        "https://rrd.claireadler.com",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ALLOWED_CORS_ORIGINS = {
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "https://repo-trigger.onrender.com",
    "https://ca532.github.io",
    "https://rrd.claireadler.com",
}


def _cors_preflight_response(request: Request) -> Response:
    origin = (request.headers.get("origin") or "").strip()
    response = Response(status_code=204)

    if origin in ALLOWED_CORS_ORIGINS:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Access-Control-Allow-Methods"] = "GET,POST,PUT,DELETE,OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = (
            request.headers.get("access-control-request-headers")
            or "authorization,content-type,accept"
        )
        response.headers["Access-Control-Max-Age"] = "86400"
        response.headers["Vary"] = "Origin"

    return response


@app.options("/{full_path:path}")
def options_preflight(full_path: str, request: Request):
    return _cors_preflight_response(request)


@app.get("/debug/version")
def debug_version():
    return {
        "version": "cors-catchall-2026-07-28",
        "has_options_catchall": True,
    }


@app.get("/debug/routes")
def debug_routes():
    return [
        {
            "path": getattr(route, "path", ""),
            "methods": sorted(list(getattr(route, "methods", []) or [])),
            "name": getattr(route, "name", ""),
        }
        for route in app.routes
    ]

GITHUB_OWNER = os.environ["GITHUB_OWNER"]
GITHUB_REPO = os.environ["GITHUB_REPO"]
GITHUB_WORKFLOW = os.environ.get("GITHUB_WORKFLOW", "collect-articles.yml")
GITHUB_REF = os.environ["GITHUB_REF"]
GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
APP_LOGIN_PASSWORD = os.environ["APP_LOGIN_PASSWORD"]
JWT_SECRET = os.environ["JWT_SECRET"]
JWT_EXPIRES_SECONDS = int(os.environ.get("JWT_EXPIRES_SECONDS", "1800"))
REFRESH_EXPIRES_SECONDS = int(os.environ.get("REFRESH_EXPIRES_SECONDS", str(7 * 24 * 60 * 60)))
REFRESH_COOKIE_NAME = "refresh_token"
REFRESH_COOKIE_SECURE = os.environ.get("REFRESH_COOKIE_SECURE", "true").lower() == "true"
REFRESH_COOKIE_SAMESITE = os.environ.get("REFRESH_COOKIE_SAMESITE", "none")
JWT_ALG = "HS256"

COOLDOWN_SECONDS = int(os.environ.get("PIPELINE_COOLDOWN_SECONDS", "1800"))
STATE_FILE = Path(os.environ.get("PIPELINE_STATE_FILE", "backend/data/pipeline_state.json"))

GITHUB_API_BASE = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}"
GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID")
GOOGLE_CREDENTIALS = os.environ.get("GOOGLE_CREDENTIALS")
STARRED_SHEET_NAME = os.environ.get("STARRED_SHEET_NAME", "Starred Summaries")
SOURCE_CONFIG_SHEET = os.environ.get("SOURCE_CONFIG_SHEET", "Source Lists")
TOPIC_CONFIG_SHEET = os.environ.get("TOPIC_CONFIG_SHEET", "Topic Config")
SOURCE_REPORT_DETAIL_SHEET = os.environ.get("SOURCE_REPORT_DETAIL_SHEET", "Source Validation Details")
CLIENT_CONFIG_SHEET = os.environ.get("CLIENT_CONFIG_SHEET", "Clients")
CLIENT_PITCH_SHEET = os.environ.get("CLIENT_PITCH_SHEET", "Client Pitches")
PITCH_MODEL_NAME = os.environ.get("PITCH_MODEL_NAME", "Qwen/Qwen2.5-1.5B-Instruct")
STARS_DEBUG_LOG = os.environ.get("STARS_DEBUG_LOG", "true").lower() == "true"
DEBUG_PROGRESS = os.environ.get("DEBUG_PROGRESS", "true").lower() == "true"
GITHUB_TERMINAL_POLL_SECONDS = int(os.environ.get("GITHUB_TERMINAL_POLL_SECONDS", "60"))
ALLOW_AUTO_TREND_TRIGGER = os.environ.get("ALLOW_AUTO_TREND_TRIGGER", "false").lower() == "true"

STATE_LOCK = threading.Lock()
_STARS_SHEET = None
_MAIN_SPREADSHEET = None
WS_CLIENTS: set[WebSocket] = set()
WS_CLIENTS_LOCK = asyncio.Lock()
STATUS_CACHE = {
    "status": "idle",
    "phase": "idle",
    "runId": None,
    "conclusion": None,
    "updatedAt": None,
}
STATUS_CACHE_LOCK = asyncio.Lock()
GH_TERMINAL_LOCK = threading.Lock()
GH_TERMINAL_CACHE = {
    "checked_at": 0.0,
    "status": None,
    "conclusion": None,
    "run_id": None,
}
_META_CACHE = {"ts": 0.0, "data": {}}
META_JOBS = {}
META_JOBS_LOCK = threading.Lock()
META_EXECUTOR = ThreadPoolExecutor(max_workers=2)
COVERAGE_JOBS = {}
COVERAGE_JOBS_LOCK = threading.Lock()
COVERAGE_EXECUTOR = ThreadPoolExecutor(max_workers=2)


class TriggerRequest(BaseModel):
    keywords: Optional[List[str]] = None
    topic: str = "Finance"
    list_name: Optional[str] = None


class WeeklyEmailConfigRequest(BaseModel):
    topic: str = "finance"
    source_list_name: Optional[str] = ""
    keywords: Optional[str] = ""


class LoginRequest(BaseModel):
    password: str


class RefreshRequest(BaseModel):
    refresh_token: Optional[str] = None


class StarCreateRequest(BaseModel):
    title: str
    url: str
    publication: str
    summary: str
    author: Optional[str] = "Unknown"
    score: Optional[float] = 0.0
    published_date: Optional[str] = ""
    week_key: Optional[str] = None
    user: Optional[str] = "default"


class StarDeleteRequest(BaseModel):
    article_id: str
    week_key: Optional[str] = None
    user: Optional[str] = "default"


class SourceRowInput(BaseModel):
    base_url: str
    rss_url: Optional[str] = ""


class SourceListCreateRequest(BaseModel):
    list_name: str
    sources: List[SourceRowInput]
    keywords: Optional[str] = ""
    summary_prompt: Optional[str] = ""


class SourceMetadataRunRequest(BaseModel):
    list_name: str


class CoverageScanRequest(BaseModel):
    report_title: str
    mention_terms: str
    search_queries: str
    date_from: Optional[str] = ""
    date_to: Optional[str] = ""
    backlink_domains: Optional[str] = ""


def _meta_job_set(job_id: str, **fields):
    with META_JOBS_LOCK:
        if job_id not in META_JOBS:
            META_JOBS[job_id] = {}
        META_JOBS[job_id].update(fields)


def _meta_job_get(job_id: str):
    with META_JOBS_LOCK:
        return dict(META_JOBS.get(job_id, {}))


def _coverage_job_set(job_id: str, **fields):
    with COVERAGE_JOBS_LOCK:
        if job_id not in COVERAGE_JOBS:
            COVERAGE_JOBS[job_id] = {}
        COVERAGE_JOBS[job_id].update(fields)


def _coverage_job_get(job_id: str):
    with COVERAGE_JOBS_LOCK:
        return dict(COVERAGE_JOBS.get(job_id, {}))


class TrendTriggerRequest(BaseModel):
    topic: str = "luxury"
    list_name: Optional[str] = None
    target_week_key: Optional[str] = None
    window_start_date: Optional[str] = None
    window_end_date: Optional[str] = None
    baseline_weeks: Optional[int] = 4
    window_mode: Optional[Literal["current_week", "current_month", "custom"]] = "current_month"


class ClientConfigRequest(BaseModel):
    client_name: str
    client_description: Optional[str] = ""
    topic: str


class PitchTriggerRequest(BaseModel):
    client_id: str
    mode: Optional[Literal["auto", "trend_signals", "recent_coverage"]] = "auto"
    trend_run_id: Optional[str] = ""
    max_pitches: Optional[int] = 5


def _issue_token(token_type: str, expires_in: int) -> str:
    now = int(time.time())
    payload = {
        "sub": "dashboard-user",
        "typ": token_type,
        "iat": now,
        "exp": now + expires_in,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)


def _issue_access_token() -> str:
    return _issue_token("access", JWT_EXPIRES_SECONDS)


def _issue_refresh_token() -> str:
    return _issue_token("refresh", REFRESH_EXPIRES_SECONDS)


def _decode_token(token: str, expected_type: str) -> dict:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
    except ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except PyJWTError:
        raise HTTPException(status_code=401, detail="Unauthorized")

    if payload.get("typ") != expected_type:
        raise HTTPException(status_code=401, detail="Invalid token type")
    return payload


def _check_auth(authorization: str) -> None:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")
    token = authorization.split(" ", 1)[1].strip()
    _decode_token(token, "access")


def _check_auth_or_refresh_cookie(request: Request, authorization: str) -> None:
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ", 1)[1].strip()
        _decode_token(token, "access")
        return

    cookie_token = request.cookies.get(REFRESH_COOKIE_NAME)
    if cookie_token:
        _decode_token(cookie_token, "refresh")
        return

    raise HTTPException(status_code=401, detail="Unauthorized")


def _check_ws_cookie_auth(websocket: WebSocket) -> None:
    cookie_token = websocket.cookies.get(REFRESH_COOKIE_NAME)
    if not cookie_token:
        raise HTTPException(status_code=401, detail="Unauthorized")
    _decode_token(cookie_token, "refresh")


def _status_to_phase(status: str) -> str:
    phase_map = {
        "queued": "initializing",
        "running": "collecting",
        "in_progress": "collecting",
        "success": "complete",
        "failed": "failed",
        "idle": "idle",
    }
    return phase_map.get(status, "idle")


def _get_week_key(dt: Optional[datetime] = None) -> str:
    d = dt or datetime.now(timezone.utc)
    iso_year, iso_week, _ = d.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def _normalize_url_for_id(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    parsed = urlparse(u)
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]

    qs = parse_qsl(parsed.query, keep_blank_values=False)
    keep = []
    for k, v in qs:
        lk = k.lower()
        if lk.startswith("utm_") or lk in {"gclid", "fbclid", "mc_cid", "mc_eid"}:
            continue
        keep.append((k, v))
    new_query = urlencode(keep, doseq=True)

    path = parsed.path.rstrip("/") or "/"
    scheme = parsed.scheme.lower() if parsed.scheme else "https"
    return urlunparse((scheme, host, path, "", new_query, ""))


def _article_id_from_url(url: str) -> str:
    normalized = _normalize_url_for_id(url)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _load_stars_sheet():
    global _STARS_SHEET
    if _STARS_SHEET is not None:
        return _STARS_SHEET

    if not GOOGLE_SHEET_ID or not GOOGLE_CREDENTIALS:
        raise HTTPException(status_code=500, detail="Missing Google Sheets env (GOOGLE_SHEET_ID/GOOGLE_CREDENTIALS)")

    scope = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(json.loads(GOOGLE_CREDENTIALS), scopes=scope)
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(GOOGLE_SHEET_ID)

    try:
        ws = spreadsheet.worksheet(STARRED_SHEET_NAME)
    except Exception:
        ws = spreadsheet.add_worksheet(title=STARRED_SHEET_NAME, rows=2000, cols=12)
        ws.update("A1:L1", [[
            "star_id", "article_id", "title", "url", "publication", "summary",
            "author", "score", "starred_at", "week_key", "user", "published_date"
        ]])

    headers = ws.row_values(1)
    if len(headers) < 12 or headers[11] != "published_date":
        ws.update_cell(1, 12, "published_date")

    if STARS_DEBUG_LOG:
        try:
            print(f"[STARS_DEBUG] spreadsheet_title={spreadsheet.title!r}")
            print(f"[STARS_DEBUG] worksheet_title={ws.title!r}")
            print(f"[STARS_DEBUG] A1:L1={ws.get('A1:L1')!r}")
        except Exception as e:
            print(f"[STARS_DEBUG] header_probe_error={e}")

    _STARS_SHEET = ws
    return _STARS_SHEET


def _load_main_spreadsheet():
    global _MAIN_SPREADSHEET
    if _MAIN_SPREADSHEET is not None:
        return _MAIN_SPREADSHEET

    if not GOOGLE_SHEET_ID or not GOOGLE_CREDENTIALS:
        raise HTTPException(status_code=500, detail="Missing Google Sheets env (GOOGLE_SHEET_ID/GOOGLE_CREDENTIALS)")

    scope = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(json.loads(GOOGLE_CREDENTIALS), scopes=scope)
    client = gspread.authorize(creds)
    _MAIN_SPREADSHEET = client.open_by_key(GOOGLE_SHEET_ID)
    return _MAIN_SPREADSHEET


def _read_metadata_map() -> dict:
    try:
        spreadsheet = _load_main_spreadsheet()
        ws = spreadsheet.worksheet("Metadata")
        values = ws.get_all_values()
        if len(values) <= 1:
            return {}
        out = {}
        for row in values[1:]:
            if not row or len(row) < 2:
                continue
            key = str(row[0]).strip()
            val = str(row[1]).strip() if len(row) > 1 else ""
            if key:
                out[key] = val
        if DEBUG_PROGRESS:
            print(
                "[WS_STATUS] metadata keys:",
                {k: out.get(k) for k in [
                    "latest_pipeline_phase",
                    "latest_pipeline_current",
                    "latest_pipeline_total",
                    "latest_pipeline_message",
                ]}
            )
        return out
    except Exception:
        return {}


def _stars_rows(ws):
    values = ws.get("A:L")
    if not values:
        return []

    headers = values[0]
    if not headers:
        return []

    rows = []
    for r in values[1:]:
        if not any((c or "").strip() for c in r):
            continue
        row = {}
        for i, h in enumerate(headers):
            if not h:
                continue
            row[h] = r[i] if i < len(r) else ""
        rows.append(row)
    return rows


def _metadata_get(key: str) -> Optional[str]:
    now = time.time()
    if now - _META_CACHE["ts"] < 10 and key in _META_CACHE["data"]:
        return _META_CACHE["data"].get(key)
    try:
        spreadsheet = _load_main_spreadsheet()
        ws = spreadsheet.worksheet("Metadata")
        values = ws.get_all_values()
        data = {}
        for row in values[1:]:
            if row and len(row) >= 2:
                data[str(row[0]).strip()] = str(row[1]).strip()
        _META_CACHE["ts"] = now
        _META_CACHE["data"] = data
        return data.get(key)
    except Exception:
        return None
    return None


def _metadata_upsert(key: str, value: str) -> None:
    try:
        spreadsheet = _load_main_spreadsheet()
        try:
            ws = spreadsheet.worksheet("Metadata")
        except Exception:
            ws = spreadsheet.add_worksheet(title="Metadata", rows=100, cols=3)
            ws.update(range_name="A1:C1", values=[["Key", "Value", "Updated"]])

        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        values = ws.get_all_values()
        row_idx = None
        for i, row in enumerate(values[1:], start=2):
            if row and len(row) >= 1 and str(row[0]).strip() == key:
                row_idx = i
                break

        if row_idx:
            ws.update(range_name=f"B{row_idx}:C{row_idx}", values=[[str(value), ts]])
        else:
            ws.append_row([key, str(value), ts], value_input_option="USER_ENTERED")
    except Exception as e:
        print(f"[TREND_METADATA_TRIGGER_WARN] key={key} err={e}")


def _ensure_ws_headers(ws, headers: list[str]) -> None:
    vals = ws.get_all_values()
    if not vals:
        ws.append_row(headers)
        return
    if vals[0] != headers:
        ws.update("A1", [headers])


def _is_valid_url(u: str) -> bool:
    try:
        p = urlparse((u or "").strip())
        return p.scheme in {"http", "https"} and bool(p.netloc)
    except Exception:
        return False


def _title_from_base_url(base_url: str) -> str:
    host = urlparse((base_url or "").strip()).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    root = host.split(".")[0] if host else "Publication"
    return root.replace("-", " ").replace("_", " ").title() or "Publication"


def _is_explicit_trend_request(request: Request) -> bool:
    v = (request.headers.get("X-Explicit-Trend-Run", "") or "").strip().lower()
    return v in {"1", "true", "yes"}


def _normalize_weekly_keywords(raw: Optional[str]) -> str:
    text = (raw or "").strip()
    if not text:
        return ""

    parts = [p.strip() for p in text.split(",") if p.strip()]
    cleaned = []
    seen = set()
    for p in parts:
        v = p[:60]
        key = v.lower()
        if key not in seen:
            seen.add(key)
            cleaned.append(v)

    if cleaned and len(cleaned) < 5:
        raise HTTPException(status_code=400, detail="At least 5 keywords required when provided")
    if len(cleaned) > 25:
        raise HTTPException(status_code=400, detail="Maximum 25 keywords")

    return ", ".join(cleaned)


def _split_keywords(raw: Optional[str]) -> list[str]:
    text = (raw or "").replace("\r", " ").replace("\n", " ").strip()
    if not text:
        return []
    cleaned = []
    seen = set()
    for part in text.split(","):
        value = part.strip()[:60]
        key = value.lower()
        if value and key not in seen:
            seen.add(key)
            cleaned.append(value)
    return cleaned


TOPIC_CONFIG_HEADERS = [
    "topic_name", "keywords", "summary_prompt", "active", "updated",
    "keyword_weights", "scoring_policy", "minimum_relevance_score",
    "minimum_distinct_keywords", "high_weight_threshold", "lookback_days",
    "max_articles_per_publication", "require_keyword_in_url",
    "weighting_status", "weighting_model",
]


def _ensure_topic_config_headers(ws) -> None:
    values = ws.get_all_values()
    if not values:
        ws.append_row(TOPIC_CONFIG_HEADERS)
        return

    headers = [str(h).strip() for h in values[0]]
    if headers == TOPIC_CONFIG_HEADERS:
        return

    existing_rows = []
    for row in values[1:]:
        if not any(str(c).strip() for c in row):
            continue
        record = {}
        for i, header in enumerate(headers):
            if header:
                record[header] = row[i] if i < len(row) else ""
        existing_rows.append([
            record.get("topic_name", ""),
            record.get("keywords", ""),
            record.get("summary_prompt", ""),
            record.get("active", "TRUE"),
            record.get("updated", ""),
            record.get("keyword_weights", ""),
            record.get("scoring_policy", ""),
            record.get("minimum_relevance_score", "4.0"),
            record.get("minimum_distinct_keywords", "2"),
            record.get("high_weight_threshold", "2.5"),
            record.get("lookback_days", "14"),
            record.get("max_articles_per_publication", "5"),
            record.get("require_keyword_in_url", "FALSE"),
            record.get("weighting_status", "pending" if record.get("keywords") else "not_required"),
            record.get("weighting_model", ""),
        ])

    ws.clear()
    ws.update(range_name="A1:O1", values=[TOPIC_CONFIG_HEADERS])
    if existing_rows:
        ws.append_rows(existing_rows, value_input_option="USER_ENTERED")


def _topic_configs() -> list[dict]:
    try:
        ss = _load_main_spreadsheet()
        try:
            ws = ss.worksheet(TOPIC_CONFIG_SHEET)
        except Exception:
            ws = ss.add_worksheet(title=TOPIC_CONFIG_SHEET, rows=100, cols=15)
            ws.update(range_name="A1:O1", values=[TOPIC_CONFIG_HEADERS])
            return []

        _ensure_topic_config_headers(ws)
        rows = ws.get_all_records()
    except Exception as e:
        print(f"[TOPIC_CONFIG_WARN] could not load topic configs: {e}")
        return []

    configs = []
    for row in rows:
        topic_name = str(row.get("topic_name", "")).strip()
        if not topic_name:
            continue
        active = str(row.get("active", "TRUE")).strip().upper() != "FALSE"
        if not active:
            continue
        keywords = ", ".join(_split_keywords(str(row.get("keywords", "")).strip()))
        configs.append({
            "topic_name": topic_name,
            "keywords": keywords,
            "summary_prompt": str(row.get("summary_prompt", "")).strip(),
            "keyword_weights": str(row.get("keyword_weights", "")).strip(),
            "scoring_policy": str(row.get("scoring_policy", "")).strip(),
            "minimum_relevance_score": row.get("minimum_relevance_score", 4.0),
            "minimum_distinct_keywords": row.get("minimum_distinct_keywords", 2),
            "high_weight_threshold": row.get("high_weight_threshold", 2.5),
            "lookback_days": row.get("lookback_days", 14),
            "max_articles_per_publication": row.get("max_articles_per_publication", 5),
            "require_keyword_in_url": str(row.get("require_keyword_in_url", "FALSE")).upper() == "TRUE",
            "weighting_status": str(row.get("weighting_status", "")).strip(),
            "weighting_model": str(row.get("weighting_model", "")).strip(),
            "keyword_count": len(_split_keywords(keywords)),
            "active": True,
        })

    return sorted(configs, key=lambda x: x["topic_name"].lower())


def _topic_config_for(topic_name: str) -> Optional[dict]:
    wanted = (topic_name or "").strip().lower()
    if not wanted:
        return None
    for cfg in _topic_configs():
        if cfg["topic_name"].strip().lower() == wanted:
            return cfg
    return None


def _topic_config_upsert(topic_name: str, keywords: str, summary_prompt: str = "") -> None:
    topic = (topic_name or "").strip()
    if not topic:
        raise HTTPException(status_code=400, detail="topic_name is required")

    clean_keywords = _normalize_weekly_keywords(keywords)
    clean_prompt = (summary_prompt or "").strip()[:4000]
    ss = _load_main_spreadsheet()
    try:
        ws = ss.worksheet(TOPIC_CONFIG_SHEET)
    except Exception:
        ws = ss.add_worksheet(title=TOPIC_CONFIG_SHEET, rows=100, cols=15)

    _ensure_topic_config_headers(ws)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    values = ws.get_all_values()
    row_idx = None
    for i, row in enumerate(values[1:], start=2):
        if row and str(row[0]).strip().lower() == topic.lower():
            row_idx = i
            break

    row_values = [
        topic, clean_keywords, clean_prompt, "TRUE", today, "", "",
        "4.0", "2", "2.5", "14", "5", "FALSE",
        "pending" if clean_keywords else "not_required", "",
    ]
    if row_idx:
        ws.update(range_name=f"A{row_idx}:O{row_idx}", values=[row_values])
    else:
        ws.append_row(row_values, value_input_option="USER_ENTERED")


CLIENT_CONFIG_HEADERS = [
    "client_id",
    "client_name",
    "client_description",
    "active",
    "updated",
    "topic",
]

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


def _slugify_client_id(name: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower())
    value = value.strip("-")
    return value[:80] or f"client-{int(time.time())}"


def _range_for_headers(headers: list[str]) -> str:
    return f"A1:{chr(64 + len(headers))}1"


def _ensure_simple_headers(ws, headers: list[str]) -> None:
    values = ws.get_all_values()
    if not values:
        ws.update(range_name=_range_for_headers(headers), values=[headers])
        return

    existing = [str(h).strip() for h in values[0]]
    if existing == headers:
        return

    ws.update(range_name=_range_for_headers(headers), values=[headers])


def _clients_ws():
    ss = _load_main_spreadsheet()
    try:
        ws = ss.worksheet(CLIENT_CONFIG_SHEET)
    except Exception:
        ws = ss.add_worksheet(title=CLIENT_CONFIG_SHEET, rows=200, cols=len(CLIENT_CONFIG_HEADERS))
    _ensure_simple_headers(ws, CLIENT_CONFIG_HEADERS)
    return ws


def _client_pitch_ws():
    ss = _load_main_spreadsheet()
    try:
        ws = ss.worksheet(CLIENT_PITCH_SHEET)
    except Exception:
        ws = ss.add_worksheet(title=CLIENT_PITCH_SHEET, rows=1000, cols=len(CLIENT_PITCH_HEADERS))
    _ensure_simple_headers(ws, CLIENT_PITCH_HEADERS)
    return ws


def _client_configs() -> list[dict]:
    try:
        rows = _clients_ws().get_all_records()
    except Exception as e:
        print(f"[CLIENT_CONFIG_WARN] could not load clients: {e}")
        return []

    clients = []
    for row in rows:
        client_name = str(row.get("client_name", "")).strip()
        if not client_name:
            continue

        active = str(row.get("active", "TRUE")).strip().upper() != "FALSE"
        if not active:
            continue

        clients.append({
            "client_id": str(row.get("client_id", "")).strip() or _slugify_client_id(client_name),
            "client_name": client_name,
            "client_description": str(row.get("client_description", "")).strip(),
            "topic": str(row.get("topic", "")).strip(),
            "active": active,
        })

    return sorted(clients, key=lambda x: x["client_name"].lower())


def _client_by_id(client_id: str) -> Optional[dict]:
    wanted = (client_id or "").strip().lower()
    for client in _client_configs():
        if client["client_id"].lower() == wanted:
            return client
    return None


def _upsert_client(req: ClientConfigRequest) -> dict:
    client_name = (req.client_name or "").strip()
    if not client_name:
        raise HTTPException(status_code=400, detail="client_name is required")

    topic_config = _topic_config_for(req.topic)
    if not topic_config:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown or inactive topic: {req.topic}",
        )
    topic = topic_config["topic_name"]

    client_id = _slugify_client_id(client_name)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    row_values = [
        client_id,
        client_name[:120],
        (req.client_description or "").strip()[:2500],
        "TRUE",
        today,
        topic,
    ]

    ws = _clients_ws()
    values = ws.get_all_values()
    row_idx = None
    for i, row in enumerate(values[1:], start=2):
        existing_id = row[0].strip().lower() if len(row) > 0 else ""
        existing_name = row[1].strip().lower() if len(row) > 1 else ""
        if existing_id == client_id.lower() or existing_name == client_name.lower():
            row_idx = i
            break

    if row_idx:
        ws.update(range_name=f"A{row_idx}:F{row_idx}", values=[row_values])
    else:
        ws.append_row(row_values, value_input_option="USER_ENTERED")

    return {
        "client_id": client_id,
        "client_name": client_name,
        "topic": topic,
    }


def _weekly_config_payload() -> dict:
    topic = (_metadata_get("weekly_topic") or "finance").strip()
    if not _topic_config_for(topic):
        topic = "finance"

    return {
        "topic": topic,
        "source_list_name": (_metadata_get("weekly_source_list_name") or "").strip(),
        "keywords": (_metadata_get("weekly_keywords") or "").strip(),
    }


def _has_active_source_rows(list_name: str) -> bool:
    ln = (list_name or "").strip()
    if not ln:
        return False
    ss = _load_main_spreadsheet()
    ws = ss.worksheet(SOURCE_CONFIG_SHEET)
    rows = ws.get_all_records()
    for r in rows:
        if str(r.get("list_name", "")).strip() == ln and str(r.get("active", "TRUE")).upper() == "TRUE":
            return True
    return False


@app.post("/auth/login")
def auth_login(req: LoginRequest, response: Response):
    if req.password != APP_LOGIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    access_token = _issue_access_token()
    refresh_token = _issue_refresh_token()
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=refresh_token,
        httponly=True,
        secure=REFRESH_COOKIE_SECURE,
        samesite=REFRESH_COOKIE_SAMESITE,
        max_age=REFRESH_EXPIRES_SECONDS,
        path="/",
    )
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": JWT_EXPIRES_SECONDS,
    }


@app.post("/auth/refresh")
def auth_refresh(request: Request, req: RefreshRequest, response: Response):
    cookie_token = request.cookies.get(REFRESH_COOKIE_NAME)
    token = cookie_token or req.refresh_token
    if not token:
        raise HTTPException(status_code=401, detail="Missing refresh token")

    _decode_token(token, "refresh")

    new_refresh = _issue_refresh_token()
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=new_refresh,
        httponly=True,
        secure=REFRESH_COOKIE_SECURE,
        samesite=REFRESH_COOKIE_SAMESITE,
        max_age=REFRESH_EXPIRES_SECONDS,
        path="/",
    )

    return {
        "access_token": _issue_access_token(),
        "token_type": "bearer",
        "expires_in": JWT_EXPIRES_SECONDS,
    }


@app.post("/auth/logout")
def auth_logout(response: Response):
    response.delete_cookie(key=REFRESH_COOKIE_NAME, path="/")
    return {"ok": True}


@app.get("/weekly-email-config")
def get_weekly_email_config(authorization: str = Header(default="")):
    _check_auth(authorization)
    return {"ok": True, "config": _weekly_config_payload()}


@app.get("/topic-configs")
def get_topic_configs(authorization: str = Header(default="")):
    _check_auth(authorization)
    return {"ok": True, "topics": _topic_configs()}


@app.put("/weekly-email-config")
def update_weekly_email_config(req: WeeklyEmailConfigRequest, authorization: str = Header(default="")):
    _check_auth(authorization)

    topic = (req.topic or "").strip()
    topic_config = _topic_config_for(topic)
    if not topic_config:
        raise HTTPException(status_code=400, detail=f"topic '{topic}' not found in {TOPIC_CONFIG_SHEET}")

    source_list_name = (req.source_list_name or "").strip() or topic_config["topic_name"]
    keywords = _normalize_weekly_keywords(req.keywords)

    if source_list_name and not _has_active_source_rows(source_list_name):
        raise HTTPException(
            status_code=400,
            detail=f"source_list_name '{source_list_name}' not found or has no active rows",
        )

    _metadata_upsert("weekly_topic", topic)
    _metadata_upsert("weekly_source_list_name", source_list_name)
    _metadata_upsert("weekly_keywords", keywords)

    return {"ok": True, "config": _weekly_config_payload()}


@app.get("/stars")
def get_stars(week_key: Optional[str] = None, user: Optional[str] = "default", authorization: str = Header(default="")):
    _check_auth(authorization)
    wk = week_key or _get_week_key()
    usr = (user or "default").strip()
    ws = _load_stars_sheet()
    if STARS_DEBUG_LOG:
        try:
            print(f"[STARS_DEBUG] get_stars worksheet_title={ws.title!r}")
            print(f"[STARS_DEBUG] get_stars A1:L1={ws.get('A1:L1')!r}")
        except Exception as e:
            print(f"[STARS_DEBUG] get_stars probe error={e}")

    rows = _stars_rows(ws)
    out = []
    for r in rows:
        if str(r.get("week_key", "")).strip() != wk:
            continue
        if str(r.get("user", "default")).strip() != usr:
            continue
        out.append(r)
    return {"week_key": wk, "count": len(out), "stars": out}


@app.get("/stars/current-week")
def get_stars_current_week(user: Optional[str] = "default", authorization: str = Header(default="")):
    return get_stars(week_key=_get_week_key(), user=user, authorization=authorization)


@app.post("/stars")
def create_star(req: StarCreateRequest, authorization: str = Header(default="")):
    _check_auth(authorization)
    ws = _load_stars_sheet()
    if STARS_DEBUG_LOG:
        try:
            print(f"[STARS_DEBUG] create_star worksheet_title={ws.title!r}")
            print(f"[STARS_DEBUG] create_star A1:L1={ws.get('A1:L1')!r}")
        except Exception as e:
            print(f"[STARS_DEBUG] create_star probe error={e}")

    wk = req.week_key or _get_week_key()
    usr = (req.user or "default").strip()
    article_id = _article_id_from_url(req.url)

    rows = _stars_rows(ws)
    for r in rows:
        if (
            str(r.get("article_id", "")).strip() == article_id
            and str(r.get("week_key", "")).strip() == wk
            and str(r.get("user", "default")).strip() == usr
        ):
            return {"ok": True, "star_id": r.get("star_id"), "article_id": article_id, "existing": True}

    star_id = str(uuid.uuid4())
    ws.append_row([
        star_id,
        article_id,
        req.title or "",
        req.url or "",
        req.publication or "",
        req.summary or "",
        req.author or "Unknown",
        float(req.score or 0.0),
        datetime.now(timezone.utc).isoformat(),
        wk,
        usr,
        req.published_date or "",
    ], value_input_option="USER_ENTERED")

    return {"ok": True, "star_id": star_id, "article_id": article_id, "existing": False}


@app.delete("/stars/{star_id}")
def delete_star(star_id: str, authorization: str = Header(default="")):
    _check_auth(authorization)
    ws = _load_stars_sheet()

    values = ws.get("A:L")
    if not values:
        return {"ok": True, "deleted": False}

    for i in range(2, len(values) + 1):
        row = values[i - 1]
        if len(row) > 0 and row[0] == star_id:
            ws.delete_rows(i)
            return {"ok": True, "deleted": True}
    return {"ok": True, "deleted": False}


@app.delete("/stars")
def delete_star_by_article(req: StarDeleteRequest, authorization: str = Header(default="")):
    _check_auth(authorization)
    ws = _load_stars_sheet()

    wk = req.week_key or _get_week_key()
    usr = (req.user or "default").strip()

    values = ws.get_all_values()
    if len(values) <= 1:
        return {"ok": True, "deleted": False}

    headers = values[0]
    idx = {h: n for n, h in enumerate(headers)}
    ai = idx.get("article_id")
    wi = idx.get("week_key")
    ui = idx.get("user")
    if ai is None or wi is None:
        raise HTTPException(status_code=500, detail="Starred sheet missing required columns")

    for i in range(2, len(values) + 1):
        row = values[i - 1]
        row_article = row[ai] if ai < len(row) else ""
        row_week = row[wi] if wi < len(row) else ""
        row_user = row[ui] if ui is not None and ui < len(row) else "default"
        if row_article == req.article_id and row_week == wk and row_user == usr:
            ws.delete_rows(i)
            return {"ok": True, "deleted": True}
    return {"ok": True, "deleted": False}


def _normalize_keywords(raw: Optional[List[str]]) -> List[str]:
    if not raw:
        return []
    cleaned = []
    seen = set()
    for k in raw:
        v = str(k).strip().lower()
        if not v:
            continue
        if len(v) > 80:
            raise HTTPException(status_code=400, detail="Keyword too long")
        if v not in seen:
            seen.add(v)
            cleaned.append(v)
    if cleaned and len(cleaned) < 5:
        raise HTTPException(status_code=400, detail="At least 5 keywords required when provided")
    if len(cleaned) > 25:
        raise HTTPException(status_code=400, detail="Maximum 25 keywords")
    return cleaned


def _normalize_topic(raw: Optional[str]) -> str:
    topic = (raw or "").strip()
    if not topic:
        raise HTTPException(status_code=400, detail="Topic is required")
    if not _topic_config_for(topic):
        raise HTTPException(status_code=400, detail=f"Unknown or inactive topic: {topic}")
    return topic


def _gh_headers() -> dict:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _gh_request(method: str, url: str, **kwargs):
    last_exc = None
    for attempt in range(3):
        try:
            r = requests.request(method, url, headers=_gh_headers(), timeout=20, **kwargs)
            if r.status_code >= 500 and attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            return r
        except requests.RequestException as exc:
            last_exc = exc
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise HTTPException(status_code=502, detail=f"GitHub request failed: {exc}") from exc
    raise HTTPException(status_code=502, detail=f"GitHub request failed: {last_exc}")


def _ensure_state_file() -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not STATE_FILE.exists():
        _write_state(
            {
                "status": "idle",
                "last_triggered_at": None,
                "last_updated_at": int(time.time()),
                "last_run_id": None,
                "last_conclusion": None,
                "queued_requests": 0,
                "last_error": None,
            }
        )


def _read_state() -> dict:
    _ensure_state_file()
    with STATE_FILE.open("r", encoding="utf-8") as f:
        return json.load(f)


def _write_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    state["last_updated_at"] = int(time.time())
    with STATE_FILE.open("w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def _get_latest_run() -> Optional[dict]:
    url = f"{GITHUB_API_BASE}/actions/workflows/{GITHUB_WORKFLOW}/runs?per_page=1"
    r = _gh_request("GET", url)
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=f"GitHub status failed: {r.status_code}")
    runs = r.json().get("workflow_runs", [])
    return runs[0] if runs else None


def _get_run_by_id(run_id: int) -> Optional[dict]:
    url = f"{GITHUB_API_BASE}/actions/runs/{run_id}"
    r = _gh_request("GET", url)
    if r.status_code != 200:
        return None
    return r.json()


def _resolve_dispatched_run_id(previous_run_id: Optional[int], dispatch_ts: int) -> Optional[int]:
    # GitHub may take a few seconds to materialize the new run after dispatch.
    for i in range(20):  # ~40s max
        run = _get_latest_run()
        if run:
            rid = run.get("id")
            created_at = run.get("created_at") or ""
            if DEBUG_PROGRESS:
                print(
                    "[PIPELINE_RUNID_RESOLVE] "
                    f"attempt={i+1}/20 prev={previous_run_id} seen={rid} created_at={created_at}"
                )
            try:
                created_epoch = int(datetime.fromisoformat(created_at.replace("Z", "+00:00")).timestamp())
            except Exception:
                created_epoch = 0
            if rid and rid != previous_run_id and created_epoch >= dispatch_ts - 5:
                return rid
        time.sleep(2)
    return None


def _normalize_status(run: Optional[dict]) -> tuple[str, Optional[str]]:
    if not run:
        return "idle", None

    gh_status = run.get("status")
    gh_conclusion = run.get("conclusion")

    if gh_status in {"queued", "in_progress", "waiting", "pending"}:
        return gh_status if gh_status in {"queued", "in_progress"} else "running", None

    if gh_status == "completed":
        if gh_conclusion == "success":
            return "success", gh_conclusion
        return "failed", gh_conclusion

    return "idle", gh_conclusion


def _maybe_get_terminal_from_github(force: bool = False) -> tuple[Optional[str], Optional[str], Optional[int]]:
    now = time.time()

    with GH_TERMINAL_LOCK:
        checked_at = float(GH_TERMINAL_CACHE.get("checked_at", 0.0) or 0.0)
        if not force and (now - checked_at) < GITHUB_TERMINAL_POLL_SECONDS:
            return (
                GH_TERMINAL_CACHE.get("status"),
                GH_TERMINAL_CACHE.get("conclusion"),
                GH_TERMINAL_CACHE.get("run_id"),
            )

    term_status = None
    term_conclusion = None
    term_run_id = None

    try:
        latest_run = _get_latest_run()
        status, conclusion = _normalize_status(latest_run)
        if status in {"success", "failed"}:
            term_status = status
            term_conclusion = conclusion
            term_run_id = latest_run.get("id") if latest_run else None
    except Exception as e:
        if DEBUG_PROGRESS:
            print(f"[WS_STATUS] github terminal poll error: {e}")

    with GH_TERMINAL_LOCK:
        GH_TERMINAL_CACHE["checked_at"] = now
        GH_TERMINAL_CACHE["status"] = term_status
        GH_TERMINAL_CACHE["conclusion"] = term_conclusion
        GH_TERMINAL_CACHE["run_id"] = term_run_id

    return term_status, term_conclusion, term_run_id


async def _broadcast_status(payload: dict) -> None:
    dead_clients = []
    async with WS_CLIENTS_LOCK:
        clients = list(WS_CLIENTS)
    for ws in clients:
        try:
            await ws.send_text(json.dumps(payload))
        except Exception:
            dead_clients.append(ws)
    if dead_clients:
        async with WS_CLIENTS_LOCK:
            for ws in dead_clients:
                WS_CLIENTS.discard(ws)


async def _refresh_status_once(force_github: bool = False) -> dict:
    metadata = _read_metadata_map()

    pinned_run_id_raw = (metadata.get("current_pipeline_run_id") or "").strip()
    pinned_run = None
    pinned_status = "idle"
    pinned_conclusion = None
    pinned_run_id = None

    if pinned_run_id_raw.isdigit():
        pinned_run_id = int(pinned_run_id_raw)
        pinned_run = _get_run_by_id(pinned_run_id)
        pinned_status, pinned_conclusion = _normalize_status(pinned_run)

    phase = (metadata.get("latest_pipeline_phase") or "").strip().lower()
    current_raw = metadata.get("latest_pipeline_current", "0")
    total_raw = metadata.get("latest_pipeline_total", "0")
    message = metadata.get("latest_pipeline_message", "")

    try:
        current = int(float(current_raw))
    except Exception:
        current = 0
    try:
        total = int(float(total_raw))
    except Exception:
        total = 0

    gh_latest_run = None
    gh_latest_status = None
    gh_latest_conclusion = None
    gh_latest_run_id = None

    if pinned_run_id is not None:
        status = pinned_status
        normalized_phase = (
            "complete" if pinned_status == "success"
            else "failed" if pinned_status == "failed"
            else "collecting" if pinned_status in {"running", "in_progress"}
            else "initializing" if pinned_status == "queued"
            else "idle"
        )
        if not message and status == "success":
            message = "Pipeline completed"
        if not message and status == "failed":
            message = f"Pipeline {pinned_conclusion or 'failed'}"
    else:
        # Fallback to GitHub latest run when no pinned run id is available
        try:
            if force_github or True:
                gh_latest_run = _get_latest_run()
                gh_latest_status, gh_latest_conclusion = _normalize_status(gh_latest_run)
                gh_latest_run_id = gh_latest_run.get("id") if gh_latest_run else None
        except Exception as e:
            if DEBUG_PROGRESS:
                print(f"[WS_STATUS] github latest fallback error: {e}")

        if gh_latest_status:
            status = gh_latest_status
            normalized_phase = (
                "complete" if gh_latest_status == "success"
                else "failed" if gh_latest_status == "failed"
                else "collecting" if gh_latest_status in {"running", "in_progress"}
                else "initializing" if gh_latest_status == "queued"
                else "idle"
            )
            if not message and status == "success":
                message = "Pipeline completed"
            if not message and status == "failed":
                message = f"Pipeline {gh_latest_conclusion or 'failed'}"
        else:
            # Last-resort metadata mapping
            if phase in {"complete", "completed", "success", "done"}:
                status = "success"
                normalized_phase = "complete"
            elif phase in {"failed", "error"}:
                status = "failed"
                normalized_phase = "failed"
            elif phase in {"initializing", "collecting", "summarizing", "saving"}:
                status = "running"
                normalized_phase = phase
            elif phase:
                status = "running"
                normalized_phase = phase
            else:
                status = "idle"
                normalized_phase = "idle"

    meta_run_id = (metadata.get("latest_pipeline_run_id") or "").strip()
    if pinned_run_id is not None and meta_run_id and meta_run_id != str(pinned_run_id):
        current = 0
        total = 0
        if status in {"running", "queued", "in_progress"}:
            message = "Pipeline is running (progress sync pending)..."

    with STATE_LOCK:
        state = _read_state()
        state["status"] = status
        if pinned_run_id is not None:
            state["last_run_id"] = pinned_run_id
        elif gh_latest_run_id is not None:
            state["last_run_id"] = gh_latest_run_id
        state["last_conclusion"] = pinned_conclusion
        if pinned_run_id is None and gh_latest_conclusion is not None:
            state["last_conclusion"] = gh_latest_conclusion
        _write_state(state)
        updated_at = state.get("last_updated_at")

    payload = {
        "status": status,
        "phase": normalized_phase,
        "current": current,
        "total": total,
        "message": message,
        "runId": (
            pinned_run_id
            if pinned_run_id is not None
            else gh_latest_run_id if gh_latest_run_id is not None
            else state.get("last_run_id")
        ),
        "conclusion": (
            pinned_conclusion
            if pinned_conclusion is not None
            else gh_latest_conclusion
        ),
        "updatedAt": updated_at,
    }
    if DEBUG_PROGRESS:
        print(
            "[WS_STATUS] payload:",
            {
                "status": payload.get("status"),
                "phase": payload.get("phase"),
                "current": payload.get("current"),
                "total": payload.get("total"),
                "message": payload.get("message"),
                "runId": payload.get("runId"),
            }
        )

    async with STATUS_CACHE_LOCK:
        STATUS_CACHE.update(payload)
    return payload


async def _status_refresher_loop() -> None:
    slow_mode = False
    while True:
        try:
            payload = await _refresh_status_once()
            await _broadcast_status(payload)

            phase = str((payload or {}).get("phase", "")).lower()
            if phase in {"collecting", "summarizing", "saving", "complete", "failed"}:
                slow_mode = True
        except Exception as e:
            print(f"[WS_STATUS] refresh error: {e}")
        await asyncio.sleep(60 if slow_mode else 5)


@app.on_event("startup")
async def startup_status_refresher():
    asyncio.create_task(_status_refresher_loop())


@app.post("/pipeline/trigger")
def trigger_pipeline(req: TriggerRequest, response: Response, authorization: str = Header(default="")):
    _check_auth(authorization)

    with STATE_LOCK:
        state = _read_state()
        latest_run = _get_latest_run()
        normalized_status, conclusion = _normalize_status(latest_run)
        prev_run_id = latest_run.get("id") if latest_run else None
        now = int(time.time())

        # Sync persisted state with GitHub state first
        state["status"] = normalized_status
        state["last_conclusion"] = conclusion
        state["last_run_id"] = latest_run.get("id") if latest_run else state.get("last_run_id")

        # Idempotency / single-active-run enforcement
        if normalized_status in {"queued", "running", "in_progress"}:
            state["queued_requests"] = int(state.get("queued_requests", 0)) + 1
            _write_state(state)
            response.status_code = 202
            return {
                "ok": True,
                "state": "already_running",
                "message": "A run is already active. Request recorded as queued.",
                "queuedRequests": state["queued_requests"],
                "activeRunId": state["last_run_id"],
            }

        # Cooldown enforcement
        last_triggered = state.get("last_triggered_at")
        if last_triggered is not None:
            elapsed = now - int(last_triggered)
            if elapsed < COOLDOWN_SECONDS:
                retry_after = COOLDOWN_SECONDS - elapsed
                _write_state(state)
                raise HTTPException(
                    status_code=429,
                    detail=f"Cooldown active. Try again in {retry_after} seconds.",
                    headers={"Retry-After": str(retry_after)},
                )

        # Trigger dispatch
        url = f"{GITHUB_API_BASE}/actions/workflows/{GITHUB_WORKFLOW}/dispatches"
        topic = _normalize_topic(req.topic)
        keywords = _normalize_keywords(req.keywords)
        list_name = (req.list_name or "").strip()
        if list_name and not _has_active_source_rows(list_name):
            raise HTTPException(status_code=400, detail=f"list_name '{list_name}' not found or has no active rows")
        body = {
            "ref": GITHUB_REF,
            "inputs": {
                "test_mode": "false",
                "keywords": ",".join(keywords),
                "topic": topic,
                "list_name": list_name,
            },
        }


        r = _gh_request("POST", url, json=body)
        if r.status_code != 204:
            state["status"] = "failed"
            state["last_error"] = f"GitHub dispatch failed: {r.status_code} {r.text}"
            _write_state(state)
            raise HTTPException(status_code=502, detail=state["last_error"])

        state["status"] = "queued"
        state["last_triggered_at"] = now
        state["last_error"] = None
        state["queued_requests"] = 0
        new_run_id = _resolve_dispatched_run_id(prev_run_id, now)
        if new_run_id:
            state["last_run_id"] = new_run_id
            _metadata_upsert("current_pipeline_run_id", str(new_run_id))
            _metadata_upsert("latest_pipeline_status", "queued")
        else:
            print("[PIPELINE_TRIGGER_WARN] Could not resolve new run id after dispatch window")
        _write_state(state)

    return {"ok": True, "state": "queued", "message": "Pipeline triggered", "runId": new_run_id}


@app.get("/pipeline/status")
async def pipeline_status(authorization: str = Header(default="")):
    _check_auth(authorization)
    async with STATUS_CACHE_LOCK:
        cached = dict(STATUS_CACHE)

    with STATE_LOCK:
        current_state = _read_state()
        expected_run_id = current_state.get("last_run_id")

    cached_run_id = cached.get("runId")
    if expected_run_id and str(cached_run_id or "") != str(expected_run_id):
        cached = await _refresh_status_once(force_github=True)

    if not cached.get("updatedAt"):
        cached = await _refresh_status_once(force_github=True)

    # Heal stale cache: running without run id is invalid
    if cached.get("status") in {"running", "queued", "in_progress"} and not cached.get("runId"):
        cached = await _refresh_status_once(force_github=True)

    if cached.get("status") in {"success", "failed"}:
        terminal_status = cached.get("status") or ""
        terminal_phase = "complete" if terminal_status == "success" else "failed"
        terminal_message = (
            "Pipeline completed"
            if terminal_status == "success"
            else f"Pipeline {cached.get('conclusion') or 'failed'}"
        )

        _metadata_upsert("latest_pipeline_status", terminal_status)
        _metadata_upsert("latest_pipeline_phase", terminal_phase)
        _metadata_upsert("latest_pipeline_message", terminal_message)
        _metadata_upsert("latest_pipeline_current", "4")
        _metadata_upsert("latest_pipeline_total", "4")
        _metadata_upsert("current_pipeline_run_id", "")

    with STATE_LOCK:
        state = _read_state()
        return {
            "status": cached.get("status", state.get("status", "idle")),
            "conclusion": cached.get("conclusion", state.get("last_conclusion")),
            "runId": cached.get("runId", state.get("last_run_id")),
            "queuedRequests": state.get("queued_requests", 0),
            "lastTriggeredAt": state.get("last_triggered_at"),
            "lastUpdatedAt": cached.get("updatedAt", state.get("last_updated_at")),
            "lastError": state.get("last_error"),
            "createdAt": None,
            "updatedAt": None,
        }


@app.websocket("/pipeline/ws")
async def pipeline_ws(websocket: WebSocket):
    await websocket.accept()
    try:
        _check_ws_cookie_auth(websocket)
    except Exception:
        await websocket.close(code=4401)
        return

    async with WS_CLIENTS_LOCK:
        WS_CLIENTS.add(websocket)

    try:
        async with STATUS_CACHE_LOCK:
            cached = dict(STATUS_CACHE)
        if not cached.get("updatedAt"):
            cached = await _refresh_status_once()
        await websocket.send_text(json.dumps(cached))

        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        async with WS_CLIENTS_LOCK:
            WS_CLIENTS.discard(websocket)


@app.get("/pipeline/latest-artifact")
def latest_artifact(authorization: str = Header(default="")):
    _check_auth(authorization)

    runs_url = f"{GITHUB_API_BASE}/actions/workflows/{GITHUB_WORKFLOW}/runs?per_page=10"
    rr = _gh_request("GET", runs_url)
    if rr.status_code != 200:
        raise HTTPException(status_code=502, detail=f"GitHub runs failed: {rr.status_code}")

    runs = rr.json().get("workflow_runs", [])
    successful = next((r for r in runs if r.get("conclusion") == "success"), None)
    if not successful:
        return {"downloadURL": None}

    artifacts_url = f"{GITHUB_API_BASE}/actions/runs/{successful['id']}/artifacts"
    ar = _gh_request("GET", artifacts_url)
    if ar.status_code != 200:
        raise HTTPException(status_code=502, detail=f"GitHub artifacts failed: {ar.status_code}")

    artifacts = ar.json().get("artifacts", [])
    artifact = next(
        (a for a in artifacts if str(a.get("name", "")).startswith("Reading-Roundup-")),
        None,
    )
    if not artifact:
        return {"downloadURL": None}

    return {
        "downloadURL": artifact.get("archive_download_url"),
        "name": artifact.get("name"),
        "createdAt": artifact.get("created_at"),
        "runNumber": successful.get("run_number"),
    }


@app.get("/pipeline/download-artifact")
def download_artifact(run_id: int, authorization: str = Header(default="")):
    _check_auth(authorization)

    run = _get_run_by_id(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Workflow run not found")

    if run.get("head_branch") != GITHUB_REF:
        raise HTTPException(
            status_code=409,
            detail=f"Run {run_id} does not belong to branch {GITHUB_REF}",
        )

    if run.get("status") != "completed":
        raise HTTPException(status_code=409, detail="The current PDF is still being generated")

    if run.get("conclusion") != "success":
        raise HTTPException(status_code=409, detail="This pipeline run did not complete successfully")

    artifacts_url = f"{GITHUB_API_BASE}/actions/runs/{run_id}/artifacts"
    ar = _gh_request("GET", artifacts_url)
    if ar.status_code != 200:
        raise HTTPException(status_code=502, detail=f"GitHub artifacts failed: {ar.status_code}")

    artifacts = ar.json().get("artifacts", [])
    artifact = next(
        (
            a for a in artifacts
            if str(a.get("name", "")).startswith("Reading-Roundup-")
            and not a.get("expired", False)
        ),
        None,
    )
    if not artifact:
        raise HTTPException(status_code=404, detail="This run has no available PDF artifact")

    download_url = artifact.get("archive_download_url")
    if not download_url:
        raise HTTPException(status_code=404, detail="Artifact download URL unavailable")

    try:
        gh_resp = requests.get(download_url, headers=_gh_headers(), stream=True, timeout=60)
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Artifact download failed: {exc}") from exc

    if gh_resp.status_code != 200:
        detail = None
        try:
            detail = gh_resp.json()
        except ValueError:
            detail = gh_resp.text
        raise HTTPException(status_code=502, detail=f"Artifact download failed: {gh_resp.status_code} {detail}")

    artifact_name = artifact.get("name") or f"Reading-Roundup-{run_id}"
    file_name = f"{artifact_name}.zip"
    return StreamingResponse(
        gh_resp.iter_content(chunk_size=1024 * 64),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{file_name}"'},
    )


@app.get("/pipeline/latest-result")
async def latest_result(authorization: str = Header(default="")):
    _check_auth(authorization)

    status = await pipeline_status(authorization=authorization)
    artifact = latest_artifact(authorization=authorization)

    return {
        "status": status.get("status"),
        "conclusion": status.get("conclusion"),
        "runId": status.get("runId"),
        "updatedAt": status.get("updatedAt"),
        "artifact": artifact,
    }


@app.get("/trends/current-week")
def get_trends_current_week(authorization: str = Header(default="")):
    _check_auth(authorization)
    wk = _get_week_key()
    spreadsheet = _load_main_spreadsheet()
    try:
        ws = spreadsheet.worksheet("Trend Signals")
    except Exception:
        return {"week_key": wk, "count": 0, "trends": []}

    values = ws.get_all_values()
    if len(values) <= 1:
        return {"week_key": wk, "count": 0, "trends": []}

    headers = values[0]
    idx = {h: i for i, h in enumerate(headers)}
    trends = []
    for row in values[1:]:
        if not row:
            continue
        week = row[idx.get("week_key", 0)] if idx.get("week_key", 0) < len(row) else ""
        if week != wk:
            continue
        trends.append({
            "week_key": week,
            "keyword": row[idx.get("keyword", 1)] if idx.get("keyword", 1) < len(row) else "",
            "count_current": row[idx.get("count_current", 2)] if idx.get("count_current", 2) < len(row) else "0",
            "baseline_4wk": row[idx.get("baseline_4wk", 3)] if idx.get("baseline_4wk", 3) < len(row) else "0",
            "pct_change": row[idx.get("pct_change", 4)] if idx.get("pct_change", 4) < len(row) else "0",
            "trend_score": row[idx.get("trend_score", 5)] if idx.get("trend_score", 5) < len(row) else "0",
            "publication_count": row[idx.get("publication_count", 6)] if idx.get("publication_count", 6) < len(row) else "0",
            "supporting_urls": row[idx.get("supporting_urls", 7)] if idx.get("supporting_urls", 7) < len(row) else "",
            "status": row[idx.get("status", 8)] if idx.get("status", 8) < len(row) else "trending",
        })

    return {"week_key": wk, "count": len(trends), "trends": trends}


@app.post("/trends/trigger")
def trigger_trend_analysis(req: TrendTriggerRequest, request: Request, response: Response, authorization: str = Header(default="")):
    _check_auth(authorization)
    if not ALLOW_AUTO_TREND_TRIGGER and not _is_explicit_trend_request(request):
        raise HTTPException(
            status_code=403,
            detail="Trend dispatch blocked unless explicitly requested (missing X-Explicit-Trend-Run header)."
        )
    phase = (_metadata_get("latest_pipeline_phase") or "").strip().lower()
    if phase in {"initializing", "collecting", "summarizing", "running", "in_progress", "queued"}:
        raise HTTPException(
            status_code=409,
            detail="Collect/summarize pipeline is still running. Run trend analysis after pipeline completion."
        )
    workflow = "trend-analysis.yml"
    url = f"{GITHUB_API_BASE}/actions/workflows/{workflow}/dispatches"

    topic = (req.topic or "").strip().lower()
    list_name = (req.list_name or "").strip()
    if not topic:
        raise HTTPException(status_code=400, detail="Topic is required")
    week_key = (req.target_week_key or "").strip()
    window_start_date = (req.window_start_date or "").strip()
    window_end_date = (req.window_end_date or "").strip()
    baseline_weeks = int(req.baseline_weeks or 4)
    trend_run_id = f"trend-{int(time.time())}-{uuid.uuid4().hex[:8]}"
    window_mode = (req.window_mode or "current_month").strip().lower()
    if window_mode not in {"current_week", "current_month", "custom"}:
        raise HTTPException(status_code=400, detail="Invalid window_mode")
    print(
        "[TREND_TRIGGER] "
        f"run_id={trend_run_id} topic={topic} window_mode={window_mode} "
        f"week_key={week_key or '-'} start={window_start_date or '-'} end={window_end_date or '-'} "
        f"baseline_weeks={baseline_weeks} ref={GITHUB_REF}"
    )
    print(
        "[TREND_TRIGGER_PAYLOAD] "
        f"run_id={trend_run_id} topic={topic} window_mode={window_mode} "
        f"week_key={week_key or '-'} start={window_start_date or '-'} end={window_end_date or '-'} "
        f"baseline_weeks={baseline_weeks} ref={GITHUB_REF}"
    )
    _metadata_upsert("latest_trend_run_id", trend_run_id)
    _metadata_upsert("latest_trend_status", "running")
    _metadata_upsert("latest_trend_rows_written", "0")
    _metadata_upsert("latest_trend_week_key", week_key or "")
    _metadata_upsert("latest_trend_window_mode", window_mode)
    _metadata_upsert("latest_trend_window_start", window_start_date or "")
    _metadata_upsert("latest_trend_window_end", window_end_date or "")
    _metadata_upsert("latest_trend_topic", topic)

    body = {
        "ref": GITHUB_REF,
        "inputs": {
            "target_week_key": week_key,
            "topic": topic,
            "list_name": list_name,
            "window_start_date": window_start_date,
            "window_end_date": window_end_date,
            "baseline_weeks": str(baseline_weeks),
            "trend_run_id": trend_run_id,
            "window_mode": window_mode,
        },
    }

    r = _gh_request("POST", url, json=body)
    print(
        "[TREND_TRIGGER_RESULT] "
        f"run_id={trend_run_id} status_code={r.status_code} "
        f"workflow=trend-analysis.yml repo={GITHUB_OWNER}/{GITHUB_REPO}"
    )
    print(f"[TREND_TRIGGER_ACK] run_id={trend_run_id} status_code={r.status_code}")
    if r.status_code != 204:
        _metadata_upsert("latest_trend_status", "failed")
        raise HTTPException(status_code=502, detail=f"GitHub trend dispatch failed: {r.status_code} {r.text}")

    response.status_code = 202
    return {
        "ok": True,
        "state": "queued",
        "message": "Trend analysis workflow triggered",
        "trend_run_id": trend_run_id,
    }


@app.get("/trends/by-run")
def get_trends_by_run(run_id: str, authorization: str = Header(default="")):
    _check_auth(authorization)
    rid = (run_id or "").strip()
    if not rid:
        raise HTTPException(status_code=400, detail="run_id is required")
    print(f"[TREND_FETCH_BY_RUN] run_id={rid}")

    spreadsheet = _load_main_spreadsheet()
    try:
        ws = spreadsheet.worksheet("Trend Signals")
    except Exception:
        return {"trend_run_id": rid, "count": 0, "trends": []}

    values = ws.get_all_values()
    if len(values) <= 1:
        return {"trend_run_id": rid, "count": 0, "trends": []}

    headers = values[0]
    idx = {h: i for i, h in enumerate(headers)}
    ridx = idx.get("trend_run_id", 0)

    trends = []
    for row in values[1:]:
        if not row or ridx >= len(row) or row[ridx] != rid:
            continue
        trends.append({
            "trend_run_id": row[ridx],
            "week_key": row[idx.get("week_key", 1)] if idx.get("week_key", 1) < len(row) else "",
            "keyword": row[idx.get("keyword", 2)] if idx.get("keyword", 2) < len(row) else "",
            "count_current": row[idx.get("count_current", 3)] if idx.get("count_current", 3) < len(row) else "0",
            "baseline_4wk": row[idx.get("baseline_4wk", 4)] if idx.get("baseline_4wk", 4) < len(row) else "0",
            "pct_change": row[idx.get("pct_change", 5)] if idx.get("pct_change", 5) < len(row) else "0",
            "trend_score": row[idx.get("trend_score", 6)] if idx.get("trend_score", 6) < len(row) else "0",
            "publication_count": row[idx.get("publication_count", 7)] if idx.get("publication_count", 7) < len(row) else "0",
            "supporting_urls": row[idx.get("supporting_urls", 8)] if idx.get("supporting_urls", 8) < len(row) else "",
            "status": row[idx.get("status", 9)] if idx.get("status", 9) < len(row) else "trending",
            "window_mode": row[idx.get("window_mode", 10)] if idx.get("window_mode", 10) < len(row) else "",
        })

    print(f"[TREND_FETCH_BY_RUN_RESULT] run_id={rid} count={len(trends)}")
    return {"trend_run_id": rid, "count": len(trends), "trends": trends}


@app.get("/trends/latest")
def get_latest_trends(authorization: str = Header(default="")):
    _check_auth(authorization)
    rid = _metadata_get("latest_trend_run_id")
    print(f"[TREND_FETCH_LATEST] latest_trend_run_id={rid}")
    if not rid:
        return {"trend_run_id": None, "count": 0, "trends": []}
    return get_trends_by_run(run_id=rid, authorization=authorization)


@app.get("/trends/run-status")
def get_trend_run_status(run_id: str, authorization: str = Header(default="")):
    _check_auth(authorization)
    rid = (run_id or "").strip()
    if not rid:
        raise HTTPException(status_code=400, detail="run_id is required")

    latest_rid = (_metadata_get("latest_trend_run_id") or "").strip()
    latest_status = (_metadata_get("latest_trend_status") or "").strip().lower()
    rows_written_raw = (_metadata_get("latest_trend_rows_written") or "0").strip()
    try:
        rows_written = int(rows_written_raw)
    except Exception:
        rows_written = 0

    if latest_rid and rid != latest_rid:
        return {
            "run_id": rid,
            "status": "stale",
            "rows_written": 0,
            "latest_run_id": latest_rid,
        }

    status = latest_status if latest_status in {"running", "complete", "failed"} else "running"
    return {
        "run_id": rid,
        "status": status,
        "rows_written": rows_written,
        "latest_run_id": latest_rid or rid,
    }


@app.get("/clients")
def get_clients(authorization: str = Header(default="")):
    _check_auth(authorization)
    clients = _client_configs()
    return {"count": len(clients), "clients": clients}


@app.post("/clients")
def upsert_client(req: ClientConfigRequest, authorization: str = Header(default="")):
    _check_auth(authorization)
    result = _upsert_client(req)
    return {"ok": True, **result}


@app.post("/client-pitches/trigger")
def trigger_client_pitch_generation(req: PitchTriggerRequest, response: Response, authorization: str = Header(default="")):
    _check_auth(authorization)

    client = _client_by_id(req.client_id)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    pitch_run_id = f"pitch-{int(time.time())}-{uuid.uuid4().hex[:8]}"
    mode = (req.mode or "auto").strip()
    trend_run_id = (req.trend_run_id or "").strip()
    max_pitches = str(max(1, min(int(req.max_pitches or 5), 8)))

    _metadata_upsert("latest_pitch_run_id", pitch_run_id)
    _metadata_upsert("latest_pitch_status", "running")
    _metadata_upsert("latest_pitch_rows_written", "0")
    _metadata_upsert("latest_pitch_client_id", client["client_id"])
    _metadata_upsert("latest_pitch_mode", mode)

    workflow = "client-pitch-generator.yml"
    url = f"{GITHUB_API_BASE}/actions/workflows/{workflow}/dispatches"
    body = {
        "ref": GITHUB_REF,
        "inputs": {
            "pitch_run_id": pitch_run_id,
            "client_id": client["client_id"],
            "mode": mode,
            "trend_run_id": trend_run_id,
            "max_pitches": max_pitches,
        },
    }

    r = _gh_request("POST", url, json=body)
    print(
        "[PITCH_TRIGGER_RESULT] "
        f"run_id={pitch_run_id} status_code={r.status_code} workflow={workflow}"
    )
    if r.status_code != 204:
        _metadata_upsert("latest_pitch_status", "failed")
        raise HTTPException(status_code=502, detail=f"GitHub pitch dispatch failed: {r.status_code} {r.text}")

    response.status_code = 202
    return {
        "ok": True,
        "state": "queued",
        "message": "Client pitch generator workflow triggered",
        "pitch_run_id": pitch_run_id,
    }


@app.get("/client-pitches/run-status")
def get_pitch_run_status(pitch_run_id: str, authorization: str = Header(default="")):
    _check_auth(authorization)
    rid = (pitch_run_id or "").strip()
    if not rid:
        raise HTTPException(status_code=400, detail="pitch_run_id is required")

    latest_rid = (_metadata_get("latest_pitch_run_id") or "").strip()
    latest_status = (_metadata_get("latest_pitch_status") or "").strip().lower()
    rows_written_raw = (_metadata_get("latest_pitch_rows_written") or "0").strip()
    try:
        rows_written = int(rows_written_raw)
    except Exception:
        rows_written = 0

    if latest_rid and rid != latest_rid:
        return {
            "pitch_run_id": rid,
            "status": "stale",
            "rows_written": 0,
            "latest_pitch_run_id": latest_rid,
        }

    status = latest_status if latest_status in {"running", "complete", "failed"} else "running"
    return {
        "pitch_run_id": rid,
        "status": status,
        "rows_written": rows_written,
        "latest_pitch_run_id": latest_rid or rid,
    }


@app.get("/client-pitches/by-run")
def get_client_pitches_by_run(pitch_run_id: str, authorization: str = Header(default="")):
    _check_auth(authorization)
    rid = (pitch_run_id or "").strip()
    if not rid:
        raise HTTPException(status_code=400, detail="pitch_run_id is required")

    try:
        ws = _client_pitch_ws()
        values = ws.get_all_values()
    except Exception:
        return {"pitch_run_id": rid, "count": 0, "pitches": []}

    if len(values) <= 1:
        return {"pitch_run_id": rid, "count": 0, "pitches": []}

    headers = values[0]
    idx = {h: i for i, h in enumerate(headers)}
    ridx = idx.get("pitch_run_id", 0)
    pitches = []

    for row in values[1:]:
        if not row or ridx >= len(row) or row[ridx] != rid:
            continue
        pitches.append({
            "pitch_run_id": rid,
            "generated_at": row[idx.get("generated_at", 1)] if idx.get("generated_at", 1) < len(row) else "",
            "mode": row[idx.get("mode", 2)] if idx.get("mode", 2) < len(row) else "",
            "client_name": row[idx.get("client_name", 3)] if idx.get("client_name", 3) < len(row) else "",
            "pitch_angle": row[idx.get("pitch_angle", 4)] if idx.get("pitch_angle", 4) < len(row) else "",
            "suggested_story": row[idx.get("suggested_story", 5)] if idx.get("suggested_story", 5) < len(row) else "",
            "subject_line": row[idx.get("subject_line", 6)] if idx.get("subject_line", 6) < len(row) else "",
            "supporting_evidence": row[idx.get("supporting_evidence", 7)] if idx.get("supporting_evidence", 7) < len(row) else "",
            "supporting_urls": row[idx.get("supporting_urls", 8)] if idx.get("supporting_urls", 8) < len(row) else "",
            "model_name": row[idx.get("model_name", 9)] if idx.get("model_name", 9) < len(row) else "",
        })

    return {"pitch_run_id": rid, "count": len(pitches), "pitches": pitches}


@app.get("/client-pitches/latest")
def get_latest_client_pitches(authorization: str = Header(default="")):
    _check_auth(authorization)
    rid = (_metadata_get("latest_pitch_run_id") or "").strip()
    if not rid:
        return {"pitch_run_id": None, "count": 0, "pitches": []}
    return get_client_pitches_by_run(pitch_run_id=rid, authorization=authorization)


@app.post("/coverage/search-report/run")
def run_client_coverage_search_report(req: CoverageScanRequest, authorization: str = Header(default="")):
    _check_auth(authorization)

    report_title = (req.report_title or "").strip()
    if not report_title:
        raise HTTPException(status_code=400, detail="report_title is required")

    mention_terms = _split_keywords(req.mention_terms)
    if not mention_terms:
        raise HTTPException(status_code=400, detail="At least one mention term is required")

    search_queries = [
        line.strip()
        for line in (req.search_queries or "").replace("\r", "\n").split("\n")
        if line.strip()
    ]
    if not search_queries:
        raise HTTPException(status_code=400, detail="At least one search query is required")

    backlink_domains = _split_keywords(req.backlink_domains)
    capacity = get_serpapi_capacity()
    available_searches = capacity["monthly_left"]
    job_id = uuid.uuid4().hex
    coverage_run_id = f"coverage-search-{int(time.time())}-{uuid.uuid4().hex[:8]}"

    _coverage_job_set(
        job_id,
        status="running",
        phase="initializing",
        current=0,
        total=available_searches,
        message="Starting coverage report",
        coverage_run_id=coverage_run_id,
        available_searches=available_searches,
        summary=None,
        results=[],
        review_results=[],
        highlights=None,
        pdf_path=None,
        error=None,
    )

    def _runner():
        try:
            def _progress_cb(phase: str, current: int, total: int, message: str):
                _coverage_job_set(
                    job_id,
                    status="running",
                    phase=phase or "running",
                    current=int(current or 0),
                    total=int(total or 0),
                    message=message or "",
                )

            result = run_keyword_coverage_report(
                report_title=report_title,
                mention_terms=mention_terms,
                search_queries=search_queries,
                date_from=(req.date_from or "").strip(),
                date_to=(req.date_to or "").strip(),
                backlink_domains=backlink_domains,
                coverage_run_id=coverage_run_id,
                progress_callback=_progress_cb,
            )
            searches_used = int(result.get("searches_used", 0) or 0)
            _coverage_job_set(
                job_id,
                status="complete",
                phase="complete",
                current=searches_used,
                total=searches_used,
                message="Coverage report complete",
                coverage_run_id=result.get("coverage_run_id", coverage_run_id),
                summary={
                    "total_coverage": result.get("count", 0),
                    "needs_review": result.get("needs_review", 0),
                    "searched_results": result.get("searched_results", 0),
                    "searches_used": searches_used,
                    "searches_remaining": result.get("searches_remaining", 0),
                    "search_stop_reason": result.get("search_stop_reason", ""),
                },
                results=result.get("results", []),
                review_results=result.get("review_results", []),
                highlights=result.get("highlights"),
                pdf_path=result.get("pdf_path"),
            )
        except Exception as e:
            _coverage_job_set(
                job_id,
                status="failed",
                phase="failed",
                message=f"Coverage report failed: {str(e)}",
                error=str(e),
            )

    COVERAGE_EXECUTOR.submit(_runner)
    return {
        "ok": True,
        "job_id": job_id,
        "coverage_run_id": coverage_run_id,
        "available_searches": available_searches,
        "status": "running",
    }


@app.get("/coverage/search-report/progress")
def get_client_coverage_search_report_progress(job_id: str, authorization: str = Header(default="")):
    _check_auth(authorization)
    if not job_id:
        raise HTTPException(status_code=400, detail="job_id is required")
    job = _coverage_job_get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return {"ok": True, **job}


@app.get("/coverage/report")
def get_client_coverage_report(coverage_run_id: str, authorization: str = Header(default="")):
    _check_auth(authorization)

    rid = (coverage_run_id or "").strip()
    if not rid:
        raise HTTPException(status_code=400, detail="coverage_run_id is required")

    ss = _load_main_spreadsheet()
    try:
        ws = ss.worksheet(os.environ.get("CLIENT_COVERAGE_REPORT_SHEET", "Client Coverage Reports"))
    except Exception:
        return {
            "ok": True,
            "coverage_run_id": rid,
            "summary": {
                "total_rows": 0,
                "found": 0,
                "possible_match": 0,
                "not_found": 0,
                "error": 0,
            },
            "results": [],
        }

    rows = ws.get_all_records()
    scoped = [
        r for r in rows
        if str(r.get("coverage_run_id", "")).strip() == rid
    ]
    summary = {
        "total_rows": len(scoped),
        "found": sum(1 for r in scoped if str(r.get("status", "")).strip() == "found"),
        "possible_match": sum(1 for r in scoped if str(r.get("status", "")).strip() == "possible_match"),
        "not_found": sum(1 for r in scoped if str(r.get("status", "")).strip() == "not_found"),
        "error": sum(1 for r in scoped if str(r.get("status", "")).strip() == "error"),
    }

    return {
        "ok": True,
        "coverage_run_id": rid,
        "summary": summary,
        "results": scoped,
    }


@app.get("/coverage/search-report/download")
def download_client_coverage_search_report(job_id: str, authorization: str = Header(default="")):
    _check_auth(authorization)
    if not job_id:
        raise HTTPException(status_code=400, detail="job_id is required")

    job = _coverage_job_get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    if job.get("status") != "complete":
        raise HTTPException(status_code=409, detail="Coverage report is not complete")

    pdf_path = str(job.get("pdf_path") or "").strip()
    if not pdf_path or not os.path.exists(pdf_path):
        raise HTTPException(status_code=404, detail="PDF not found")

    filename = f"{job.get('coverage_run_id', 'client-coverage-report')}.pdf"
    return FileResponse(pdf_path, media_type="application/pdf", filename=filename)


@app.post("/sources/lists")
def create_source_list(req: SourceListCreateRequest, authorization: str = Header(default="")):
    _check_auth(authorization)
    list_name = (req.list_name or "").strip()
    if not list_name:
        raise HTTPException(status_code=400, detail="list_name is required")
    if not req.sources:
        raise HTTPException(status_code=400, detail="sources is required")

    ss = _load_main_spreadsheet()
    ws = ss.worksheet(SOURCE_CONFIG_SHEET)
    _ensure_ws_headers(ws, ["list_name", "publication", "base_url", "sitemap_url", "rss_url", "active", "date_added"])

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rows = []
    for s in req.sources:
        base_url = (s.base_url or "").strip()
        rss_url = (s.rss_url or "").strip()
        if not _is_valid_url(base_url):
            raise HTTPException(status_code=400, detail=f"Invalid base_url: {base_url}")
        if rss_url and not _is_valid_url(rss_url):
            raise HTTPException(status_code=400, detail=f"Invalid rss_url: {rss_url}")

        rows.append([
            list_name,
            _title_from_base_url(base_url),
            base_url,
            "",
            rss_url,
            "TRUE",
            today
        ])

    ws.append_rows(rows, value_input_option="USER_ENTERED")
    _topic_config_upsert(list_name, req.keywords or "", req.summary_prompt or "")
    return {"ok": True, "list_name": list_name, "inserted": len(rows)}


@app.get("/sources/lists")
def get_source_lists(authorization: str = Header(default="")):
    _check_auth(authorization)
    ss = _load_main_spreadsheet()
    ws = ss.worksheet(SOURCE_CONFIG_SHEET)
    rows = ws.get_all_records()

    agg = {}
    for r in rows:
        ln = str(r.get("list_name", "")).strip()
        if not ln:
            continue
        active = str(r.get("active", "TRUE")).upper() == "TRUE"
        if ln not in agg:
            agg[ln] = {"list_name": ln, "total_rows": 0, "active_rows": 0}
        agg[ln]["total_rows"] += 1
        if active:
            agg[ln]["active_rows"] += 1

    return {"ok": True, "lists": sorted(agg.values(), key=lambda x: x["list_name"].lower())}


@app.post("/sources/metadata/run")
def run_sources_metadata(req: SourceMetadataRunRequest, authorization: str = Header(default="")):
    _check_auth(authorization)
    list_name = (req.list_name or "").strip()
    if not list_name:
        raise HTTPException(status_code=400, detail="list_name is required")

    job_id = uuid.uuid4().hex
    _meta_job_set(
        job_id,
        status="running",
        phase="initializing",
        current=0,
        total=0,
        message=f"Starting metadata check for '{list_name}'",
        list_name=list_name,
        report=None,
        error=None,
    )

    def _runner():
        try:
            def _progress_cb(phase: str, current: int, total: int, message: str):
                _meta_job_set(
                    job_id,
                    status="running",
                    phase=phase or "running",
                    current=int(current or 0),
                    total=int(total or 0),
                    message=message or "",
                )

            result = run_publication_metadata_pipeline(list_name, progress_callback=_progress_cb)
            _meta_job_set(
                job_id,
                status="complete",
                phase="complete",
                current=int(result.get("total", 0)),
                total=int(result.get("total", 0)),
                message="Metadata check complete",
                report=result,
            )
        except Exception as e:
            _meta_job_set(
                job_id,
                status="failed",
                phase="failed",
                message=f"Metadata check failed: {str(e)}",
                error=str(e),
            )

    META_EXECUTOR.submit(_runner)
    return {"ok": True, "job_id": job_id}


@app.get("/sources/metadata/progress")
def get_sources_metadata_progress(job_id: str, authorization: str = Header(default="")):
    _check_auth(authorization)
    if not job_id:
        raise HTTPException(status_code=400, detail="job_id is required")
    job = _meta_job_get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return {"ok": True, **job}


@app.get("/sources/metadata/report")
def get_sources_metadata_report(list_name: str, run_id: Optional[str] = None, authorization: str = Header(default="")):
    _check_auth(authorization)
    ln = (list_name or "").strip()
    if not ln:
        raise HTTPException(status_code=400, detail="list_name is required")

    ss = _load_main_spreadsheet()
    ws = ss.worksheet(SOURCE_REPORT_DETAIL_SHEET)
    rows = ws.get_all_records()

    scoped = [r for r in rows if str(r.get("list_name", "")).strip() == ln]
    if run_id:
        rid = run_id.strip()
        scoped = [r for r in scoped if str(r.get("run_id", "")).strip() == rid]
    elif scoped:
        latest = str(scoped[-1].get("run_id", "")).strip()
        scoped = [r for r in scoped if str(r.get("run_id", "")).strip() == latest]

    total = len(scoped)
    valid_sitemap = sum(1 for r in scoped if str(r.get("sitemap_valid", "")).lower() == "true")
    valid_rss = sum(1 for r in scoped if str(r.get("rss_valid", "")).lower() == "true")
    both_valid = sum(
        1
        for r in scoped
        if str(r.get("sitemap_valid", "")).lower() == "true" and str(r.get("rss_valid", "")).lower() == "true"
    )
    neither_valid = sum(
        1
        for r in scoped
        if str(r.get("sitemap_valid", "")).lower() != "true" and str(r.get("rss_valid", "")).lower() != "true"
    )

    summary_text = (
        f"Total: {total} | Valid sitemap: {valid_sitemap} | Valid RSS: {valid_rss} | "
        f"Both valid: {both_valid} | Neither valid: {neither_valid}"
    )

    return {
        "ok": True,
        "summary": {
            "total": total,
            "valid_sitemap": valid_sitemap,
            "valid_rss": valid_rss,
            "both_valid": both_valid,
            "neither_valid": neither_valid,
            "summary_text": summary_text,
        },
        "details": scoped,
    }
