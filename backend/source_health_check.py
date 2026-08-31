"""Biweekly sitemap and RSS health diagnostics with conservative repairs."""

from __future__ import annotations

import argparse
import copy
import gzip
import json
import os
import re
import xml.etree.ElementTree as ET
from calendar import timegm
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import feedparser
import gspread
import requests
from google.oauth2.service_account import Credentials
from newspaper import Article

from article_quality import clean_article_text, validate_article_content


SCHEMA_VERSION = 3
SOURCE_SHEET = os.getenv("SOURCE_CONFIG_SHEET", "Source Lists")
HISTORY_SHEET = os.getenv("SOURCE_HEALTH_HISTORY_SHEET", "Source Health History")
REQUEST_TIMEOUT = int(os.getenv("SOURCE_HEALTH_TIMEOUT", "12"))
RECENT_DAYS = int(os.getenv("SOURCE_HEALTH_RECENT_DAYS", "30"))
MAX_SITEMAP_URLS = int(os.getenv("SOURCE_HEALTH_MAX_SITEMAP_URLS", "250"))
MAX_CHILD_SITEMAPS = int(os.getenv("SOURCE_HEALTH_MAX_CHILD_SITEMAPS", "6"))
SOURCE_DISABLE_THRESHOLD = int(
    os.getenv("SOURCE_HEALTH_SOURCE_DISABLE_THRESHOLD", "2")
)
SITEMAP_SELECTION_MARGIN = int(
    os.getenv("SOURCE_HEALTH_SITEMAP_SELECTION_MARGIN", "15")
)

USER_AGENT = "Mozilla/5.0 (compatible; LuxuryRoundupSourceHealth/1.0)"
COMMON_SITEMAP_PATHS = (
    "/sitemap.xml", "/sitemap_index.xml", "/sitemap-index.xml",
    "/news-sitemap.xml", "/sitemap/news.xml", "/wp-sitemap.xml",
)
PERMANENT_RSS_FAILURES = {
    "http_404", "http_410", "html_not_feed", "invalid_xml",
    "feed_parse_error",
}
TEMPORARY_REASONS = {
    "http_401", "http_403", "http_429", "timeout", "request_error",
    "server_error",
}
SOURCE_HEALTH_COLUMNS = (
    "rss_active", "sitemap_health_status", "rss_health_status",
    "rss_permanent_failures", "rss_disabled_reason",
    "source_health_managed", "source_permanent_failures",
    "source_disabled_reason", "source_last_checked_at",
)
HISTORY_HEADERS = (
    "run_id", "checked_at", "list_name", "publication", "source_type",
    "configured_url", "final_url", "state", "reason", "action",
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value: datetime | None) -> str | None:
    if not value:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def host_of(url: str) -> str:
    host = (urlparse(str(url or "")).hostname or "").lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host


def same_domain(left: str, right: str) -> bool:
    left_host = host_of(left)
    right_host = host_of(right)
    if not left_host or not right_host:
        return False
    return (
        left_host == right_host
        or left_host.endswith(f".{right_host}")
        or right_host.endswith(f".{left_host}")
    )


def local_name(tag: str) -> str:
    return str(tag or "").rsplit("}", 1)[-1].lower()


def classify_http_reason(status: int) -> str:
    if status in {401, 403, 404, 410, 429}:
        return f"http_{status}"
    if status >= 500:
        return "server_error"
    return f"http_{status}"


def fetch(session: requests.Session, url: str) -> dict[str, Any]:
    result = {
        "requested_url": url, "final_url": url, "http_status": 0,
        "body": "", "redirects": [], "reason": "request_error",
    }
    try:
        response = session.get(
            url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT,
            allow_redirects=True,
        )
        raw_body = getattr(response, "content", None)
        if raw_body is not None:
            if raw_body[:2] == b"\x1f\x8b":
                raw_body = gzip.decompress(raw_body)
            body = raw_body.decode(response.encoding or "utf-8", errors="replace")
        else:
            body = response.text or ""
        result.update({
            "final_url": response.url or url,
            "http_status": int(response.status_code),
            "body": body,
            "redirects": [item.url for item in response.history],
            "reason": (
                "ok" if response.status_code == 200
                else classify_http_reason(response.status_code)
            ),
        })
    except requests.Timeout:
        result["reason"] = "timeout"
    except requests.RequestException:
        result["reason"] = "request_error"
    return result


