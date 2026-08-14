from __future__ import annotations

import os
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

try:
    from article_quality import canonicalize_url, clean_article_text, extract_page_metadata
    from client_coverage_pdf import build_coverage_pdf
    from google_storage import GoogleSheetsDB
    from publication_traffic import lookup_hypestat_monthly_visits
except ImportError:
    from backend.article_quality import canonicalize_url, clean_article_text, extract_page_metadata
    from backend.client_coverage_pdf import build_coverage_pdf
    from backend.google_storage import GoogleSheetsDB
    from backend.publication_traffic import lookup_hypestat_monthly_visits


SERPAPI_URL = "https://serpapi.com/search.json"
SERPAPI_ACCOUNT_URL = "https://serpapi.com/account.json"
SERPAPI_API_KEY = os.getenv("SERPAPI_API_KEY", "")
REPORT_SHEET = os.getenv("CLIENT_COVERAGE_REPORT_SHEET", "Client Coverage Reports")
OUTPUT_DIR = Path(os.getenv("CLIENT_COVERAGE_OUTPUT_DIR", "output/client_coverage"))
REPORT_HEADERS = [
    "coverage_run_id",
    "created_at",
    "report_title",
    "search_query",
    "article_title",
    "article_url",
    "publication",
    "domain",
    "country",
    "published_date",
    "coverage_type",
    "has_backlink",
    "matched_terms",
    "monthly_visits",
    "monthly_visits_display",
    "traffic_source",
    "evidence_snippet",
]


def domain_from_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    host = urlparse(raw).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def publication_name_from_domain(domain: str) -> str:
    root = (domain or "publication").split(".")[0]
    return root.replace("-", " ").replace("_", " ").title()


def split_lines(raw: str) -> list[str]:
    return [
        line.strip()
        for line in (raw or "").replace("\r", "\n").split("\n")
        if line.strip()
    ]


def split_csv_or_lines(raw: str) -> list[str]:
    value = (raw or "").replace("\r", "\n")
    parts = []
    for line in value.split("\n"):
        parts.extend(piece.strip() for piece in line.split(","))
    return [part for part in parts if part]


def get_serpapi_capacity() -> dict:
    if not SERPAPI_API_KEY:
        raise RuntimeError("SERPAPI_API_KEY is required")

    response = requests.get(
        SERPAPI_ACCOUNT_URL,
        params={"api_key": SERPAPI_API_KEY},
        timeout=20,
    )
    response.raise_for_status()
    data = response.json()

    monthly_left_raw = data.get("total_searches_left")
    if monthly_left_raw is None:
        monthly_left_raw = data.get("plan_searches_left", 0)
    monthly_left = int(monthly_left_raw or 0)
    hourly_limit = int(data.get("account_rate_limit_per_hour") or 0)
    hourly_used = int(data.get("this_hour_searches") or 0)

    return {
        "monthly_left": monthly_left,
        "hourly_limit": hourly_limit,
        "hourly_used": hourly_used,
        "hourly_left": (
            max(0, hourly_limit - hourly_used)
            if hourly_limit
            else monthly_left
        ),
    }


