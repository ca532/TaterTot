from __future__ import annotations

import json
import os
import re
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

try:
    from article_quality import (
        canonicalize_url,
        clean_article_text,
        extract_page_metadata,
        keyword_matches,
        validate_article_content,
    )
    from client_coverage_pdf import build_coverage_pdf
    from google_storage import GoogleSheetsDB
    from publication_traffic import lookup_hypestat_monthly_visits
except ImportError:
    from backend.article_quality import (
        canonicalize_url,
        clean_article_text,
        extract_page_metadata,
        keyword_matches,
        validate_article_content,
    )
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
    "extraction_method",
    "verification_status",
    "verification_reason",
    "matched_location",
    "backlink_url",
]

NON_CONTENT_PATH_PARTS = (
    "/tag/", "/tags/", "/category/", "/categories/", "/author/",
    "/authors/", "/search", "/archive/", "/archives/", "/topic/",
    "/topics/", "/newsletter", "/subscribe",
)
ARTICLE_CONTAINER_SELECTORS = (
    "[itemprop='articleBody']",
    ".article-body",
    ".article-content",
    ".story-body",
    ".story-content",
    ".entry-content",
    ".post-content",
)


def domain_from_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = f"https://{raw}"
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


def validate_coverage_url(url: str) -> tuple[bool, str]:
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False, "invalid_url"
    path = (parsed.path or "/").lower()
    if path.rstrip("/") == "":
        return False, "homepage"
    if any(part in path for part in NON_CONTENT_PATH_PARTS):
        return False, "non_content_url"
    return True, ""


def _json_ld_objects(value):
    if isinstance(value, list):
        for item in value:
            yield from _json_ld_objects(item)
    elif isinstance(value, dict):
        yield value
        if "@graph" in value:
            yield from _json_ld_objects(value["@graph"])


def _extract_json_ld_article_body(soup: BeautifulSoup) -> str:
    candidates = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            value = json.loads(script.string or script.get_text() or "{}")
        except Exception:
            continue
        for obj in _json_ld_objects(value):
            article_type = str(obj.get("@type", "")).lower()
            body = clean_article_text(str(obj.get("articleBody", "")))
            if body and ("article" in article_type or "news" in article_type):
                candidates.append(body)
    return max(candidates, key=len, default="")


def _find_article_container(soup: BeautifulSoup):
    article_nodes = soup.find_all("article")
    if article_nodes:
        return max(article_nodes, key=lambda node: len(node.get_text(" ", strip=True))), "article"

    for selector in ARTICLE_CONTAINER_SELECTORS:
        nodes = soup.select(selector)
        if nodes:
            node = max(nodes, key=lambda item: len(item.get_text(" ", strip=True)))
            if len(node.get_text(" ", strip=True)) >= 150:
                return node, f"selector:{selector}"
    return None, ""


def _extract_newspaper_text(html_text: str, url: str) -> str:
    try:
        from newspaper import Article

        article = Article(url=url)
        article.set_html(html_text)
        article.parse()
        return clean_article_text(article.text)
    except Exception:
        return ""


def _valid_article_text(text: str) -> bool:
    valid, _ = validate_article_content(text)
    return valid


def _extract_subtitle(soup: BeautifulSoup) -> str:
    selectors = (
        "[class*='standfirst']",
        "[class*='subheadline']",
        "[class*='subtitle']",
        "[class*='article-dek']",
        "[class*='article__dek']",
    )
    for selector in selectors:
        node = soup.select_one(selector)
        text = clean_article_text(node.get_text(" ", strip=True)) if node else ""
        if text:
            return text

    for attrs in ({"property": "og:description"}, {"name": "description"}):
        node = soup.find("meta", attrs=attrs)
        text = clean_article_text(str(node.get("content", ""))) if node else ""
        if text:
            return text
    return ""


def _extract_article_links(container, page_url: str) -> list[str]:
    if container is None:
        return []
    links = []
    seen = set()
    for anchor in container.find_all("a", href=True):
        absolute = urljoin(page_url, str(anchor.get("href", "")).strip())
        parsed = urlparse(absolute)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue
        normalized = canonicalize_url(absolute)
        if normalized not in seen:
            seen.add(normalized)
            links.append(normalized)
    return links