def extract_sample_article(session: requests.Session, url: str) -> dict[str, Any]:
    result = {
        "attempted": bool(url), "url": url or None, "ok": False,
        "character_count": 0,
        "reason": "no_article_urls" if not url else "sample_extraction_failed",
    }
    if not url:
        return result
    fetched = fetch(session, url)
    if fetched["http_status"] != 200:
        result["reason"] = fetched["reason"]
        return result
    try:
        article = Article(fetched["final_url"])
        article.download_state = 2
        article.html = fetched["body"]
        article.parse()
        text = clean_article_text(article.text)
        result["character_count"] = len(text)
        valid, reason = validate_article_content(text)
        result["ok"] = valid
        result["reason"] = "ok" if valid else reason
    except Exception:
        result["reason"] = "sample_extraction_failed"
    return result


def _sitemap_items(
    session: requests.Session,
    url: str,
    depth: int = 0,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    fetched = fetch(session, url)
    metadata = {
        "configured_url": url,
        "final_url": fetched["final_url"],
        "http_status": fetched["http_status"],
        "redirects": fetched["redirects"],
        "valid_xml": False,
        "sitemap_type": None,
        "reason": fetched["reason"],
    }
    if fetched["http_status"] != 200:
        return metadata, []
    if re.search(r"<\s*html\b", fetched["body"][:1500], flags=re.I):
        metadata["reason"] = "html_not_xml"
        return metadata, []
    try:
        root = ET.fromstring(fetched["body"].encode("utf-8", errors="ignore"))
    except ET.ParseError:
        metadata["reason"] = "invalid_xml"
        return metadata, []

    root_type = local_name(root.tag)
    if root_type not in {"urlset", "sitemapindex"}:
        metadata["reason"] = "invalid_xml"
        return metadata, []
    metadata.update({
        "valid_xml": True, "sitemap_type": root_type, "reason": "ok",
    })

    entries: list[dict[str, Any]] = []
    if root_type == "urlset":
        for element in root:
            if local_name(element.tag) != "url":
                continue
            values = {
                local_name(child.tag): (child.text or "").strip()
                for child in element
            }
            if values.get("loc"):
                entries.append({
                    "url": values["loc"], "lastmod": values.get("lastmod"),
                })
            if len(entries) >= MAX_SITEMAP_URLS:
                break
        return metadata, entries

    if depth >= 2:
        metadata["reason"] = "no_article_urls"
        return metadata, []
    child_urls = []
    for element in root:
        if local_name(element.tag) != "sitemap":
            continue
        values = {
            local_name(child.tag): (child.text or "").strip()
            for child in element
        }
        if values.get("loc"):
            child_urls.append((values["loc"], parse_datetime(values.get("lastmod"))))
    child_urls.sort(
        key=lambda item: item[1] or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    for child_url, _ in child_urls[:MAX_CHILD_SITEMAPS]:
        _, child_entries = _sitemap_items(session, child_url, depth + 1)
        entries.extend(child_entries)
        if len(entries) >= MAX_SITEMAP_URLS:
            break
    if not entries:
        metadata["reason"] = "no_article_urls"
    return metadata, entries[:MAX_SITEMAP_URLS]


def validate_sitemap(
    session: requests.Session,
    url: str,
    base_url: str,
    checked_at: datetime,
) -> dict[str, Any]:
    metadata, entries = _sitemap_items(session, url)
    dates = [parse_datetime(item.get("lastmod")) for item in entries]
    dates = [value for value in dates if value]
    cutoff = checked_at - timedelta(days=RECENT_DAYS)
    recent_count = sum(value >= cutoff for value in dates)
    sample = {
        "attempted": False, "url": None, "ok": False,
        "character_count": 0, "reason": "no_article_urls",
    }
    sample_candidates = sorted(
        entries,
        key=lambda item: (
            parse_datetime(item.get("lastmod"))
            or datetime.min.replace(tzinfo=timezone.utc)
        ),
        reverse=True,
    )
    for item in sample_candidates[:5]:
        sample = extract_sample_article(session, item["url"])
        if sample["ok"]:
            break

    same_host = same_domain(metadata["final_url"], base_url)
    passed = bool(
        metadata["valid_xml"] and entries and same_host and sample["ok"]
    )
    if not same_host and metadata["http_status"] == 200:
        metadata["reason"] = "cross_domain_redirect"
    elif metadata["valid_xml"] and entries and not sample["ok"]:
        metadata["reason"] = "sample_extraction_failed"
    if passed and dates and recent_count == 0:
        state = "stale"
        metadata["reason"] = "no_recent_entries"
    elif passed:
        state = "healthy"
    else:
        state = (
            "temporary_error" if metadata["reason"] in TEMPORARY_REASONS
            else "permanent_error"
        )
    return {
        **metadata,
        "state": state,
        "url_count": len(entries),
        "recent_url_count": recent_count,
        "latest_last_modified": iso_utc(max(dates)) if dates else None,
        "sample_extraction": sample,
        "passed": passed,
    }


def discover_sitemap_urls(
    session: requests.Session,
    base_url: str,
) -> tuple[str, list[str], set[str]]:
    robots_url = urljoin(base_url.rstrip("/") + "/", "robots.txt")
    candidates: list[str] = []
    robots_declared: set[str] = set()
    robots = fetch(session, robots_url)
    if robots["http_status"] == 200:
        for line in robots["body"].splitlines():
            if line.strip().lower().startswith("sitemap:"):
                candidate = line.split(":", 1)[1].strip()
                if candidate:
                    candidates.append(candidate)
                    robots_declared.add(candidate)
    candidates.extend(
        urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
        for path in COMMON_SITEMAP_PATHS
    )
    unique: list[str] = []
    for candidate in candidates:
        if candidate not in unique and same_domain(candidate, base_url):
            unique.append(candidate)
    return robots_url, unique, robots_declared


def sitemap_candidate_score(result: dict[str, Any]) -> int:
    if not result.get("passed"):
        return -1
    final_url = str(result.get("final_url") or "").lower()
    score = 0
    if result.get("declared_in_robots"):
        score += 100
    if "news" in final_url:
        score += 40
    if "sitemap_index" in final_url or "sitemap-index" in final_url:
        score += 15
    score += min(int(result.get("recent_url_count") or 0), 30)
    score += min(int(result.get("url_count") or 0) // 20, 20)
    if result.get("sample_extraction", {}).get("ok"):
        score += 30
    return score


def choose_sitemap_replacement(
    results: list[dict[str, Any]],
) -> tuple[str | None, str]:
    passing = {}
    for result in results:
        if result.get("passed"):
            existing = passing.get(result["final_url"])
            if (
                existing is None
                or sitemap_candidate_score(result)
                > sitemap_candidate_score(existing)
            ):
                passing[result["final_url"]] = result
    if len(passing) == 1:
        return next(iter(passing)), "one_valid_replacement"
    if len(passing) > 1:
        ranked = sorted(
            passing.values(), key=sitemap_candidate_score, reverse=True
        )
        if (
            sitemap_candidate_score(ranked[0])
            - sitemap_candidate_score(ranked[1])
            >= SITEMAP_SELECTION_MARGIN
        ):
            return ranked[0]["final_url"], "highest_confidence_replacement"
        return None, "multiple_ambiguous_replacements"
    return None, "no_valid_replacement"


def validate_rss(
    session: requests.Session,
    url: str,
    base_url: str,
    checked_at: datetime,
) -> dict[str, Any]:
    fetched = fetch(session, url)
    result = {
        "configured_url": url,
        "final_url": fetched["final_url"],
        "http_status": fetched["http_status"],
        "redirects": fetched["redirects"],
        "state": "temporary_error",
        "reason": fetched["reason"],
        "valid_xml": False,
        "entry_count": 0,
        "recent_entry_count": 0,
        "latest_entry_at": None,
        "sample_extraction": {
            "attempted": False, "url": None, "ok": False,
            "character_count": 0, "reason": "no_entries",
        },
    }
    if fetched["http_status"] != 200:
        if fetched["reason"] in PERMANENT_RSS_FAILURES:
            result["state"] = "permanent_error"
        return result
    if re.search(r"<\s*html\b", fetched["body"][:1500], flags=re.I):
        result.update({"state": "permanent_error", "reason": "html_not_feed"})
        return result

    parsed = feedparser.parse(fetched["body"])
    entries = list(getattr(parsed, "entries", []) or [])
    if getattr(parsed, "bozo", False) and not entries:
        result.update({
            "state": "permanent_error", "reason": "feed_parse_error",
        })
        return result
    result["valid_xml"] = True
    result["entry_count"] = len(entries)
    if not entries:
        result.update({"state": "stale", "reason": "no_entries"})
        return result

    entry_dates: list[datetime] = []
    for entry in entries:
        value = None
        for field in ("published_parsed", "updated_parsed"):
            parsed_value = entry.get(field)
            if parsed_value:
                value = datetime.fromtimestamp(timegm(parsed_value), timezone.utc)
                break
        if not value:
            for field in ("published", "updated"):
                value = parse_datetime(entry.get(field))
                if value:
                    break
        if value:
            entry_dates.append(value)
    cutoff = checked_at - timedelta(days=RECENT_DAYS)
    result["recent_entry_count"] = sum(value >= cutoff for value in entry_dates)
    result["latest_entry_at"] = iso_utc(max(entry_dates)) if entry_dates else None

    for entry in entries[:5]:
        link = str(entry.get("link", "")).strip()
        if not link:
            continue
        result["sample_extraction"] = extract_sample_article(session, link)
        if result["sample_extraction"]["ok"]:
            break

    if entry_dates and not result["recent_entry_count"]:
        result.update({"state": "stale", "reason": "no_recent_entries"})
    elif not result["sample_extraction"]["ok"]:
        result.update({
            "state": "attention", "reason": "sample_extraction_failed",
        })
    else:
        result.update({"state": "healthy", "reason": "ok"})
    return result


def apply_rss_result(
    record: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any] | None:
    before_active = (
        str(record.get("rss_active", "TRUE") or "TRUE").upper() != "FALSE"
    )
    try:
        before_failures = int(record.get("rss_permanent_failures") or 0)
    except (TypeError, ValueError):
        before_failures = 0
    result["active_before"] = before_active
    result["permanent_failures_before"] = before_failures

    action = None
    if result["state"] in {"healthy", "stale"}:
        record["rss_health_status"] = result["state"]
        record["rss_permanent_failures"] = "0"
        previously_disabled_reason = str(
            record.get("rss_disabled_reason") or ""
        ).strip()
        record["rss_disabled_reason"] = ""
        result["permanent_failures_after"] = 0
        if not before_active and previously_disabled_reason:
            record["rss_active"] = "TRUE"
            action = {
                "type": "rss_reactivated",
                "old_value": "FALSE",
                "new_value": "TRUE",
                "reason": "The feed recovered and is usable again",
            }
    elif result["reason"] in PERMANENT_RSS_FAILURES:
        failures = before_failures + 1
        record["rss_health_status"] = "permanent_error"
        record["rss_permanent_failures"] = str(failures)
        result["permanent_failures_after"] = failures
        if failures >= 2 and before_active:
            record["rss_active"] = "FALSE"
            record["rss_disabled_reason"] = result["reason"]
            action = {
                "type": "rss_disabled",
                "old_value": "TRUE",
                "new_value": "FALSE",
                "reason": (
                    f"{result['reason']} confirmed on {failures} "
                    "consecutive diagnostic runs"
                ),
            }
    else:
        record["rss_health_status"] = result["state"]
        record["rss_permanent_failures"] = "0"
        result["permanent_failures_after"] = 0
    result["active_after"] = (
        str(record.get("rss_active", "TRUE") or "TRUE").upper() != "FALSE"
    )
    result["disabled_reason"] = record.get("rss_disabled_reason") or None
    return action


def _column_name(number: int) -> str:
    value = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        value = chr(65 + remainder) + value
    return value


def ensure_headers(
    worksheet,
    required: tuple[str, ...],
    write: bool = True,
) -> list[str]:
    headers = list(worksheet.row_values(1))
    for name in required:
        if name not in headers:
            headers.append(name)
    if headers and write:
        worksheet.update(f"A1:{_column_name(len(headers))}1", [headers])
    return headers


def source_records(
    worksheet,
    headers: list[str],
) -> list[tuple[int, dict[str, str]]]:
    values = worksheet.get_all_values()
    records = []
    for row_number, row in enumerate(values[1:], start=2):
        padded = row + [""] * max(0, len(headers) - len(row))
        records.append((row_number, dict(zip(headers, padded))))
    return records


def save_source_records(
    worksheet,
    headers: list[str],
    updates: list[tuple[int, dict[str, Any]]],
) -> None:
    mutable = ("active", "sitemap_url") + SOURCE_HEALTH_COLUMNS
    batch = []
    for row_number, record in updates:
        for field in mutable:
            if field not in headers:
                continue
            column = _column_name(headers.index(field) + 1)
            batch.append({
                "range": f"{column}{row_number}",
                "values": [[str(record.get(field, "") or "")]],
            })
    if batch:
        worksheet.batch_update(batch)


def _is_true(value: Any, default: bool = True) -> bool:
    text = str(value or "").strip().upper()
    return default if not text else text != "FALSE"


def endpoint_is_usable(result: dict[str, Any] | None) -> bool:
    return bool(result and result.get("state") in {"healthy", "stale"})


def should_check_source(record: dict[str, Any]) -> bool:
    if not str(record.get("base_url", "")).strip():
        return False
    return _is_true(record.get("active"), default=True) or _is_true(
        record.get("source_health_managed"), default=False
    )


def apply_source_availability_policy(
    record: dict[str, Any],
    sitemap: dict[str, Any],
    rss_feeds: list[dict[str, Any]],
    publication: str,
    actions: list[dict[str, Any]],
    attention: list[dict[str, Any]],
) -> str:
    sitemap_usable = endpoint_is_usable(sitemap)
    rss_usable = any(
        endpoint_is_usable(rss) and rss.get("active_after", True)
        for rss in rss_feeds
    )
    active_before = _is_true(record.get("active"), default=True)
    health_managed = _is_true(
        record.get("source_health_managed"), default=False
    )
    try:
        failures_before = int(record.get("source_permanent_failures") or 0)
    except (TypeError, ValueError):
        failures_before = 0

    if sitemap_usable or rss_usable:
        record["source_permanent_failures"] = "0"
        record["source_disabled_reason"] = ""
        if health_managed:
            record["source_health_managed"] = "FALSE"
        if not active_before and health_managed:
            record["active"] = "TRUE"
            actions.append({
                "type": "source_reactivated",
                "publication": publication,
                "old_value": "FALSE",
                "new_value": "TRUE",
                "reason": "At least one collection route recovered",
            })
        if actions:
            return "repaired"
        return "healthy" if sitemap_usable and rss_usable else "degraded"

    has_temporary_failure = any(
        endpoint.get("state") == "temporary_error"
        for endpoint in [sitemap, *rss_feeds]
    )
    if has_temporary_failure:
        record["source_permanent_failures"] = "0"
        return "retry_pending"

    failures = failures_before + 1
    record["source_permanent_failures"] = str(failures)
    if failures < SOURCE_DISABLE_THRESHOLD:
        return "retry_pending"

    record["active"] = "FALSE"
    record["source_health_managed"] = "TRUE"
    record["source_disabled_reason"] = "no_usable_sitemap_or_rss"
    if active_before:
        actions.append({
            "type": "source_quarantined",
            "publication": publication,
            "old_value": "TRUE",
            "new_value": "FALSE",
            "reason": (
                "No usable sitemap or RSS feed was confirmed on "
                f"{failures} consecutive diagnostic runs"
            ),
        })
    attention.append({
        "source_type": "publication",
        "configured_url": record.get("base_url") or None,
        "reason": "genuinely_unresolved",
        "message": (
            "No safe collection route was found; the source was quarantined "
            "and will continue to be checked for recovery"
        ),
        "candidates": [],
        "unresolved": True,
    })
    return "genuinely_unresolved"


def process_source(
    record: dict[str, Any],
    row_number: int,
    session: requests.Session,
    checked_at: datetime,
    sitemap_cache: dict[tuple[str, str], dict[str, Any]],
    rss_cache: dict[tuple[str, str], dict[str, Any]],
    apply_fixes: bool = True,
) -> dict[str, Any]:
    publication = str(record.get("publication", "")).strip() or "Unknown"
    base_url = str(record.get("base_url", "")).strip()
    configured_sitemap = str(record.get("sitemap_url", "")).strip()
    configured_rss = str(record.get("rss_url", "")).strip()
    actions: list[dict[str, Any]] = []
    attention: list[dict[str, Any]] = []

    sitemap = {
        "configured_url": configured_sitemap or None,
        "state": "not_configured",
        "reason": "not_configured",
        "replacement_search": {
            "attempted": False, "candidates_tested": [],
            "passing_candidates": 0, "selected_url": None,
            "configuration_updated": False,
        },
    }
    if configured_sitemap:
        key = (configured_sitemap, host_of(base_url))
        if key not in sitemap_cache:
            sitemap_cache[key] = validate_sitemap(
                session, configured_sitemap, base_url, checked_at
            )
        sitemap = copy.deepcopy(sitemap_cache[key])

    if sitemap.get("state") not in {"healthy", "stale"}:
        robots_url, discovered, robots_declared = discover_sitemap_urls(
            session, base_url
        )
        tested = []
        for candidate in discovered:
            if candidate == configured_sitemap:
                continue
            key = (candidate, host_of(base_url))
            if key not in sitemap_cache:
                sitemap_cache[key] = validate_sitemap(
                    session, candidate, base_url, checked_at
                )
            candidate_result = copy.deepcopy(sitemap_cache[key])
            candidate_result["declared_in_robots"] = (
                candidate in robots_declared
                or candidate_result.get("final_url") in robots_declared
            )
            candidate_result["selection_score"] = sitemap_candidate_score(
                candidate_result
            )
            tested.append(candidate_result)
        replacement, replacement_reason = choose_sitemap_replacement(tested)
        passing_urls = {
            item["final_url"] for item in tested if item.get("passed")
        }
        sitemap["replacement_search"] = {
            "attempted": True,
            "robots_url": robots_url,
            "candidates_tested": tested,
            "passing_candidates": len(passing_urls),
            "selected_url": replacement,
            "configuration_updated": bool(replacement and apply_fixes),
            "reason": replacement_reason,
        }
        if replacement:
            old_value = configured_sitemap
            record["sitemap_url"] = replacement
            record["sitemap_health_status"] = "healthy"
            selected_result = next(
                item for item in tested if item["final_url"] == replacement
            )
            replacement_search = sitemap["replacement_search"]
            sitemap = copy.deepcopy(selected_result)
            sitemap["configured_url"] = configured_sitemap or None
            sitemap["replacement_search"] = replacement_search
            actions.append({
                "type": "sitemap_replaced" if old_value else "sitemap_added",
                "publication": publication,
                "old_value": old_value,
                "new_value": replacement,
                "reason": (
                    "A same-domain sitemap replacement passed all checks "
                    f"({replacement_reason})"
                ),
            })
        else:
            record["sitemap_health_status"] = sitemap.get(
                "state", "unresolved"
            )
            attention.append({
                "source_type": "sitemap",
                "configured_url": configured_sitemap or None,
                "reason": replacement_reason,
                "message": (
                    "No sufficiently confident, extractable same-domain "
                    "replacement was found"
                ),
                "candidates": sorted(passing_urls),
                "unresolved": False,
            })
    else:
        record["sitemap_health_status"] = sitemap["state"]
        sitemap["replacement_search"] = {
            "attempted": False, "candidates_tested": [],
            "passing_candidates": 0, "selected_url": None,
            "configuration_updated": False,
        }
        if sitemap["state"] == "stale":
            attention.append({
                "source_type": "sitemap",
                "configured_url": configured_sitemap,
                "reason": "no_recent_entries",
                "message": "Sitemap is valid but has no recently modified URLs",
                "candidates": [],
                "unresolved": False,
            })

    rss_feeds = []
    if configured_rss:
        key = (configured_rss, host_of(base_url))
        if key not in rss_cache:
            rss_cache[key] = validate_rss(
                session, configured_rss, base_url, checked_at
            )
        rss = copy.deepcopy(rss_cache[key])
        action = apply_rss_result(record, rss)
        rss_feeds.append(rss)
        if action:
            action["publication"] = publication
            actions.append(action)
        if rss["state"] != "healthy":
            attention.append({
                "source_type": "rss",
                "configured_url": configured_rss,
                "reason": rss["reason"],
                "message": (
                    "RSS feed requires attention" if not action
                    else action["reason"]
                ),
                "candidates": [],
                "unresolved": False,
            })
    else:
        record["rss_health_status"] = "not_configured"

    record["source_last_checked_at"] = iso_utc(checked_at)
    overall = apply_source_availability_policy(
        record, sitemap, rss_feeds, publication, actions, attention
    )
    return {
        "list_name": record.get("list_name", ""),
        "publication": publication,
        "sheet_row": row_number,
        "base_url": base_url,
        "active": _is_true(record.get("active"), default=True),
        "availability": {
            "health_managed": _is_true(
                record.get("source_health_managed"), default=False
            ),
            "permanent_failures": int(
                record.get("source_permanent_failures") or 0
            ),
            "disabled_reason": record.get("source_disabled_reason") or None,
        },
        "overall_status": overall,
        "checked_at": iso_utc(checked_at),
        "sitemap": sitemap,
        "rss_feeds": rss_feeds,
        "actions": actions,
        "attention": attention,
    }


def summarize_sources(sources: list[dict[str, Any]]) -> dict[str, int]:
    actions = [action for source in sources for action in source["actions"]]
    unresolved = []
    for source in sources:
        for item in source["attention"]:
            if item.get("unresolved"):
                unresolved.append({"publication": source["publication"], **item})
    rss_feeds = [rss for source in sources for rss in source["rss_feeds"]]
    return {
        "sources_checked": len(sources),
        "healthy_sources": sum(
            source["overall_status"] == "healthy" for source in sources
        ),
        "repaired_sources": sum(
            source["overall_status"] == "repaired" for source in sources
        ),
        "degraded_sources": sum(
            source["overall_status"] == "degraded" for source in sources
        ),
        "retry_pending_sources": sum(
            source["overall_status"] == "retry_pending" for source in sources
        ),
        "attention_sources": sum(
            bool(source["attention"] or source["actions"]) for source in sources
        ),
        "unresolved_sources": sum(
            source["overall_status"] in {"unresolved", "genuinely_unresolved"}
            for source in sources
        ),
        "quarantined_sources": sum(
            action["type"] == "source_quarantined" for action in actions
        ),
        "reactivated_sources": sum(
            action["type"] == "source_reactivated" for action in actions
        ),
        "sitemaps_checked": sum(
            bool(source["sitemap"].get("configured_url")) for source in sources
        ),
        "healthy_sitemaps": sum(
            source["sitemap"].get("state") == "healthy" for source in sources
        ),
        "sitemaps_replaced": sum(
            action["type"] in {"sitemap_replaced", "sitemap_added"}
            for action in actions
        ),
        "stale_sitemaps": sum(
            source["sitemap"].get("state") == "stale" for source in sources
        ),
        "unresolved_sitemaps": sum(
            item["source_type"] == "sitemap" for item in unresolved
        ),
        "rss_feeds_checked": len(rss_feeds),
        "healthy_rss_feeds": sum(
            item["state"] == "healthy" for item in rss_feeds
        ),
        "stale_rss_feeds": sum(
            item["state"] == "stale" for item in rss_feeds
        ),
        "temporarily_unavailable_rss_feeds": sum(
            item["state"] == "temporary_error" for item in rss_feeds
        ),
        "disabled_rss_feeds": sum(
            action["type"] == "rss_disabled" for action in actions
        ),
    }


def build_report(
    sources: list[dict[str, Any]],
    checked_at: datetime,
    apply_fixes: bool,
    worksheet_name: str,
) -> dict[str, Any]:
    actions = [action for source in sources for action in source["actions"]]
    unresolved = []
    for source in sources:
        for item in source["attention"]:
            if item.get("unresolved"):
                unresolved.append({"publication": source["publication"], **item})
    summary = summarize_sources(sources)
    topic_groups: dict[str, list[dict[str, Any]]] = {}
    for source in sources:
        topic = str(source.get("list_name") or "").strip() or "Unassigned"
        topic_groups.setdefault(topic, []).append(source)

    topics = []
    for topic, topic_sources in sorted(topic_groups.items()):
        topic_actions = [
            action
            for source in topic_sources
            for action in source["actions"]
        ]
        topic_unresolved = [
            {"publication": source["publication"], **item}
            for source in topic_sources
            for item in source["attention"]
            if item.get("unresolved")
        ]
        publications_by_status: dict[str, list[str]] = {}
        for source in topic_sources:
            publications_by_status.setdefault(
                source["overall_status"], []
            ).append(source["publication"])
        topics.append({
            "topic": topic,
            "summary": summarize_sources(topic_sources),
            "publications_by_status": {
                status: sorted(publications)
                for status, publications in sorted(
                    publications_by_status.items()
                )
            },
            "actions_applied": topic_actions if apply_fixes else [],
            "actions_proposed": [] if apply_fixes else topic_actions,
            "unresolved_problems": topic_unresolved,
        })
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": f"source-health-{int(checked_at.timestamp())}",
        "checked_at": iso_utc(checked_at),
        "mode": "apply" if apply_fixes else "dry_run",
        "scope": {
            "worksheet": worksheet_name,
            "list_names": sorted({
                source["list_name"] for source in sources
                if source["list_name"]
            }),
            "sources_checked": len(sources),
        },
        "summary": summary,
        "topics": topics,
        "actions_applied": actions if apply_fixes else [],
        "actions_proposed": [] if apply_fixes else actions,
        "unresolved_problems": unresolved,
        "sources": sources,
    }


def markdown_report(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Source Health Diagnostic", "",
        f"- Checked: {report['checked_at']}",
        f"- Mode: {report['mode']}",
        f"- Sources checked: {summary['sources_checked']}",
        f"- Healthy: {summary['healthy_sources']}",
        f"- Repaired: {summary['repaired_sources']}",
        f"- Degraded but usable: {summary['degraded_sources']}",
        f"- Pending retry: {summary['retry_pending_sources']}",
        f"- Quarantined this run: {summary['quarantined_sources']}",
        f"- Reactivated this run: {summary['reactivated_sources']}",
        f"- Attention: {summary['attention_sources']}",
        f"- Unresolved: {summary['unresolved_sources']}",
        "", "## Topic summary", "",
        "| Topic | Checked | Healthy | Repaired | Degraded | Retry pending | Quarantined | Unresolved |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for topic in report.get("topics", []):
        topic_summary = topic["summary"]
        lines.append(
            f"| {topic['topic']} | {topic_summary['sources_checked']} | "
            f"{topic_summary['healthy_sources']} | "
            f"{topic_summary['repaired_sources']} | "
            f"{topic_summary['degraded_sources']} | "
            f"{topic_summary['retry_pending_sources']} | "
            f"{topic_summary['quarantined_sources']} | "
            f"{topic_summary['unresolved_sources']} |"
        )
    lines.extend(["", "## Actions", ""])
    actions = report["actions_applied"] or report["actions_proposed"]
    if actions:
        lines.extend(
            f"- **{item['publication']}** - `{item['type']}`: {item['reason']}"
            for item in actions
        )
    else:
        lines.append("No source configuration changes were required.")
    lines.extend(["", "## Problems requiring review", ""])
    if report["unresolved_problems"]:
        lines.extend(
            f"- **{item['publication']}** ({item['source_type']}): "
            f"`{item['reason']}` - {item['message']}"
            for item in report["unresolved_problems"]
        )
    else:
        lines.append("No unresolved problems.")
    lines.extend([
        "", "## Source status", "",
        "| Topic | Publication | Overall | Sitemap | RSS |",
        "|---|---|---|---|---|",
    ])
    for source in report["sources"]:
        rss_state = ", ".join(
            item["state"] for item in source["rss_feeds"]
        ) or "not_configured"
        lines.append(
            f"| {source.get('list_name') or 'Unassigned'} | "
            f"{source['publication']} | {source['overall_status']} | "
            f"{source['sitemap'].get('state', 'not_configured')} | "
            f"{rss_state} |"
        )
    return "\n".join(lines) + "\n"


def open_spreadsheet():
    sheet_id = os.environ["GOOGLE_SHEET_ID"]
    raw_credentials = os.getenv("GOOGLE_CREDENTIALS", "").strip()
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    if raw_credentials:
        credentials = Credentials.from_service_account_info(
            json.loads(raw_credentials), scopes=scopes
        )
    else:
        credentials = Credentials.from_service_account_file(
            os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "credentials.json"),
            scopes=scopes,
        )
    return gspread.authorize(credentials).open_by_key(sheet_id)


def write_history(spreadsheet, report: dict[str, Any]) -> None:
    try:
        worksheet = spreadsheet.worksheet(HISTORY_SHEET)
    except gspread.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(
            title=HISTORY_SHEET, rows=2000, cols=len(HISTORY_HEADERS)
        )
        worksheet.append_row(list(HISTORY_HEADERS))
    else:
        existing_headers = worksheet.row_values(1)
        if existing_headers != list(HISTORY_HEADERS):
            worksheet.update(
                f"A1:{_column_name(len(HISTORY_HEADERS))}1",
                [list(HISTORY_HEADERS)],
            )
    rows = []
    for source in report["sources"]:
        rows.append([
            report["run_id"], report["checked_at"], source["list_name"],
            source["publication"], "sitemap",
            source["sitemap"].get("configured_url") or "",
            source["sitemap"].get("final_url") or "",
            source["sitemap"].get("state", "not_configured"),
            source["sitemap"].get("reason", ""),
            ",".join(
                item["type"] for item in source["actions"]
                if item["type"].startswith("sitemap")
            ),
        ])
        for rss in source["rss_feeds"]:
            rows.append([
                report["run_id"], report["checked_at"], source["list_name"],
                source["publication"], "rss",
                rss.get("configured_url") or "", rss.get("final_url") or "",
                rss["state"], rss["reason"],
                ",".join(
                    item["type"] for item in source["actions"]
                    if item["type"].startswith("rss")
                ),
            ])
    if rows:
        worksheet.append_rows(rows, value_input_option="RAW")


def run_diagnostic(
    apply_fixes: bool,
    output_dir: str,
    github_output: str = "",
) -> dict[str, Any]:
    checked_at = utc_now()
    spreadsheet = open_spreadsheet()
    worksheet = spreadsheet.worksheet(SOURCE_SHEET)
    headers = ensure_headers(
        worksheet, SOURCE_HEALTH_COLUMNS, write=apply_fixes
    )
    session = requests.Session()
    sources = []
    pending_updates = []
    sitemap_cache: dict[tuple[str, str], dict[str, Any]] = {}
    rss_cache: dict[tuple[str, str], dict[str, Any]] = {}

    for row_number, record in source_records(worksheet, headers):
        if not should_check_source(record):
            continue
        source = process_source(
            record, row_number, session, checked_at, sitemap_cache, rss_cache,
            apply_fixes=apply_fixes,
        )
        sources.append(source)
        pending_updates.append((row_number, record))
        print(f"[{source['overall_status'].upper()}] {source['publication']}")

    report = build_report(sources, checked_at, apply_fixes, worksheet.title)
    if apply_fixes:
        save_source_records(worksheet, headers, pending_updates)
        write_history(spreadsheet, report)

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (destination / "report.md").write_text(
        markdown_report(report), encoding="utf-8"
    )

    attention_count = report["summary"]["attention_sources"]
    unresolved_count = report["summary"]["unresolved_sources"]
    if github_output:
        with open(github_output, "a", encoding="utf-8") as stream:
            stream.write(f"attention_count={attention_count}\n")
            stream.write(f"unresolved_count={unresolved_count}\n")
            stream.write(f"mode={report['mode']}\n")
    print(f"Sources checked: {report['summary']['sources_checked']}")
    print(f"Attention: {attention_count}")
    print(f"Unresolved: {unresolved_count}")
    print(f"Report: {destination / 'report.json'}")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate and conservatively repair article sources"
    )
    parser.add_argument(
        "--apply-fixes", action="store_true",
        help="Update safe repairs in Google Sheets",
    )
    parser.add_argument("--output-dir", default="output/source-health")
    parser.add_argument("--github-output", default="")
    args = parser.parse_args()
    run_diagnostic(args.apply_fixes, args.output_dir, args.github_output)


if __name__ == "__main__":
    main()
