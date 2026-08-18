from __future__ import annotations

import json
import os
import re
import time
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup


HYPESTAT_BASE_URL = "https://hypestat.com/info"
ZENROWS_API_URL = "https://api.zenrows.com/v1/"
ZENROWS_API_KEY = os.getenv("ZENROWS_API_KEY", "").strip()
ZENROWS_WAIT_MS = os.getenv("ZENROWS_WAIT_MS", "3000")
ZENROWS_MAX_RETRIES = int(os.getenv("ZENROWS_MAX_RETRIES", "2"))
ZENROWS_TIMEOUT_SECONDS = int(os.getenv("ZENROWS_TIMEOUT_SECONDS", "150"))
BLOCKED_MARKERS = (
    "verify you are human",
    "access denied",
    "captcha",
    "checking your browser",
    "cloudflare ray id",
)


def domain_from_url(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = f"https://{raw}"
    host = urlparse(raw).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def parse_visit_count(value: str) -> int | None:
    raw = (value or "").replace(",", "").strip().upper()
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*([KMB]?)", raw)
    if not match:
        return None

    number = float(match.group(1))
    multipliers = {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}
    return int(number * multipliers[match.group(2)])


def format_visits(value: int | None) -> str:
    if value is None:
        return "N/A"
    if value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.1f}B"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return str(value)


def extract_monthly_visits(page_text: str) -> int | None:
    patterns = (
        r"\bMonthly Visits\s*:?\s*([0-9][0-9,.]*\s*[KMB]?)\b",
        r"\babout\s+([0-9][0-9,.]*\s*[KMB]?)\s+monthly visitors\b",
    )
    for pattern in patterns:
        match = re.search(pattern, page_text or "", flags=re.IGNORECASE)
        if match:
            visits = parse_visit_count(match.group(1))
            if visits is not None:
                return visits
    return None


def default_result(error: str) -> dict:
    return {
        "monthly_visits": None,
        "monthly_visits_display": "N/A",
        "source": "hypestat_via_zenrows",
        "error": error,
    }


def _log_lookup(
    domain: str,
    status: str,
    visits: int | None = None,
    error: str = "",
) -> None:
    print(
        "[TRAFFIC_LOOKUP] "
        + json.dumps({
            "domain": domain,
            "source": "hypestat_via_zenrows",
            "status": status,
            "monthly_visits": visits,
            "error": error,
        }, sort_keys=True),
        flush=True,
    )


def _lookup_domain(domain: str) -> dict:
    params = {
        "apikey": ZENROWS_API_KEY,
        "url": f"{HYPESTAT_BASE_URL}/{domain}",
        "js_render": "true",
        "premium_proxy": "true",
        "wait": ZENROWS_WAIT_MS,
    }
    last_error = "request_failed"

    for attempt in range(ZENROWS_MAX_RETRIES + 1):
        try:
            response = requests.get(
                ZENROWS_API_URL,
                params=params,
                timeout=(10, ZENROWS_TIMEOUT_SECONDS),
            )
        except requests.RequestException as exc:
            last_error = type(exc).__name__
        else:
            if response.status_code == 429 or response.status_code >= 500:
                last_error = f"zenrows_http_{response.status_code}"
            elif response.status_code >= 400:
                last_error = f"zenrows_http_{response.status_code}"
                break
            else:
                page_text = BeautifulSoup(
                    response.text,
                    "html.parser",
                ).get_text(" ", strip=True)
                if any(marker in page_text.lower() for marker in BLOCKED_MARKERS):
                    last_error = "hypestat_blocked"
                else:
                    visits = extract_monthly_visits(page_text)
                    status = "success" if visits is not None else "not_found"
                    error = "" if visits is not None else "monthly_visits_not_found"
                    _log_lookup(domain, status, visits, error)
                    return {
                        "monthly_visits": visits,
                        "monthly_visits_display": format_visits(visits),
                        "source": "hypestat_via_zenrows",
                        "error": error,
                    }

        if attempt < ZENROWS_MAX_RETRIES:
            time.sleep(2 ** attempt)

    _log_lookup(domain, "failed", error=last_error)
    return default_result(last_error)


def lookup_publication_traffic(domains: list[str]) -> dict[str, dict]:
    targets = sorted({
        normalized
        for domain in domains
        if (normalized := domain_from_url(domain))
    })
    if not targets:
        return {}
    if not ZENROWS_API_KEY:
        raise RuntimeError("ZENROWS_API_KEY is required")

    return {domain: _lookup_domain(domain) for domain in targets}