def fetch_page(url: str) -> dict:
    valid_url, invalid_reason = validate_coverage_url(url)
    if not valid_url:
        return {"ok": False, "url": url, "error": invalid_reason}

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
    json_ld_body = _extract_json_ld_article_body(soup)

    for tag in soup(["script", "style", "noscript", "svg", "form", "nav", "footer", "aside"]):
        tag.decompose()

    html_title = soup.title.get_text(" ", strip=True) if soup.title else ""
    canonical = metadata.get("canonical_url") or res.url or url
    canonical = urljoin(res.url or url, canonical)
    valid_canonical, invalid_reason = validate_coverage_url(canonical)
    if not valid_canonical:
        return {"ok": False, "url": canonical, "error": invalid_reason}

    container, container_method = _find_article_container(soup)
    container_text = clean_article_text(container.get_text(" ", strip=True)) if container else ""
    newspaper_text = ""

    if _valid_article_text(json_ld_body):
        body_text = json_ld_body
        extraction_method = "json_ld_article_body"
        extraction_reliable = True
    elif container_method == "article" and _valid_article_text(container_text):
        body_text = container_text
        extraction_method = "article_element"
        extraction_reliable = True
    else:
        newspaper_text = _extract_newspaper_text(res.text, canonical)
        if _valid_article_text(newspaper_text):
            body_text = newspaper_text
            extraction_method = "newspaper3k"
            extraction_reliable = True
        elif container and _valid_article_text(container_text):
            body_text = container_text
            extraction_method = container_method
            extraction_reliable = True
        else:
            body_text = clean_article_text(soup.get_text(" ", strip=True))
            extraction_method = "whole_page_fallback"
            extraction_reliable = False

    captions = []
    if container:
        captions = [
            clean_article_text(node.get_text(" ", strip=True))
            for node in container.find_all("figcaption")
            if clean_article_text(node.get_text(" ", strip=True))
        ]

    return {
        "ok": True,
        "url": canonicalize_url(canonical),
        "title": clean_article_text(metadata.get("title") or html_title),
        "subtitle": _extract_subtitle(soup),
        "body_text": body_text[:30000],
        "captions": captions,
        "extraction_method": extraction_method,
        "extraction_reliable": extraction_reliable,
        "has_article_container": container is not None,
        "article_links": _extract_article_links(container, canonical),
        "published_date": metadata.get("published_date"),
        "domain": domain_from_url(canonical),
    }


def _evidence_snippet(text: str, term: str) -> str:
    match = re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text or "", flags=re.IGNORECASE)
    if not match:
        return ""
    start = max(0, match.start() - 180)
    end = min(len(text), match.end() + 220)
    return clean_article_text(text[start:end])


