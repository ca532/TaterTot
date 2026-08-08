from __future__ import annotations

import hashlib
import os
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.parse import urljoin

import feedparser
from bs4 import BeautifulSoup

try:
    from article_quality import (
        canonicalize_url,
        clean_article_text,
        extract_page_metadata,
        resolve_published_date,
        validate_author,
        validate_candidate_url,
    )
    from google_storage import GoogleSheetsDB
    from publication_metadata_pipeline import discover_sitemap_candidates, fetch_text
except ImportError:
    from backend.article_quality import (
        canonicalize_url,
        clean_article_text,
        extract_page_metadata,
        resolve_published_date,
        validate_author,
        validate_candidate_url,
    )
    from backend.google_storage import GoogleSheetsDB
    from backend.publication_metadata_pipeline import discover_sitemap_candidates, fetch_text


REPORT_SHEET = os.getenv("CLIENT_COVERAGE_REPORT_SHEET", "Client Coverage Reports")
REPORT_HEADERS = [
    "coverage_run_id",
    "created_at",
    "client_name",
    "publication",
    "publication_url",
    "author",
    "source_type",
    "matched_title",
    "matched_url",
    "matched_author",
    "matched_date",
    "confidence_score",
    "status",
    "evidence",
]


def ensure_report_sheet(db: GoogleSheetsDB):
    try:
        ws = db.spreadsheet.worksheet(REPORT_SHEET)
    except Exception:
        ws = db.spreadsheet.add_worksheet(
            title=REPORT_SHEET,
            rows=2000,
            cols=len(REPORT_HEADERS),
        )

    values = ws.get_all_values()
    if not values:
        ws.append_row(REPORT_HEADERS)
    elif values[0] != REPORT_HEADERS:
        ws.update("A1:N1", [REPORT_HEADERS])

    return ws


def discover_rss_candidates(base_url: str) -> list[str]:
    base = base_url.rstrip("/") + "/"
    return [
        urljoin(base, "feed"),
        urljoin(base, "feed/"),
        urljoin(base, "rss"),
        urljoin(base, "rss.xml"),
        urljoin(base, "feed.xml"),
        urljoin(base, "atom.xml"),
    ]


def parse_rss_items(rss_url: str, max_items: int = 100) -> list[dict]:
    status, final_url, body = fetch_text(rss_url)
    if status != 200 or not body:
        return []

    parsed = feedparser.parse(body)
    items = []

    for entry in parsed.entries[:max_items]:
        summary = BeautifulSoup(
            getattr(entry, "summary", "") or "",
            "html.parser",
        ).get_text(" ", strip=True)

        items.append({
            "title": clean_article_text(getattr(entry, "title", "") or ""),
            "url": getattr(entry, "link", "") or "",
            "author": validate_author(getattr(entry, "author", "") or ""),
            "published_date": getattr(entry, "published", "") or getattr(entry, "updated", "") or "",
            "summary": clean_article_text(summary),
            "source_type": "rss",
            "source_url": final_url or rss_url,
        })

    return items


def parse_sitemap_items(sitemap_url: str, max_urls: int = 200) -> list[dict]:
    status, final_url, body = fetch_text(sitemap_url)
    if status != 200 or not body:
        return []

    try:
        root = ET.fromstring(body.encode("utf-8", errors="ignore"))
    except Exception:
        return []

    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    tag = root.tag.lower()

    if "sitemapindex" in tag:
        items = []
        for loc in root.findall(".//sm:sitemap/sm:loc", ns)[:10]:
            items.extend(parse_sitemap_items(loc.text or "", max_urls=max_urls))
            if len(items) >= max_urls:
                break
        return items[:max_urls]

    items = []
    for url_el in root.findall(".//sm:url", ns):
        loc = url_el.find("sm:loc", ns)
        lastmod = url_el.find("sm:lastmod", ns)
        if loc is None or not loc.text:
            continue

        url = loc.text.strip()
        ok, _ = validate_candidate_url(url)
        if not ok:
            continue

        items.append({
            "title": "",
            "url": url,
            "author": "Unknown",
            "published_date": (lastmod.text or "").strip() if lastmod is not None else "",
            "summary": "",
            "source_type": "sitemap",
            "source_url": final_url or sitemap_url,
        })

        if len(items) >= max_urls:
            break

    return items


