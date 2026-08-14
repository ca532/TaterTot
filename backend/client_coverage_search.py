from __future__ import annotations

import os
import time
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


def serpapi_search(query: str, date_from: str = "", date_to: str = "", max_pages: int = 1) -> list[dict]:
    if not SERPAPI_API_KEY:
        raise RuntimeError("SERPAPI_API_KEY is required")

    dated_query = query.strip()
    if date_from:
        dated_query += f" after:{date_from}"
    if date_to:
        dated_query += f" before:{date_to}"

    results = []
    for page in range(max(1, min(int(max_pages or 1), 10))):
        params = {
            "engine": "google",
            "q": dated_query,
            "api_key": SERPAPI_API_KEY,
            "num": 100,
            "start": page * 100,
        }
        res = requests.get(SERPAPI_URL, params=params, timeout=30)
        res.raise_for_status()
        data = res.json()

        for item in data.get("organic_results", []) or []:
            link = item.get("link", "")
            if not link:
                continue
            results.append({
                "title": item.get("title", ""),
                "url": link,
                "snippet": item.get("snippet", ""),
                "date": item.get("date", ""),
                "domain": domain_from_url(link),
                "search_query": query,
            })

    return results


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
    pages_per_query: int = 1,
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
    all_search_results = []
    total_queries = len(search_queries)

    emit("searching", 0, total_queries, "Searching Google")
    for index, query in enumerate(search_queries, start=1):
        emit("searching", index, total_queries, f"Searching query {index} of {total_queries}")
        all_search_results.extend(
            serpapi_search(query, date_from=date_from, date_to=date_to, max_pages=pages_per_query)
        )

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
    }