def serpapi_search_all(
    queries: list[str],
    date_from: str = "",
    date_to: str = "",
    progress_callback=None,
) -> dict:
    def emit(phase: str, current: int, total: int, message: str):
        if progress_callback:
            try:
                progress_callback(phase, current, total, message)
            except Exception:
                pass

    starting_capacity = get_serpapi_capacity()
    starting_budget = starting_capacity["monthly_left"]
    queue = deque()

    for query in queries:
        dated_query = query.strip()
        if date_from:
            dated_query += f" after:{date_from}"
        if date_to:
            dated_query += f" before:{date_to}"

        queue.append({
            "query": query,
            "dated_query": dated_query,
            "next_url": "",
            "visited_pages": set(),
        })

    results = []
    searches_used = 0
    stop_reason = "all_pages_searched"

    while queue:
        capacity = get_serpapi_capacity()
        if capacity["monthly_left"] <= 0:
            stop_reason = "monthly_limit_reached"
            break

        if capacity["hourly_limit"] and capacity["hourly_left"] <= 0:
            emit(
                "waiting",
                searches_used,
                starting_budget,
                "Hourly search limit reached. Waiting to continue.",
            )
            time.sleep(60)
            continue

        state = queue.popleft()
        if state["next_url"]:
            request_key = state["next_url"]
            if request_key in state["visited_pages"]:
                continue
            state["visited_pages"].add(request_key)
            response = requests.get(
                state["next_url"],
                params={"api_key": SERPAPI_API_KEY},
                timeout=30,
            )
        else:
            request_key = state["dated_query"]
            state["visited_pages"].add(request_key)
            response = requests.get(
                SERPAPI_URL,
                params={
                    "engine": "google",
                    "q": state["dated_query"],
                    "api_key": SERPAPI_API_KEY,
                    "num": 100,
                },
                timeout=30,
            )

        if response.status_code == 429:
            queue.appendleft(state)
            emit(
                "waiting",
                searches_used,
                starting_budget,
                "SerpApi is temporarily at capacity. Waiting to continue.",
            )
            time.sleep(60)
            continue

        response.raise_for_status()
        data = response.json()
        searches_used += 1
        organic_results = data.get("organic_results", []) or []

        for item in organic_results:
            link = item.get("link", "")
            if not link:
                continue
            results.append({
                "title": item.get("title", ""),
                "url": link,
                "snippet": item.get("snippet", ""),
                "date": item.get("date", ""),
                "domain": domain_from_url(link),
                "search_query": state["query"],
            })

        next_url = (data.get("serpapi_pagination", {}) or {}).get("next", "")
        if organic_results and next_url:
            state["next_url"] = next_url
            queue.append(state)

        emit(
            "searching",
            searches_used,
            starting_budget,
            f"Searched {searches_used} Google pages",
        )

    final_capacity = get_serpapi_capacity()
    return {
        "results": results,
        "searches_used": searches_used,
        "searches_remaining": final_capacity["monthly_left"],
        "stop_reason": stop_reason,
    }


def fetch_page(url: str) -> dict:
    try:
        res = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; CA-CoverageBot/1.0)"},
            timeout=25,
            allow_redirects=True,
        )
        if res.status_code >= 400:
            return {"ok": False, "url": url, "error": f"http_{res.status_code}"}
    except Exception as exc:
        return {"ok": False, "url": url, "error": type(exc).__name__}

    soup = BeautifulSoup(res.text, "html.parser")
    metadata = extract_page_metadata(res.text)

    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    html_title = soup.title.get_text(" ", strip=True) if soup.title else ""
    text = clean_article_text(soup.get_text(" ", strip=True))
    canonical = metadata.get("canonical_url") or res.url or url

    return {
        "ok": True,
        "url": canonicalize_url(canonical),
        "title": clean_article_text(metadata.get("title") or html_title),
        "text": text[:12000],
        "published_date": metadata.get("published_date"),
        "domain": domain_from_url(canonical),
        "html": res.text,
    }


def extract_evidence(page: dict, mention_terms: list[str], backlink_domains: list[str] | None = None) -> dict:
    backlink_domains = backlink_domains or []
    page_text = page.get("text", "")
    lowered = page_text.lower()
    html_lowered = (page.get("html", "") or "").lower()

    matched_terms = [
        term for term in mention_terms
        if term and term.lower() in lowered
    ]
    quote_markers = (
        "said", "says", "told", "explained", "according to",
        "expert", "specialist", "predicts", "estimates", "quoted",
    )
    has_quote_context = any(marker in lowered for marker in quote_markers) and bool(matched_terms)
    has_backlink = any(domain.lower() in html_lowered for domain in backlink_domains if domain)

    snippet = ""
    for term in matched_terms:
        idx = lowered.find(term.lower())
        if idx >= 0:
            start = max(0, idx - 180)
            end = min(len(page_text), idx + len(term) + 220)
            snippet = page_text[start:end].strip()
            break

    if has_quote_context and has_backlink:
        coverage_type = "quote + backlink"
    elif has_quote_context:
        coverage_type = "quote"
    elif has_backlink:
        coverage_type = "backlink"
    elif matched_terms:
        coverage_type = "mention"
    else:
        coverage_type = "not_relevant"

    return {
        "matched_terms": matched_terms,
        "has_backlink": has_backlink,
        "coverage_type": coverage_type,
        "evidence_snippet": snippet,
        "is_relevant": bool(matched_terms or has_backlink),
    }


def dedupe_results(results: list[dict]) -> list[dict]:
    seen = set()
    deduped = []
    for row in results:
        key = canonicalize_url(row.get("article_url", ""))
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def ensure_report_sheet(db: GoogleSheetsDB):
    try:
        ws = db.spreadsheet.worksheet(REPORT_SHEET)
    except Exception:
        ws = db.spreadsheet.add_worksheet(title=REPORT_SHEET, rows=3000, cols=len(REPORT_HEADERS))

    values = ws.get_all_values()
    if not values:
        ws.append_row(REPORT_HEADERS)
    elif values[0] != REPORT_HEADERS:
        ws.update("A1:Q1", [REPORT_HEADERS])
    return ws


