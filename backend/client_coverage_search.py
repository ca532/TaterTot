from __future__ import annotations

import json
import os
import re
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

try:
    from article_quality import (
        clean_article_text,
        extract_page_metadata,
        keyword_matches,
        validate_article_content,
    )
    from client_coverage_pdf import build_coverage_pdf
    from google_storage import GoogleSheetsDB
    from publication_country import (
        enrich_publication_countries,
        extract_metadata_country,
    )
    from publication_traffic import lookup_publication_traffic
except ImportError:
    from backend.article_quality import (
        clean_article_text,
        extract_page_metadata,
        keyword_matches,
        validate_article_content,
    )
    from backend.client_coverage_pdf import build_coverage_pdf
    from backend.google_storage import GoogleSheetsDB
    from backend.publication_country import (
        enrich_publication_countries,
        extract_metadata_country,
    )
    from backend.publication_traffic import lookup_publication_traffic


SERPAPI_URL = "https://serpapi.com/search.json"
SERPAPI_ACCOUNT_URL = "https://serpapi.com/account.json"
SERPAPI_API_KEY = os.getenv("SERPAPI_API_KEY", "")
SERPAPI_GOOGLE_DOMAIN = os.getenv(
    "SERPAPI_GOOGLE_DOMAIN",
    "google.com",
).strip()
SERPAPI_GL = os.getenv("SERPAPI_GL", "us").strip()
SERPAPI_HL = os.getenv("SERPAPI_HL", "en").strip()
SERPAPI_CONNECT_TIMEOUT = 10
SERPAPI_READ_TIMEOUT = 90
SERPAPI_NETWORK_RETRIES = 3
SERPAPI_RESULTS_PER_PAGE = 10
SERPAPI_DATE_SLICE_DAYS = 7
SERPAPI_COUNTRY_RESERVE = max(
    0,
    int(os.getenv("SERPAPI_COUNTRY_RESERVE", "10")),
)
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
    "country_source",
    "country_confidence",
    "country_lookup_key",
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
TRACKING_QUERY_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid"}
SKIPPED_HREF_PREFIXES = (
    "#",
    "javascript:",
    "mailto:",
    "tel:",
    "data:",
)
MAX_ARTICLE_RESPONSE_BYTES = 5_000_000


def normalize_coverage_url(value: str) -> str:
    try:
        parsed = urlparse((value or "").strip())
        if parsed.scheme.lower() not in {"http", "https"}:
            return ""

        host = (parsed.hostname or "").lower()
        if not host or parsed.username or parsed.password:
            return ""

        host_text = f"[{host}]" if ":" in host else host
        netloc = f"{host_text}:{parsed.port}" if parsed.port else host_text
        path = re.sub(r"/{2,}", "/", parsed.path or "/").rstrip("/") or "/"
        query = urlencode(sorted(
            (key, item)
            for key, item in parse_qsl(parsed.query, keep_blank_values=True)
            if not key.lower().startswith("utm_")
            and key.lower() not in TRACKING_QUERY_KEYS
        ))
        return urlunparse((parsed.scheme.lower(), netloc, path, "", query, ""))
    except (TypeError, ValueError, UnicodeError):
        return ""


def safe_urljoin(page_url: str, href: str) -> str:
    raw = (href or "").strip()
    if not raw or raw.lower().startswith(SKIPPED_HREF_PREFIXES):
        return ""
    try:
        return normalize_coverage_url(urljoin(page_url, raw))
    except (TypeError, ValueError, UnicodeError):
        return ""


def domain_from_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = f"https://{raw}"
    normalized = normalize_coverage_url(raw)
    if not normalized:
        return ""
    try:
        host = (urlparse(normalized).hostname or "").lower()
    except ValueError:
        return ""
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


def split_search_queries(raw: str) -> list[str]:
    queries = []
    seen = set()

    for part in re.split(r"\r\n?|\n|\|\|", raw or ""):
        query = part.strip()
        key = query.casefold()

        if not query or key in seen:
            continue

        seen.add(key)
        queries.append(query)

    return queries


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