def fetch_article_context(url: str) -> dict:
    status, final_url, body = fetch_text(url)
    if status != 200 or not body:
        return {
            "text": "",
            "title": "",
            "author": "Unknown",
            "published_date": None,
            "canonical_url": canonicalize_url(url),
        }

    metadata = extract_page_metadata(body)
    soup = BeautifulSoup(body, "html.parser")

    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    title = clean_article_text(metadata.get("title") or "")
    if not title:
        html_title = soup.find("title")
        title = clean_article_text(html_title.get_text(" ", strip=True) if html_title else "")

    author = "Unknown"
    for attrs in (
        {"name": "author"},
        {"property": "article:author"},
        {"name": "byl"},
    ):
        tag = soup.find("meta", attrs=attrs)
        if tag and tag.get("content"):
            author = validate_author(tag.get("content"))
            break

    published_date, _ = resolve_published_date(metadata)
    text = clean_article_text(soup.get_text(" ", strip=True))

    return {
        "text": text[:7000],
        "title": title,
        "author": author,
        "published_date": published_date,
        "canonical_url": canonicalize_url(metadata.get("canonical_url") or final_url or url),
    }


def score_candidate(
    candidate: dict,
    client_name: str,
    author: str = "",
    keywords: list[str] | None = None,
) -> tuple[int, list[str], dict]:
    keywords = keywords or []
    evidence = []
    score = 0

    client = client_name.lower().strip()
    wanted_author = author.lower().strip()

    title = clean_article_text(candidate.get("title", ""))
    summary = clean_article_text(candidate.get("summary", ""))
    candidate_author = validate_author(candidate.get("author", ""))
    url = candidate.get("url", "")

    text = f"{title} {summary}".lower()

    if client and client in text:
        score += 45
        evidence.append("client name appears in feed metadata")

    if wanted_author and wanted_author in candidate_author.lower():
        score += 20
        evidence.append("author matches feed metadata")

    matched_keywords = [
        k.strip()
        for k in keywords
        if k.strip() and k.strip().lower() in text
    ]
    if matched_keywords:
        score += min(15, len(matched_keywords) * 5)
        evidence.append("keyword overlap: " + ", ".join(matched_keywords[:4]))

    if client and client.replace(" ", "-") in url.lower():
        score += 10
        evidence.append("client name appears in URL")

    page = {}
    if score < 75 and url:
        page = fetch_article_context(url)
        page_text = f"{page.get('title', '')} {page.get('text', '')}".lower()

        if client and client in page_text:
            score += 45
            evidence.append("client name appears on article page")

        if wanted_author and wanted_author in page.get("author", "").lower():
            score += 15
            evidence.append("author appears in page metadata")
        elif wanted_author and wanted_author in page_text:
            score += 10
            evidence.append("author appears on article page")

    return min(score, 100), evidence, page


def status_for_score(score: int) -> str:
    if score >= 75:
        return "found"
    if score >= 50:
        return "possible_match"
    return "not_found"