def save_report_rows(rows: list[dict]) -> None:
    if not rows:
        return
    db = GoogleSheetsDB()
    ws = ensure_report_sheet(db)
    payload = [[row.get(header, "") for header in REPORT_HEADERS] for row in rows]
    ws.append_rows(payload, value_input_option="USER_ENTERED")


def run_keyword_coverage_report(
    report_title: str,
    mention_terms: list[str],
    search_queries: list[str],
    date_from: str = "",
    date_to: str = "",
    backlink_domains: list[str] | None = None,
    coverage_run_id: str | None = None,
    progress_callback=None,
):
    def emit(phase: str, current: int, total: int, message: str):
        if progress_callback:
            try:
                progress_callback(phase, current, total, message)
            except Exception:
                pass

    backlink_domains = backlink_domains or []
    run_id = coverage_run_id or f"coverage-search-{int(time.time())}"
    created_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    emit("searching", 0, 0, "Searching Google")
    search_run = serpapi_search_all(
        queries=search_queries,
        date_from=date_from,
        date_to=date_to,
        progress_callback=progress_callback,
    )
    all_search_results = search_run["results"]

    unique_results = dedupe_results([
        {"article_url": item["url"], **item}
        for item in all_search_results
    ])

    confirmed = []
    total_results = len(unique_results)
    emit("verifying", 0, total_results, "Checking article mentions")
    for index, result in enumerate(unique_results, start=1):
        emit("verifying", index, total_results, f"Checking result {index} of {total_results}")
        page = fetch_page(result["article_url"])
        if not page.get("ok"):
            continue

        evidence = extract_evidence(page, mention_terms=mention_terms, backlink_domains=backlink_domains)
        if not evidence["is_relevant"]:
            continue

        domain = page.get("domain") or result.get("domain", "")
        published = page.get("published_date") or result.get("date", "")
        if hasattr(published, "strftime"):
            published = published.strftime("%Y-%m-%d")

        confirmed.append({
            "coverage_run_id": run_id,
            "created_at": created_at,
            "report_title": report_title,
            "search_query": result.get("search_query", ""),
            "article_title": page.get("title") or result.get("title", ""),
            "article_url": page.get("url") or result["article_url"],
            "publication": publication_name_from_domain(domain),
            "domain": domain,
            "country": "",
            "published_date": str(published or ""),
            "coverage_type": evidence["coverage_type"],
            "has_backlink": "TRUE" if evidence["has_backlink"] else "FALSE",
            "matched_terms": ", ".join(evidence["matched_terms"]),
            "monthly_visits": "",
            "monthly_visits_display": "N/A",
            "traffic_source": "hypestat",
            "evidence_snippet": evidence["evidence_snippet"],
            "link_note": "with link back" if evidence["has_backlink"] else "",
        })

    confirmed = dedupe_results(confirmed)
    domains = sorted({row["domain"] for row in confirmed if row.get("domain")})
    emit("traffic", 0, len(domains), "Looking up publication traffic")
    traffic = lookup_hypestat_monthly_visits(domains)
    for row in confirmed:
        data = traffic.get(row["domain"], {})
        row["monthly_visits"] = data.get("monthly_visits") or ""
        row["monthly_visits_display"] = data.get("monthly_visits_display") or "N/A"
        row["traffic_source"] = data.get("source") or "hypestat"

    emit("saving", 0, len(confirmed), "Saving report")
    save_report_rows(confirmed)

    countries = sorted({row["country"] for row in confirmed if row.get("country")})
    highlights = {
        "total_coverage": len(confirmed),
        "country_count": len(countries),
        "countries": ", ".join(countries),
        "highlight_publications": ", ".join(row["publication"] for row in confirmed[:8]),
    }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    pdf_path = str(OUTPUT_DIR / f"{run_id}.pdf")
    emit("pdf", 0, len(confirmed), "Generating PDF")
    build_coverage_pdf(pdf_path, report_title, highlights, confirmed)

    emit("complete", len(confirmed), len(confirmed), "Coverage report complete")
    return {
        "coverage_run_id": run_id,
        "count": len(confirmed),
        "results": confirmed,
        "highlights": highlights,
        "pdf_path": pdf_path,
        "searched_results": total_results,
        "searches_used": search_run["searches_used"],
        "searches_remaining": search_run["searches_remaining"],
        "search_stop_reason": search_run["stop_reason"],
    }
