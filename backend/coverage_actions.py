"""Staged operations for persistent client coverage jobs."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import requests

try:
    from article_quality import keyword_matches, parse_date
    from client_coverage_pdf import build_coverage_pdf
    from client_coverage_search import (
        OUTPUT_DIR,
        SERPAPI_API_KEY,
        SERPAPI_GL,
        SERPAPI_GOOGLE_DOMAIN,
        SERPAPI_HL,
        SERPAPI_COUNTRY_RESERVE,
        extract_evidence,
        fetch_page,
        get_serpapi_capacity,
        publication_name_from_domain,
        serpapi_search_all,
    )
    from coverage_job_store import CoverageJobStore, canonical_candidate_key, utc_now
    from publication_country import enrich_publication_countries
    from publication_traffic import lookup_publication_traffic
except ImportError:
    from backend.article_quality import keyword_matches, parse_date
    from backend.client_coverage_pdf import build_coverage_pdf
    from backend.client_coverage_search import (
        OUTPUT_DIR,
        SERPAPI_API_KEY,
        SERPAPI_GL,
        SERPAPI_GOOGLE_DOMAIN,
        SERPAPI_HL,
        SERPAPI_COUNTRY_RESERVE,
        extract_evidence,
        fetch_page,
        get_serpapi_capacity,
        publication_name_from_domain,
        serpapi_search_all,
    )
    from backend.coverage_job_store import (
        CoverageJobStore,
        canonical_candidate_key,
        utc_now,
    )
    from backend.publication_country import enrich_publication_countries
    from backend.publication_traffic import lookup_publication_traffic


UNTRUSTED_COUNTRY_SOURCES = {
    "serpapi_organic_results",
    "unresolved",
    "social_unresolved",
}


def country_requires_review(row: dict) -> bool:
    country = str(row.get("country", "")).strip()
    confidence = str(row.get("country_confidence", "")).strip().lower()
    source = str(row.get("country_source", "")).strip().lower()

    return (
        not country
        or confidence in {"", "low", "very_low"}
        or source in UNTRUSTED_COUNTRY_SOURCES
    )


def _json_list(value) -> list[str]:
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value or "[]")
    except (json.JSONDecodeError, TypeError):
        parsed = [value] if value else []
    return [str(item) for item in parsed if item]


def _date_state(value, date_from: str, date_to: str) -> tuple[str, str]:
    if not date_from and not date_to:
        parsed = parse_date(value)
        return ("valid", parsed.strftime("%Y-%m-%d")) if parsed else ("unknown", "")
    parsed = parse_date(value)
    if not parsed:
        return "unknown", ""
    day = parsed.date()
    if date_from and day < datetime.strptime(date_from, "%Y-%m-%d").date():
        return "outside", day.isoformat()
    if date_to and day > datetime.strptime(date_to, "%Y-%m-%d").date():
        return "outside", day.isoformat()
    return "valid", day.isoformat()


def _dates_conflict(primary, secondary) -> bool:
    first = parse_date(primary)
    second = parse_date(secondary)
    return bool(first and second and abs((first.date() - second.date()).days) > 1)


def job_payload(store: CoverageJobStore, job_id: str) -> dict:
    job = store.get_job(job_id)
    if not job:
        raise KeyError(f"Coverage job {job_id} was not found")
    job.pop("_row_number", None)
    candidates = store.list_candidates(job_id)
    for candidate in candidates:
        candidate.pop("_row_number", None)
    approved = [row for row in candidates if row.get("decision") == "approved"]
    review = [row for row in candidates if row.get("decision") == "pending_review"]
    country_review = [
        row
        for row in approved
        if str(row.get("country_reviewed", "")).upper() != "TRUE"
    ]
    return {
        "job": job,
        "results": approved,
        "review_results": review,
        "country_review_results": country_review,
        "summary": {
            "candidates": len(candidates),
            "total_coverage": len(approved),
            "needs_review": len(review),
            "countries_need_review": len(country_review),
            "rejected": sum(row.get("decision") == "rejected" for row in candidates),
        },
    }


def discover_job(
    store: CoverageJobStore,
    job_id: str,
    queries: list[str] | None = None,
    progress_callback=None,
) -> dict:
    job = store.get_job(job_id)
    if not job:
        raise KeyError(f"Coverage job {job_id} was not found")
    requested = queries or _json_list(job.get("search_queries"))
    requested = list(dict.fromkeys(query.strip() for query in requested if query.strip()))
    if not requested:
        raise ValueError("At least one search query is required")

    merged_queries = list(dict.fromkeys(_json_list(job.get("search_queries")) + requested))
    store.update_job(job_id, search_queries=merged_queries, status="discovering")
    search_run = serpapi_search_all(
        queries=requested,
        date_from=job.get("date_from", ""),
        date_to=job.get("date_to", ""),
        progress_callback=progress_callback,
        reserved_searches=SERPAPI_COUNTRY_RESERVE,
        known_urls={
            candidate.get("article_url", "")
            for candidate in store.list_candidates(job_id)
            if candidate.get("article_url")
        },
    )
    inserted = 0
    updated = 0
    discoveries = [
        {**result, "article_url": result.get("url", "")}
        for result in search_run.get("results", [])
    ]
    inserted, updated = store.upsert_discoveries(job_id, discoveries)
    previous_used = int(job.get("searches_used") or 0)
    searches_used = int(search_run.get("searches_used") or 0)
    searches_remaining = int(search_run.get("searches_remaining") or 0)
    store.update_job(
        job_id,
        status="discovered",
        searches_used=previous_used + searches_used,
        searches_remaining=searches_remaining,
    )
    return {
        **job_payload(store, job_id),
        "searches_used": search_run.get("searches_used", 0),
        "searches_remaining": search_run.get("searches_remaining", 0),
        "search_stop_reason": search_run.get("stop_reason", ""),
        "search_diagnostics": search_run.get("search_diagnostics", []),
        "new_candidates": inserted,
        "merged_candidates": updated,
    }


def verify_job(
    store: CoverageJobStore,
    job_id: str,
    progress_callback=None,
) -> dict:
    job = store.get_job(job_id)
    mention_terms = _json_list(job.get("mention_terms"))
    backlink_domains = _json_list(job.get("backlink_domains"))
    pending = [
        row
        for row in store.list_candidates(job_id)
        if row.get("decision") == "pending_verification"
        or row.get("verification_status") == "pending"
    ]
    store.update_job(job_id, status="verifying")
    updates = []
    wire_suggestions = []
    canonical_seen = {
        canonical_candidate_key(
            row.get("canonical_url") or row.get("article_url", "")
        ): row.get("url_key")
        for row in store.list_candidates(job_id)
        if row.get("decision") not in {"pending_verification", "rejected"}
    }

    for index, candidate in enumerate(pending, start=1):
        if progress_callback:
            progress_callback("verifying", index, len(pending), f"Checking article {index} of {len(pending)}")
        page = fetch_page(candidate.get("article_url", ""))
        fallback_text = " ".join(
            [
                candidate.get("search_result_title", ""),
                candidate.get("snippet", ""),
                candidate.get("article_url", ""),
            ]
        )
        fallback_matches = keyword_matches(fallback_text, mention_terms)

        if not page.get("ok"):
            if not fallback_matches:
                updates.append({
                    "url_key": candidate.get("url_key"),
                    "decision": "rejected",
                    "verification_status": "not_found",
                    "verification_reason": "Extraction failed and search evidence contained no client term",
                    "extraction_method": "unavailable",
                    "verified_at": utc_now(),
                })
                continue
            evidence = {
                "matched_terms": fallback_matches,
                "has_backlink": False,
                "backlink_urls": [],
                "coverage_type": "needs_review",
                "evidence_snippet": candidate.get("snippet", ""),
                "verification_status": "needs_review",
                "verification_reason": f"Article extraction failed: {page.get('error', 'unavailable')}; client term appears in search evidence",
                "is_relevant": False,
            }
        else:
            evidence = extract_evidence(page, mention_terms, backlink_domains)

        page_date = page.get("published_date")
        search_date = candidate.get("search_date", "")
        published = page_date or search_date
        date_conflict = _dates_conflict(page_date, search_date)
        date_status, published_date = _date_state(
            published,
            job.get("date_from", ""),
            job.get("date_to", ""),
        )
        if date_status == "outside":
            decision = "rejected"
            status = "out_of_range"
            reason = f"Confirmed publication date {published_date} is outside the reporting period"
        elif evidence.get("is_relevant") and date_conflict:
            decision = "pending_review"
            status = "needs_review"
            reason = "Client term confirmed, but page and search publication dates conflict"
        elif evidence.get("is_relevant") and date_status != "unknown":
            decision = "approved"
            status = "confirmed"
            reason = evidence.get("verification_reason", "")
        elif evidence.get("is_relevant") and (job.get("date_from") or job.get("date_to")):
            decision = "pending_review"
            status = "needs_review"
            reason = "Client term confirmed, but the publication date could not be verified"
        elif evidence.get("verification_status") == "needs_review":
            decision = "pending_review"
            status = "needs_review"
            reason = evidence.get("verification_reason", "")
        else:
            decision = "rejected"
            status = evidence.get("verification_status", "not_found")
            reason = evidence.get("verification_reason", "No client term found")

        canonical_key = canonical_candidate_key(
            page.get("url") or candidate.get("article_url", "")
        )
        existing_key = canonical_seen.get(canonical_key)
        if decision != "rejected" and existing_key and existing_key != candidate.get("url_key"):
            decision = "rejected"
            status = "duplicate"
            reason = "Canonical article already exists in this coverage job"
        elif decision != "rejected":
            canonical_seen[canonical_key] = candidate.get("url_key")

        domain = page.get("domain") or candidate.get("domain", "")
        title = page.get("title") or candidate.get("search_result_title", "")
        publication = (
            candidate.get("publication")
            or page.get("publication_name")
            or publication_name_from_domain(domain)
        )
        updates.append({
            "url_key": candidate.get("url_key"),
            "article_url": page.get("url") or candidate.get("article_url", ""),
            "canonical_url": page.get("url", ""),
            "article_title": title,
            "publication": publication,
            "domain": domain,
            "decision": decision,
            "verification_status": status,
            "verification_reason": reason,
            "matched_terms": ", ".join(evidence.get("matched_terms", [])),
            "published_date": published_date,
            "extraction_method": page.get("extraction_method", "unavailable"),
            "evidence_snippet": evidence.get("evidence_snippet", ""),
            "has_backlink": "TRUE" if evidence.get("has_backlink") else "FALSE",
            "backlink_url": ", ".join(evidence.get("backlink_urls", [])),
            "coverage_type": evidence.get("coverage_type", "mention"),
            "verified_at": utc_now(),
        })
        lower_publication = publication.lower()
        if status == "confirmed" and (
            any(wire in lower_publication for wire in ("associated press", "ap news", "apnews", "reuters", "afp"))
            or domain in {"apnews.com", "reuters.com", "afp.com"}
        ):
            suggestion = f'"{title}"' if title else ""
            if suggestion and suggestion not in wire_suggestions:
                wire_suggestions.append(suggestion)

    store.update_candidates(job_id, updates)
    payload = job_payload(store, job_id)
    next_status = "article_review" if payload["summary"]["needs_review"] else "verified"
    suggestions = list(dict.fromkeys(
        _json_list(job.get("suggested_queries")) + wire_suggestions
    ))
    store.update_job(
        job_id,
        status=next_status,
        suggested_queries=suggestions,
    )
    return {**job_payload(store, job_id), "suggested_queries": wire_suggestions}


def enrich_countries_job(store: CoverageJobStore, job_id: str, database) -> dict:
    payload = job_payload(store, job_id)
    if payload["summary"]["needs_review"]:
        raise ValueError("Every article-review item must be approved or rejected first")
    rows = [
        {
            **dict(row),
            "_publication_hint": row.get("publication_hint", ""),
        }
        for row in payload["results"]
    ]
    store.update_job(job_id, status="country_enrichment")
    stats = enrich_publication_countries(
        rows,
        db=database,
        serpapi_api_key=SERPAPI_API_KEY,
        google_domain=SERPAPI_GOOGLE_DOMAIN,
        gl=SERPAPI_GL,
        hl=SERPAPI_HL,
        google_budget=SERPAPI_COUNTRY_RESERVE,
    )
    updates = []
    for row in rows:
        source = row.get("country_source", "")
        updates.append({
            "url_key": row.get("url_key") or canonical_candidate_key(row.get("article_url", "")),
            "country": row.get("country", ""),
            "country_code": row.get("country_code", ""),
            "country_source": source,
            "country_confidence": row.get("country_confidence", ""),
            "country_lookup_key": row.get("country_lookup_key", ""),
            "country_reviewed": (
                "FALSE" if country_requires_review(row) else "TRUE"
            ),
        })
    store.update_candidates(job_id, updates)

    job = payload["job"]
    previous_used = int(job.get("searches_used") or 0)
    country_searches_used = int(stats.get("google_searches_used") or 0)
    try:
        searches_remaining = int(
            get_serpapi_capacity().get("monthly_left") or 0
        )
    except (
        requests.RequestException,
        RuntimeError,
        KeyError,
        TypeError,
        ValueError,
    ):
        searches_remaining = int(job.get("searches_remaining") or 0)

    store.update_job(
        job_id,
        status="country_review",
        searches_used=previous_used + country_searches_used,
        searches_remaining=searches_remaining,
    )
    return {**job_payload(store, job_id), "country_stats": stats}


def finalize_job(store: CoverageJobStore, job_id: str) -> dict:
    payload = job_payload(store, job_id)
    if payload["summary"]["needs_review"]:
        raise ValueError("Article review is incomplete")
    if payload["summary"]["countries_need_review"]:
        raise ValueError("Country review is incomplete")
    rows = [dict(row) for row in payload["results"]]
    if not rows:
        raise ValueError("No approved coverage is available to finalize")

    store.update_job(job_id, status="finalizing")
    domains = sorted({row.get("domain", "") for row in rows if row.get("domain")})
    cached_traffic = store.load_traffic_cache()
    traffic = {
        domain: cached_traffic[domain]
        for domain in domains
        if domain in cached_traffic
    }
    missing = [
        domain
        for domain in domains
        if domain not in cached_traffic
    ]
    if missing:
        fresh = lookup_publication_traffic(missing)
        store.upsert_traffic_many(fresh, existing=cached_traffic)
        traffic.update(fresh)

    updates = []
    for row in rows:
        values = traffic.get(row.get("domain", ""), {})
        row["monthly_visits"] = values.get("monthly_visits") or ""
        row["monthly_visits_display"] = values.get("monthly_visits_display") or "N/A"
        row["traffic_source"] = values.get("source") or values.get("traffic_source") or "unavailable"
        row["link_note"] = "with link back" if row.get("has_backlink") == "TRUE" else ""
        updates.append({
            "url_key": row.get("url_key"),
            "monthly_visits": row["monthly_visits"],
            "monthly_visits_display": row["monthly_visits_display"],
            "traffic_source": row["traffic_source"],
        })
    store.update_candidates(job_id, updates)

    countries = sorted({row.get("country", "") for row in rows if row.get("country")})
    publications = sorted({row.get("publication", "") for row in rows if row.get("publication")})
    highlights = {
        "total_coverage": len(rows),
        "publication_count": len(publications),
        "country_count": len(countries),
        "countries": ", ".join(countries),
        "highlight_publications": ", ".join(publications[:8]),
        "date_from": payload["job"].get("date_from", ""),
        "date_to": payload["job"].get("date_to", ""),
        "traffic_unavailable": any(row["monthly_visits_display"] == "N/A" for row in rows),
    }
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    pdf_path = str(Path(OUTPUT_DIR) / f"coverage-search-{job_id}.pdf")
    build_coverage_pdf(pdf_path, payload["job"].get("report_title", ""), highlights, rows)
    store.update_job(
        job_id,
        status="complete",
        pdf_path=pdf_path,
        finalized_at=utc_now(),
    )
    return {**job_payload(store, job_id), "highlights": highlights, "pdf_path": pdf_path}