def build_dated_query(
    query: str,
    date_from: str = "",
    date_to: str = "",
) -> str:
    parts = [query.strip()]

    if date_from:
        start = datetime.strptime(date_from, "%Y-%m-%d")
        parts.append(f"after:{(start - timedelta(days=1)):%Y-%m-%d}")

    if date_to:
        end = datetime.strptime(date_to, "%Y-%m-%d")
        parts.append(f"before:{(end + timedelta(days=1)):%Y-%m-%d}")

    return " ".join(parts)


def build_news_tbs(date_from: str = "", date_to: str = "") -> str:
    filters = []
    if date_from and date_to:
        start = datetime.strptime(date_from, "%Y-%m-%d")
        end = datetime.strptime(date_to, "%Y-%m-%d")
        filters.extend([
            "cdr:1",
            f"cd_min:{start:%m/%d/%Y}",
            f"cd_max:{end:%m/%d/%Y}",
        ])

    # Sort by date and include similar syndicated news results.
    filters.extend(["sbd:1", "nsd:1"])
    return ",".join(filters)


def build_date_windows(date_from: str, date_to: str) -> list[tuple[str, str]]:
    if not date_from or not date_to:
        return [(date_from, date_to)]

    start = datetime.strptime(date_from, "%Y-%m-%d")
    end = datetime.strptime(date_to, "%Y-%m-%d")
    if start > end:
        raise ValueError("From date cannot be after To date")

    windows = []
    cursor = start
    while cursor <= end:
        window_end = min(
            cursor + timedelta(days=SERPAPI_DATE_SLICE_DAYS - 1),
            end,
        )
        windows.append((
            cursor.strftime("%Y-%m-%d"),
            window_end.strftime("%Y-%m-%d"),
        ))
        cursor = window_end + timedelta(days=1)

    return windows


def request_serpapi(url: str, params: dict):
    last_error = None

    for attempt in range(1, SERPAPI_NETWORK_RETRIES + 1):
        try:
            return requests.get(
                url,
                params=params,
                timeout=(SERPAPI_CONNECT_TIMEOUT, SERPAPI_READ_TIMEOUT),
            )
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_error = exc
            if attempt < SERPAPI_NETWORK_RETRIES:
                time.sleep(5 * attempt)

    raise RuntimeError(
        f"SerpApi remained unavailable after "
        f"{SERPAPI_NETWORK_RETRIES} attempts: {last_error}"
    ) from last_error


def log_serpapi_page(
    state: dict,
    data: dict,
    page_results: list[dict],
    searches_used: int,
    cumulative_links: int,
    new_unique_links: int,
    new_source_links: int,
) -> None:
    parameters = data.get("search_parameters", {}) or {}
    metadata = data.get("search_metadata", {}) or {}
    pagination = data.get("serpapi_pagination", {}) or {}
    page_links = [
        str(item.get("link", "")).strip()
        for item in page_results
        if str(item.get("link", "")).strip()
    ]

    payload = {
        "search_id": metadata.get("id", ""),
        "query": state["query"],
        "dated_query": state["dated_query"],
        "search_source": state["search_source"],
        "search_scope": state["search_scope"],
        "window_from": state["window_from"],
        "window_to": state["window_to"],
        "page_number": state["page_number"],
        "start": parameters.get("start", 0),
        "results_returned": len(page_results),
        "organic_results": (
            len(page_results) if state["search_source"] == "web" else 0
        ),
        "news_results": (
            len(page_results) if state["search_source"] == "news" else 0
        ),
        "links_returned": len(page_links),
        "unique_links_on_page": len(set(page_links)),
        "new_unique_links": new_unique_links,
        "new_source_links": new_source_links,
        "cumulative_links": cumulative_links,
        "has_next_page": bool(pagination.get("next")),
        "empty_page_retry": state["empty_page_retries"],
        "no_cache": state["empty_page_retries"] >= 1,
        "offset_fallback": (
            state["empty_page_retries"] >= 2
            and state["page_number"] == 1
            and state["search_source"] == "web"
        ),
        "forced_pagination": state["forced_pagination"],
        "consecutive_duplicate_pages": state[
            "consecutive_duplicate_pages"
        ],
        "searches_used": searches_used,
        "error": data.get("error", ""),
    }
    print("[SERPAPI_PAGE] " + json.dumps(payload, sort_keys=True), flush=True)