def scan_publication(
    client_name: str,
    publication: str,
    publication_url: str,
    author: str = "",
    keywords: list[str] | None = None,
) -> list[dict]:
    candidates = []

    for rss_url in discover_rss_candidates(publication_url):
        items = parse_rss_items(rss_url)
        if items:
            candidates.extend(items)
            break

    for sitemap_url in discover_sitemap_candidates(publication_url)[:5]:
        items = parse_sitemap_items(sitemap_url)
        if items:
            candidates.extend(items)
            break

    seen = set()
    matches = []

    for candidate in candidates:
        url = candidate.get("url", "").strip()
        if not url:
            continue

        normalized = canonicalize_url(url)
        if normalized in seen:
            continue
        seen.add(normalized)

        score, evidence, page = score_candidate(candidate, client_name, author, keywords)
        if score < 50:
            continue

        matched_title = candidate.get("title") or page.get("title") or url
        matched_author = validate_author(candidate.get("author") or page.get("author") or "")
        matched_date = candidate.get("published_date") or ""
        if not matched_date and page.get("published_date"):
            matched_date = page["published_date"].strftime("%Y-%m-%d")

        matches.append({
            "publication": publication,
            "publication_url": publication_url,
            "author": author,
            "source_type": candidate.get("source_type", ""),
            "matched_title": matched_title,
            "matched_url": page.get("canonical_url") or normalized or url,
            "matched_author": matched_author,
            "matched_date": str(matched_date),
            "confidence_score": score,
            "status": status_for_score(score),
            "evidence": "; ".join(evidence),
        })

    matches.sort(key=lambda item: item["confidence_score"], reverse=True)
    return matches[:10]


def run_coverage_scan(
    client_name: str,
    publications: list[dict],
    author: str = "",
    keywords: list[str] | None = None,
    coverage_run_id: str | None = None,
    progress_callback=None,
):
    def emit(phase: str, current: int, total: int, message: str):
        if progress_callback:
            try:
                progress_callback(phase, current, total, message)
            except Exception:
                pass

    db = GoogleSheetsDB()
    ws = ensure_report_sheet(db)

    run_id = coverage_run_id or f"coverage-{int(time.time())}-{hashlib.sha1(client_name.encode()).hexdigest()[:8]}"
    created_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    rows = []
    results = []
    total = len(publications)

    emit("initializing", 0, total, "Preparing coverage scan")

    for index, pub in enumerate(publications, start=1):
        publication = str(pub.get("publication", "")).strip()
        publication_url = str(pub.get("publication_url", "")).strip()

        if not publication or not publication_url:
            continue

        emit("scanning", index, total, f"Scanning {publication}")

        try:
            matches = scan_publication(
                client_name=client_name,
                publication=publication,
                publication_url=publication_url,
                author=author,
                keywords=keywords or [],
            )
        except Exception as exc:
            matches = [{
                "publication": publication,
                "publication_url": publication_url,
                "author": author,
                "source_type": "",
                "matched_title": "",
                "matched_url": "",
                "matched_author": "",
                "matched_date": "",
                "confidence_score": 0,
                "status": "error",
                "evidence": str(exc),
            }]

        if not matches:
            matches = [{
                "publication": publication,
                "publication_url": publication_url,
                "author": author,
                "source_type": "",
                "matched_title": "",
                "matched_url": "",
                "matched_author": "",
                "matched_date": "",
                "confidence_score": 0,
                "status": "not_found",
                "evidence": "No matching RSS or sitemap coverage found",
            }]

        for match in matches:
            row = [
                run_id,
                created_at,
                client_name,
                match["publication"],
                match["publication_url"],
                author,
                match["source_type"],
                match["matched_title"],
                match["matched_url"],
                match["matched_author"],
                match["matched_date"],
                match["confidence_score"],
                match["status"],
                match["evidence"],
            ]
            rows.append(row)
            results.append(dict(zip(REPORT_HEADERS, row)))

    emit("saving", total, total, "Saving coverage report")

    if rows:
        ws.append_rows(rows, value_input_option="USER_ENTERED")

    summary = {
        "total_publications": len(publications),
        "rows_written": len(rows),
        "found": sum(1 for r in results if r["status"] == "found"),
        "possible_match": sum(1 for r in results if r["status"] == "possible_match"),
        "not_found": sum(1 for r in results if r["status"] == "not_found"),
        "error": sum(1 for r in results if r["status"] == "error"),
    }

    emit("complete", total, total, "Coverage scan complete")

    return {
        "coverage_run_id": run_id,
        "summary": summary,
        "results": results,
    }
