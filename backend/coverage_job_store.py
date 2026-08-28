"""Persistent Google Sheets storage for staged client coverage jobs."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


JOBS_SHEET = "Coverage Jobs"
CANDIDATES_SHEET = "Coverage Candidates"
TRAFFIC_SHEET = "Coverage Traffic Cache"

JOB_HEADERS = [
    "job_id",
    "status",
    "report_title",
    "mention_terms",
    "search_queries",
    "suggested_queries",
    "date_from",
    "date_to",
    "backlink_domains",
    "active_action",
    "created_at",
    "updated_at",
    "last_error",
    "pdf_path",
    "finalized_at",
]

CANDIDATE_HEADERS = [
    "job_id",
    "url_key",
    "article_url",
    "canonical_url",
    "article_title",
    "search_result_title",
    "snippet",
    "search_date",
    "publication_hint",
    "publication",
    "domain",
    "search_queries",
    "search_sources",
    "first_seen_at",
    "verified_at",
    "decision",
    "verification_status",
    "verification_reason",
    "matched_terms",
    "published_date",
    "extraction_method",
    "manually_approved",
    "country",
    "country_code",
    "country_source",
    "country_confidence",
    "country_lookup_key",
    "country_reviewed",
    "monthly_visits",
    "monthly_visits_display",
    "traffic_source",
    "evidence_snippet",
    "has_backlink",
    "backlink_url",
    "coverage_type",
]

TRAFFIC_HEADERS = [
    "domain",
    "monthly_visits",
    "monthly_visits_display",
    "traffic_source",
    "checked_at",
]

TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ref",
    "referrer",
}


class CoverageRunInProgress(RuntimeError):
    """Raised when a mutating action is already active for a coverage job."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def canonical_candidate_key(url: str) -> str:
    """Normalize common URL variants without making a network request."""
    parsed = urlparse(str(url or "").strip())
    host = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")
    query = urlencode(
        sorted(
            (key, value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if not key.lower().startswith("utm_")
            and key.lower() not in TRACKING_QUERY_KEYS
        )
    )
    return urlunparse(("https", host, path, "", query, ""))


def _as_json_list(value) -> str:
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return "[]"
        try:
            value = json.loads(stripped)
        except json.JSONDecodeError:
            value = [stripped]
    values = value if isinstance(value, list) else list(value or [])
    return json.dumps(list(dict.fromkeys(str(item) for item in values if item)))


def _merge_json_lists(left, right) -> str:
    merged = []
    for raw in (left, right):
        try:
            values = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            values = [raw]
        if not isinstance(values, list):
            values = [values]
        merged.extend(str(value) for value in values if value)
    return json.dumps(list(dict.fromkeys(merged)))


class CoverageJobStore:
    def __init__(self, database):
        self.spreadsheet = getattr(database, "spreadsheet", database)
        self.jobs = self._ensure_sheet(JOBS_SHEET, JOB_HEADERS)
        self.candidates = self._ensure_sheet(CANDIDATES_SHEET, CANDIDATE_HEADERS)
        self.traffic = self._ensure_sheet(TRAFFIC_SHEET, TRAFFIC_HEADERS)

    def _ensure_sheet(self, title: str, headers: list[str]):
        try:
            worksheet = self.spreadsheet.worksheet(title)
        except Exception:
            worksheet = self.spreadsheet.add_worksheet(
                title=title,
                rows=1000,
                cols=max(len(headers), 12),
            )
            worksheet.update("A1", [headers])
            return worksheet

        values = worksheet.get_all_values()
        if not values:
            worksheet.update("A1", [headers])
        elif values[0][: len(headers)] != headers:
            raise RuntimeError(
                f"{title} has an unexpected schema; expected {headers!r}"
            )
        return worksheet

    @staticmethod
    def _records(worksheet, headers: list[str]) -> list[dict]:
        values = worksheet.get_all_values()
        records = []
        for row_number, row in enumerate(values[1:], start=2):
            record = {
                header: row[index] if index < len(row) else ""
                for index, header in enumerate(headers)
            }
            record["_row_number"] = row_number
            records.append(record)
        return records

    @staticmethod
    def _row_values(headers: list[str], record: dict) -> list[str]:
        return [str(record.get(header, "") or "") for header in headers]

    @staticmethod
    def _replace_row(worksheet, headers: list[str], row_number: int, record: dict):
        end_column = CoverageJobStore._column_name(len(headers))
        worksheet.update(
            f"A{row_number}:{end_column}{row_number}",
            [CoverageJobStore._row_values(headers, record)],
        )

    @staticmethod
    def _column_name(number: int) -> str:
        result = ""
        while number:
            number, remainder = divmod(number - 1, 26)
            result = chr(65 + remainder) + result
        return result

    def create_job(self, job: dict) -> dict:
        job_id = str(job.get("job_id", "")).strip()
        if not job_id:
            raise ValueError("job_id is required")
        if self.get_job(job_id):
            raise ValueError(f"Coverage job {job_id} already exists")

        now = utc_now()
        record = {header: job.get(header, "") for header in JOB_HEADERS}
        record.update(
            {
                "job_id": job_id,
                "status": job.get("status", "draft"),
                "mention_terms": _as_json_list(job.get("mention_terms", [])),
                "search_queries": _as_json_list(job.get("search_queries", [])),
                "backlink_domains": _as_json_list(job.get("backlink_domains", [])),
                "created_at": now,
                "updated_at": now,
            }
        )
        self.jobs.append_row(self._row_values(JOB_HEADERS, record))
        return record

    def get_job(self, job_id: str) -> dict | None:
        return next(
            (
                record
                for record in self._records(self.jobs, JOB_HEADERS)
                if record.get("job_id") == job_id
            ),
            None,
        )

    def update_job(self, job_id: str, **changes) -> dict:
        record = self.get_job(job_id)
        if not record:
            raise KeyError(f"Coverage job {job_id} was not found")
        row_number = record.pop("_row_number")
        for key, value in changes.items():
            if key in {
                "mention_terms",
                "search_queries",
                "suggested_queries",
                "backlink_domains",
            }:
                value = _as_json_list(value)
            if key in JOB_HEADERS:
                record[key] = value
        record["updated_at"] = utc_now()
        self._replace_row(self.jobs, JOB_HEADERS, row_number, record)
        return record

    def acquire_action(self, job_id: str, action: str):
        job = self.get_job(job_id)
        if not job:
            raise KeyError(f"Coverage job {job_id} was not found")
        active_action = str(job.get("active_action", "")).strip()
        if active_action:
            raise CoverageRunInProgress(
                "A coverage run is already in progress. "
                "Wait for it to finish before starting another run."
            )
        self.update_job(job_id, active_action=action, status=f"{action}_queued")

    def release_action(
        self,
        job_id: str,
        *,
        status: str,
        error: str = "",
        action: str = "",
    ):
        job = self.get_job(job_id)
        if not job:
            return
        if action and job.get("active_action") not in {"", action}:
            return
        self.update_job(
            job_id,
            active_action="",
            status=status,
            last_error=error,
        )

    def list_candidates(self, job_id: str) -> list[dict]:
        return [
            record
            for record in self._records(self.candidates, CANDIDATE_HEADERS)
            if record.get("job_id") == job_id
        ]

    def upsert_discoveries(
        self,
        job_id: str,
        discoveries: list[dict],
        *,
        query: str = "",
        search_source: str = "",
    ) -> tuple[int, int]:
        existing = {
            record.get("url_key"): record
            for record in self.list_candidates(job_id)
            if record.get("url_key")
        }
        inserted = 0
        updated = 0
        now = utc_now()
        pending_updates = {}
        pending_inserts = []

        for discovery in discoveries:
            article_url = str(
                discovery.get("article_url") or discovery.get("link") or ""
            ).strip()
            if not article_url:
                continue
            url_key = canonical_candidate_key(
                discovery.get("canonical_url") or article_url
            )
            record = existing.get(url_key)
            if record:
                record["search_queries"] = _merge_json_lists(
                    record.get("search_queries"),
                    [query or discovery.get("search_query", "")],
                )
                record["search_sources"] = _merge_json_lists(
                    record.get("search_sources"),
                    [search_source or discovery.get("search_source", "")],
                )
                if not record.get("snippet"):
                    record["snippet"] = discovery.get("snippet", "")
                if not record.get("search_result_title"):
                    record["search_result_title"] = discovery.get(
                        "title", discovery.get("article_title", "")
                    )
                if not record.get("publication_hint"):
                    record["publication_hint"] = discovery.get(
                        "publication_hint", ""
                    )
                row_number = record.get("_row_number")
                if row_number:
                    pending_updates[url_key] = record
                updated += 1
                continue

            parsed = urlparse(article_url)
            record = {header: "" for header in CANDIDATE_HEADERS}
            record.update(
                {
                    "job_id": job_id,
                    "url_key": url_key,
                    "article_url": article_url,
                    "canonical_url": discovery.get("canonical_url", ""),
                    "search_result_title": discovery.get(
                        "title", discovery.get("article_title", "")
                    ),
                    "snippet": discovery.get("snippet", ""),
                    "search_date": discovery.get("date", ""),
                    "publication_hint": discovery.get("publication_hint", ""),
                    "publication": discovery.get("publication", ""),
                    "domain": parsed.netloc.lower().removeprefix("www."),
                    "search_queries": _as_json_list(
                        [query or discovery.get("search_query", "")]
                    ),
                    "search_sources": _as_json_list(
                        [search_source or discovery.get("search_source", "")]
                    ),
                    "first_seen_at": now,
                    "decision": "pending_verification",
                    "verification_status": "pending",
                }
            )
            pending_inserts.append(record)
            existing[url_key] = record
            inserted += 1

        if pending_updates:
            end_column = self._column_name(len(CANDIDATE_HEADERS))
            self.candidates.batch_update(
                [
                    {
                        "range": f"A{record['_row_number']}:{end_column}{record['_row_number']}",
                        "values": [self._row_values(CANDIDATE_HEADERS, record)],
                    }
                    for record in pending_updates.values()
                ],
                value_input_option="RAW",
            )
        if pending_inserts:
            self.candidates.append_rows(
                [
                    self._row_values(CANDIDATE_HEADERS, record)
                    for record in pending_inserts
                ],
                value_input_option="RAW",
            )
        return inserted, updated

    def update_candidates(self, job_id: str, candidates: list[dict]):
        existing = {
            record.get("url_key"): record
            for record in self.list_candidates(job_id)
            if record.get("url_key")
        }
        pending_updates = {}
        for changes in candidates:
            url_key = changes.get("url_key") or canonical_candidate_key(
                changes.get("canonical_url") or changes.get("article_url", "")
            )
            record = existing.get(url_key)
            if not record:
                continue
            for key, value in changes.items():
                if key in CANDIDATE_HEADERS:
                    record[key] = value
            pending_updates[url_key] = record
        if pending_updates:
            end_column = self._column_name(len(CANDIDATE_HEADERS))
            self.candidates.batch_update(
                [
                    {
                        "range": f"A{record['_row_number']}:{end_column}{record['_row_number']}",
                        "values": [self._row_values(CANDIDATE_HEADERS, record)],
                    }
                    for record in pending_updates.values()
                ],
                value_input_option="RAW",
            )

    def set_review_decisions(self, job_id: str, decisions: list[dict]):
        updates = []
        for decision in decisions:
            value = str(decision.get("decision", "")).lower()
            if value not in {"approved", "rejected"}:
                raise ValueError("decision must be approved or rejected")
            updates.append(
                {
                    "url_key": decision.get("url_key", ""),
                    "decision": value,
                    "manually_approved": "TRUE" if value == "approved" else "FALSE",
                }
            )
        self.update_candidates(job_id, updates)

    def update_publication(self, job_id: str, url_key: str, publication: str):
        self.update_candidates(
            job_id,
            [{"url_key": url_key, "publication": publication.strip()}],
        )

    def apply_country_override(
        self,
        job_id: str,
        lookup_key: str,
        country: str,
        country_code: str,
        *,
        not_applicable: bool = False,
    ):
        updates = []
        for candidate in self.list_candidates(job_id):
            if candidate.get("country_lookup_key") != lookup_key:
                continue
            updates.append(
                {
                    "url_key": candidate.get("url_key"),
                    "country": "" if not_applicable else country,
                    "country_code": "" if not_applicable else country_code,
                    "country_source": (
                        "not_applicable" if not_applicable else "manual"
                    ),
                    "country_confidence": "high",
                    "country_reviewed": "TRUE",
                }
            )
        self.update_candidates(job_id, updates)

    def load_traffic_cache(self) -> dict[str, dict]:
        cache = {}
        for record in self._records(self.traffic, TRAFFIC_HEADERS):
            domain = str(record.get("domain", "")).lower().removeprefix("www.")
            if domain:
                cache[domain] = record
        return cache

    def upsert_traffic_many(
        self,
        traffic_by_domain: dict[str, dict],
        *,
        existing: dict[str, dict] | None = None,
    ) -> None:
        if existing is None:
            existing = self.load_traffic_cache()

        now = utc_now()
        pending_updates = []
        pending_inserts = []

        for domain, values in traffic_by_domain.items():
            normalized = str(domain or "").lower().removeprefix("www.")
            if not normalized:
                continue

            record = {
                header: values.get(header, "")
                for header in TRAFFIC_HEADERS
            }
            record.update({
                "domain": normalized,
                "traffic_source": (
                    values.get("traffic_source") or values.get("source", "")
                ),
                "checked_at": now,
            })

            current = existing.get(normalized)
            if current:
                pending_updates.append({
                    **record,
                    "_row_number": current["_row_number"],
                })
            else:
                pending_inserts.append(record)

        if pending_updates:
            end_column = self._column_name(len(TRAFFIC_HEADERS))
            self.traffic.batch_update(
                [
                    {
                        "range": (
                            f"A{record['_row_number']}:"
                            f"{end_column}{record['_row_number']}"
                        ),
                        "values": [self._row_values(TRAFFIC_HEADERS, record)],
                    }
                    for record in pending_updates
                ],
                value_input_option="RAW",
            )

        if pending_inserts:
            self.traffic.append_rows(
                [
                    self._row_values(TRAFFIC_HEADERS, record)
                    for record in pending_inserts
                ],
                value_input_option="RAW",
            )