def serpapi_search_all(
    queries: list[str],
    date_from: str = "",
    date_to: str = "",
    progress_callback=None,
    reserved_searches: int = 0,
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
        weekly_windows = build_date_windows(date_from, date_to)
        search_windows = [("full_range", date_from, date_to)]

        if weekly_windows != [(date_from, date_to)]:
            search_windows.extend(
                ("weekly", window_from, window_to)
                for window_from, window_to in weekly_windows
            )

        for search_scope, window_from, window_to in search_windows:
            dated_query = build_dated_query(
                query,
                date_from=window_from,
                date_to=window_to,
            )
            common_params = {
                "api_key": SERPAPI_API_KEY,
                "num": SERPAPI_RESULTS_PER_PAGE,
                "google_domain": SERPAPI_GOOGLE_DOMAIN,
                "gl": SERPAPI_GL,
                "hl": SERPAPI_HL,
                "filter": "0",
            }
            news_query = (
                query.strip()
                if window_from and window_to
                else dated_query
            )
            source_params = (
                (
                    "news",
                    "news_results",
                    news_query,
                    {
                        **common_params,
                        "engine": "google",
                        "tbm": "nws",
                        "q": news_query,
                        "tbs": build_news_tbs(window_from, window_to),
                    },
                ),
                (
                    "web",
                    "organic_results",
                    dated_query,
                    {
                        **common_params,
                        "engine": "google",
                        "q": dated_query,
                    },
                ),
            )

            for search_source, result_key, displayed_query, params in source_params:
                queue.append({
                    "query": query.strip(),
                    "dated_query": displayed_query,
                    "search_source": search_source,
                    "result_key": result_key,
                    "search_scope": search_scope,
                    "window_from": window_from,
                    "window_to": window_to,
                    "params": params,
                    "next_url": "",
                    "visited_pages": set(),
                    "empty_page_retries": 0,
                    "page_number": 1,
                    "forced_start": None,
                    "forced_pagination": False,
                    "seen_result_urls": set(),
                    "consecutive_duplicate_pages": 0,
                })

    results = []
    diagnostics = []
    telemetry_seen_urls = set()
    searches_used = 0
    stop_reason = "all_pages_searched"

    while queue:
        capacity = get_serpapi_capacity()
        if capacity["monthly_left"] <= reserved_searches:
            stop_reason = "country_search_reserve_reached"
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
        retry_stage = state["empty_page_retries"]
        retry_params = {}
        if retry_stage >= 1:
            retry_params["no_cache"] = "true"
        if (
            retry_stage >= 2
            and state["page_number"] == 1
            and state["search_source"] == "web"
        ):
            retry_params["start"] = 1
        elif state["forced_start"] is not None:
            retry_params["start"] = state["forced_start"]

        if state["next_url"]:
            request_params = {
                "api_key": SERPAPI_API_KEY,
                **retry_params,
            }
            request_key = json.dumps({
                "url": state["next_url"],
                **request_params,
            }, sort_keys=True)
            if request_key in state["visited_pages"]:
                continue
            state["visited_pages"].add(request_key)
            response = request_serpapi(
                state["next_url"],
                request_params,
            )
        else:
            request_params = {
                **state["params"],
                **retry_params,
            }
            request_key = json.dumps(request_params, sort_keys=True)
            if request_key in state["visited_pages"]:
                continue
            state["visited_pages"].add(request_key)
            response = request_serpapi(
                SERPAPI_URL,
                request_params,
            )

        if response.status_code == 429:
            state["visited_pages"].discard(request_key)
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
        metadata = data.get("search_metadata", {}) or {}
        information = data.get("search_information", {}) or {}
        search_status = str(metadata.get("status", "")).strip()
        search_error = str(data.get("error", "")).strip()
        search_id = str(metadata.get("id", "")).strip()
        page_results = data.get(state["result_key"], []) or []

        is_empty_error = (
            "hasn't returned any results for this query"
            in search_error.lower()
        )
        is_empty_page = is_empty_error or (
            not search_error
            and search_status.lower() != "error"
            and not page_results
        )

        if is_empty_page:
            log_serpapi_page(
                state=state,
                data=data,
                page_results=[],
                searches_used=searches_used,
                cumulative_links=len(telemetry_seen_urls),
                new_unique_links=0,
                new_source_links=0,
            )

        next_retry_stage = None
        if is_empty_page and state["empty_page_retries"] == 0:
            next_retry_stage = 1
        elif (
            is_empty_page
            and state["empty_page_retries"] == 1
            and state["page_number"] == 1
            and state["search_source"] == "web"
        ):
            next_retry_stage = 2

        if next_retry_stage is not None:
            state["empty_page_retries"] = next_retry_stage
            queue.appendleft(state)
            time.sleep(2 ** next_retry_stage)
            continue

        if is_empty_page:
            diagnostics.append({
                "search_id": search_id,
                "status": search_status,
                "query": state["query"],
                "dated_query": state["dated_query"],
                "search_source": state["search_source"],
                "search_scope": state["search_scope"],
                "window_from": state["window_from"],
                "window_to": state["window_to"],
                "page_number": state["page_number"],
                "page_start": (data.get("search_parameters", {}) or {}).get(
                    "start",
                    0,
                ),
                "query_displayed": information.get("query_displayed", ""),
                "organic_results_state": information.get(
                    "organic_results_state",
                    "Fully empty",
                ),
                "organic_results_count": 0,
                "news_results_count": 0,
                "results_count": 0,
                "links_returned": 0,
                "new_unique_links": 0,
                "new_source_links": 0,
                "cumulative_unique_links": len(telemetry_seen_urls),
                "has_next_page": False,
                "pagination_complete": bool(state["next_url"]),
                "forced_pagination": state["forced_pagination"],
                "consecutive_duplicate_pages": state[
                    "consecutive_duplicate_pages"
                ],
            })
            continue

        if search_error or search_status.lower() == "error":
            message = search_error or "SerpApi returned an unsuccessful search"
            identifier = f" ({search_id})" if search_id else ""
            raise RuntimeError(f"SerpApi search failed{identifier}: {message}")

        page_links = [
            str(item.get("link", "")).strip()
            for item in page_results
            if str(item.get("link", "")).strip()
        ]
        normalized_page_links = set()
        for link in page_links:
            normalized = normalize_coverage_url(link)
            if normalized:
                normalized_page_links.add(normalized)
        new_source_links = normalized_page_links - state["seen_result_urls"]
        state["seen_result_urls"].update(normalized_page_links)
        if new_source_links:
            state["consecutive_duplicate_pages"] = 0
        elif page_results:
            state["consecutive_duplicate_pages"] += 1

        new_page_links = normalized_page_links - telemetry_seen_urls
        telemetry_seen_urls.update(normalized_page_links)

        log_serpapi_page(
            state=state,
            data=data,
            page_results=page_results,
            searches_used=searches_used,
            cumulative_links=len(telemetry_seen_urls),
            new_unique_links=len(new_page_links),
            new_source_links=len(new_source_links),
        )
        diagnostics.append({
            "search_id": search_id,
            "status": search_status,
            "query": state["query"],
            "dated_query": state["dated_query"],
            "search_source": state["search_source"],
            "search_scope": state["search_scope"],
            "window_from": state["window_from"],
            "window_to": state["window_to"],
            "page_number": state["page_number"],
            "page_start": (data.get("search_parameters", {}) or {}).get(
                "start",
                0,
            ),
            "query_displayed": information.get("query_displayed", ""),
            "organic_results_state": information.get("organic_results_state", ""),
            "organic_results_count": (
                len(page_results) if state["search_source"] == "web" else 0
            ),
            "news_results_count": (
                len(page_results) if state["search_source"] == "news" else 0
            ),
            "results_count": len(page_results),
            "links_returned": len(page_links),
            "new_unique_links": len(new_page_links),
            "new_source_links": len(new_source_links),
            "cumulative_unique_links": len(telemetry_seen_urls),
            "has_next_page": bool(
                (data.get("serpapi_pagination", {}) or {}).get("next")
            ),
            "forced_pagination": state["forced_pagination"],
            "consecutive_duplicate_pages": state[
                "consecutive_duplicate_pages"
            ],
        })

        for item in page_results:
            link = normalize_coverage_url(item.get("link", ""))
            if not link:
                continue
            publication_hint = item.get("source", "")
            if isinstance(publication_hint, dict):
                publication_hint = publication_hint.get("name", "")
            results.append({
                "title": item.get("title", ""),
                "url": link,
                "snippet": item.get("snippet", ""),
                "date": (
                    item.get("published_at")
                    or item.get("iso_date")
                    or item.get("date", "")
                ),
                "domain": domain_from_url(link),
                "search_query": state["query"],
                "search_source": state["search_source"],
                "publication_hint": str(publication_hint or ""),
            })

        next_url = (data.get("serpapi_pagination", {}) or {}).get("next", "")
        duplicate_limit_reached = (
            state["consecutive_duplicate_pages"] >= 2
        )
        full_page = len(page_results) >= SERPAPI_RESULTS_PER_PAGE
        state["empty_page_retries"] = 0

        if duplicate_limit_reached:
            pass
        elif page_results and next_url:
            state["next_url"] = next_url
            state["forced_start"] = None
            state["forced_pagination"] = False
            state["page_number"] += 1
            queue.append(state)
        elif full_page:
            state["next_url"] = ""
            state["forced_start"] = (
                state["page_number"] * SERPAPI_RESULTS_PER_PAGE
            )
            state["forced_pagination"] = True
            state["page_number"] += 1
            queue.append(state)

        emit(
            "searching",
            searches_used,
            starting_budget,
            f"Searched {searches_used} Google result pages",
        )

    if searches_used and not results:
        stop_reason = "no_google_results"

    try:
        final_capacity = get_serpapi_capacity()
    except (requests.RequestException, ValueError, TypeError):
        final_capacity = {
            "monthly_left": max(0, starting_budget - searches_used),
        }
    return {
        "results": results,
        "searches_used": searches_used,
        "searches_remaining": final_capacity["monthly_left"],
        "stop_reason": stop_reason,
        "search_diagnostics": diagnostics,
    }


def validate_coverage_url(url: str) -> tuple[bool, str]:
    normalized = normalize_coverage_url(url)
    if not normalized:
        return False, "invalid_url"
    parsed = urlparse(normalized)
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
        normalized = safe_urljoin(
            page_url,
            str(anchor.get("href", "")),
        )
        if not normalized or normalized in seen:
            continue
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

    content_type = res.headers.get("Content-Type", "").lower()
    if content_type and not any(
        allowed in content_type
        for allowed in ("text/html", "application/xhtml+xml")
    ):
        return {"ok": False, "url": url, "error": "non_html_response"}
    if len(res.content) > MAX_ARTICLE_RESPONSE_BYTES:
        return {"ok": False, "url": url, "error": "response_too_large"}

    soup = BeautifulSoup(res.text, "html.parser")
    metadata = extract_page_metadata(res.text)
    country_hint = extract_metadata_country(soup)
    json_ld_body = _extract_json_ld_article_body(soup)

    for tag in soup(["script", "style", "noscript", "svg", "form", "nav", "footer", "aside"]):
        tag.decompose()

    html_title = soup.title.get_text(" ", strip=True) if soup.title else ""
    canonical = safe_urljoin(
        res.url or url,
        metadata.get("canonical_url") or res.url or url,
    )
    if not canonical:
        canonical = normalize_coverage_url(res.url or url)
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
        "url": canonical,
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
        "country_hint": country_hint,
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
        key = normalize_coverage_url(row.get("article_url", ""))
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


def save_report_rows(
    rows: list[dict],
    db: GoogleSheetsDB | None = None,
) -> None:
    if not rows:
        return
    db = db or GoogleSheetsDB()
    ws = ensure_report_sheet(db)
    payload = [[row.get(header, "") for header in REPORT_HEADERS] for row in rows]
    ws.append_rows(payload, value_input_option="RAW")


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
        reserved_searches=SERPAPI_COUNTRY_RESERVE,
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
        try:
            page = fetch_page(result["article_url"])
        except Exception as exc:
            print(
                "[ARTICLE_ERROR] "
                f"url={result['article_url']!r} "
                f"error={type(exc).__name__}",
                flush=True,
            )
            page = {
                "ok": False,
                "url": result["article_url"],
                "error": f"unexpected_{type(exc).__name__}",
            }
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
            "country_source": "",
            "country_confidence": "",
            "country_lookup_key": "",
            "_country_hint": page.get("country_hint"),
            "_publication_hint": result.get("publication_hint", ""),
        }
        if evidence["is_relevant"]:
            confirmed.append(row)
        else:
            review_results.append(row)

    confirmed = dedupe_results(confirmed)
    review_results = dedupe_results(review_results)
    warnings = []
    db = None
    country_stats = {}
    emit(
        "countries",
        0,
        len(confirmed),
        "Resolving publication countries",
    )
    try:
        db = GoogleSheetsDB()
        country_stats = enrich_publication_countries(
            confirmed,
            db=db,
            serpapi_api_key=SERPAPI_API_KEY,
            google_domain=SERPAPI_GOOGLE_DOMAIN,
            gl=SERPAPI_GL,
            hl=SERPAPI_HL,
            google_budget=search_run["searches_remaining"],
        )
    except Exception as exc:
        print(
            "[COUNTRY_ENRICHMENT] "
            f"status=failed error={type(exc).__name__}: {exc}"
        )
        warnings.append(
            f"Country enrichment failed: {type(exc).__name__}: {exc}"
        )
    for row in confirmed + review_results:
        row.pop("_country_hint", None)
        row.pop("_publication_hint", None)

    domains = sorted({row["domain"] for row in confirmed if row.get("domain")})
    skip_traffic = os.getenv(
        "SKIP_PUBLICATION_TRAFFIC",
        "",
    ).strip().lower() in {"1", "true", "yes", "on"}

    if skip_traffic:
        emit(
            "traffic",
            len(domains),
            len(domains),
            "Skipping publication traffic lookup",
        )
        traffic = {}
    else:
        emit("traffic", 0, len(domains), "Looking up publication traffic")
        try:
            traffic = lookup_publication_traffic(domains)
        except Exception as exc:
            traffic = {}
            warnings.append(f"Traffic lookup failed: {type(exc).__name__}")

    for row in confirmed:
        data = traffic.get(row["domain"], {})
        row["monthly_visits"] = data.get("monthly_visits") or ""
        row["monthly_visits_display"] = (
            data.get("monthly_visits_display") or "N/A"
        )
        row["traffic_source"] = data.get("source") or "unavailable"

    rows_to_save = confirmed + review_results
    emit("saving", 0, len(rows_to_save), "Saving report")
    if db is not None:
        try:
            save_report_rows(rows_to_save, db=db)
        except Exception as exc:
            warnings.append(
                f"Google Sheets save failed: {type(exc).__name__}"
            )
    else:
        warnings.append("Google Sheets save skipped: database unavailable")

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
    try:
        final_capacity = get_serpapi_capacity()
    except (requests.RequestException, ValueError, TypeError):
        country_searches = int(
            country_stats.get("google_searches_used", 0) or 0
        )
        final_capacity = {
            "monthly_left": max(
                0,
                int(search_run["searches_remaining"] or 0)
                - country_searches,
            ),
        }
        warnings.append("SerpApi capacity refresh failed; using local estimate")
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
        "searches_remaining": final_capacity["monthly_left"],
        "search_stop_reason": search_run["stop_reason"],
        "search_diagnostics": search_run["search_diagnostics"],
        "country_stats": country_stats,
        "warnings": warnings,
    }