def extract_evidence(page: dict, mention_terms: list[str], backlink_domains: list[str] | None = None) -> dict:
    locations = (
        ("title", page.get("title", "")),
        ("subtitle", page.get("subtitle", "")),
        ("body", page.get("body_text", "")),
        ("caption", " ".join(page.get("captions", []) or [])),
    )
    matched_terms = []
    matched_locations = []
    evidence_snippet = ""
    for location, text in locations:
        location_matches = keyword_matches(text, mention_terms)
        if location_matches:
            matched_locations.append(location)
        for term in location_matches:
            if term not in matched_terms:
                matched_terms.append(term)
            if not evidence_snippet:
                evidence_snippet = _evidence_snippet(text, term)

    target_domains = {
        domain_from_url(domain)
        for domain in (backlink_domains or [])
        if domain_from_url(domain)
    }
    backlink_urls = []
    for link in page.get("article_links", []) or []:
        host = domain_from_url(link)
        if any(host == domain or host.endswith(f".{domain}") for domain in target_domains):
            backlink_urls.append(link)
    backlink_urls = list(dict.fromkeys(backlink_urls))
    has_backlink = bool(backlink_urls)

    trusted_locations = {"title", "subtitle"}
    if page.get("has_article_container"):
        trusted_locations.add("caption")
    trusted_match = page.get("extraction_reliable") or any(
        location in trusted_locations for location in matched_locations
    )

    if matched_terms and trusted_match:
        verification_status = "confirmed + backlink" if has_backlink else "confirmed"
        verification_reason = f"Exact approved term found in {', '.join(matched_locations)}"
    elif matched_terms:
        verification_status = "needs_review"
        verification_reason = "Term found only in whole-page fallback text"
    elif page.get("extraction_reliable"):
        verification_status = "not_found"
        verification_reason = "No approved term found in article-owned content"
    else:
        verification_status = "needs_review"
        verification_reason = "Reliable article-body extraction was unavailable"

    return {
        "matched_terms": matched_terms,
        "has_backlink": has_backlink,
        "backlink_urls": backlink_urls,
        "coverage_type": (
            "needs_review"
            if verification_status == "needs_review"
            else "mention + backlink" if has_backlink else "mention"
        ),
        "evidence_snippet": evidence_snippet,
        "matched_locations": matched_locations,
        "verification_status": verification_status,
        "verification_reason": verification_reason,
        "is_relevant": verification_status.startswith("confirmed"),
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

    if ws.col_count < len(REPORT_HEADERS):
        ws.resize(cols=len(REPORT_HEADERS))

    values = ws.get_all_values()
    if not values:
        ws.append_row(REPORT_HEADERS)
    elif values[0] != REPORT_HEADERS:
        column = len(REPORT_HEADERS)
        end_column = ""
        while column:
            column, remainder = divmod(column - 1, 26)
            end_column = chr(65 + remainder) + end_column
        ws.update(f"A1:{end_column}1", [REPORT_HEADERS])
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
    review_results = []
    total_results = len(unique_results)
    emit("verifying", 0, total_results, "Checking article mentions")
    for index, result in enumerate(unique_results, start=1):
        emit("verifying", index, total_results, f"Checking result {index} of {total_results}")
        page = fetch_page(result["article_url"])
        if not page.get("ok"):
            error = page.get("error", "extraction_failed")
            if error in {"invalid_url", "homepage", "non_content_url", "http_404", "http_410"}:
                continue
            evidence = {
                "matched_terms": [],
                "has_backlink": False,
                "backlink_urls": [],
                "coverage_type": "needs_review",
                "evidence_snippet": "",
                "matched_locations": [],
                "verification_status": "needs_review",
                "verification_reason": f"Article extraction failed: {error}",
                "is_relevant": False,
            }
        else:
            evidence = extract_evidence(
                page,
                mention_terms=mention_terms,
                backlink_domains=backlink_domains,
            )

        if not evidence["is_relevant"] and evidence["verification_status"] != "needs_review":
            continue

        domain = page.get("domain") or result.get("domain", "")
        published = page.get("published_date") or result.get("date", "")
        if hasattr(published, "strftime"):
            published = published.strftime("%Y-%m-%d")

        row = {
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
            "extraction_method": page.get("extraction_method", "unavailable"),
            "verification_status": evidence["verification_status"],
            "verification_reason": evidence["verification_reason"],
            "matched_location": ", ".join(evidence["matched_locations"]),
            "backlink_url": ", ".join(evidence["backlink_urls"]),
            "evidence_snippet": evidence["evidence_snippet"],
            "link_note": "with link back" if evidence["has_backlink"] else "",
        }
        if evidence["is_relevant"]:
            confirmed.append(row)
        else:
            review_results.append(row)

    confirmed = dedupe_results(confirmed)
    review_results = dedupe_results(review_results)
    domains = sorted({row["domain"] for row in confirmed if row.get("domain")})
    emit("traffic", 0, len(domains), "Looking up publication traffic")
    traffic = lookup_hypestat_monthly_visits(domains)
    for row in confirmed:
        data = traffic.get(row["domain"], {})
        row["monthly_visits"] = data.get("monthly_visits") or ""
        row["monthly_visits_display"] = data.get("monthly_visits_display") or "N/A"
        row["traffic_source"] = data.get("source") or "hypestat"

    rows_to_save = confirmed + review_results
    emit("saving", 0, len(rows_to_save), "Saving report")
    save_report_rows(rows_to_save)

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
        "needs_review": len(review_results),
        "review_results": review_results,
        "highlights": highlights,
        "pdf_path": pdf_path,
        "searched_results": total_results,
        "searches_used": search_run["searches_used"],
        "searches_remaining": search_run["searches_remaining"],
        "search_stop_reason": search_run["stop_reason"],
    }
