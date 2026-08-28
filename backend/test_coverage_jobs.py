from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.coverage_job_store import (
    CANDIDATE_HEADERS,
    CANDIDATES_SHEET,
    JOB_HEADERS,
    JOBS_SHEET,
    TRAFFIC_HEADERS,
    TRAFFIC_SHEET,
    CoverageJobStore,
    CoverageRunInProgress,
    canonical_candidate_key,
)


class FakeWorksheet:
    def __init__(self, headers):
        self.values = [list(headers)]
        self.read_count = 0

    def get_all_values(self):
        self.read_count += 1
        return [list(row) for row in self.values]

    def append_row(self, row, **_kwargs):
        self.values.append(list(row))

    def append_rows(self, rows, **_kwargs):
        self.values.extend(list(row) for row in rows)

    def update(self, cell_range, rows, **_kwargs):
        start = cell_range.split(":", 1)[0]
        row_number = int("".join(character for character in start if character.isdigit()))
        while len(self.values) < row_number:
            self.values.append([])
        self.values[row_number - 1] = list(rows[0])

    def batch_update(self, updates, **_kwargs):
        for update in updates:
            self.update(update["range"], update["values"])


class FakeSpreadsheet:
    def __init__(self):
        self.sheets = {
            JOBS_SHEET: FakeWorksheet(JOB_HEADERS),
            CANDIDATES_SHEET: FakeWorksheet(CANDIDATE_HEADERS),
            TRAFFIC_SHEET: FakeWorksheet(TRAFFIC_HEADERS),
        }

    def worksheet(self, title):
        return self.sheets[title]

    def add_worksheet(self, title, rows, cols):
        worksheet = FakeWorksheet([])
        self.sheets[title] = worksheet
        return worksheet


def make_store() -> CoverageJobStore:
    return CoverageJobStore(FakeSpreadsheet())


def create_job(store: CoverageJobStore):
    store.create_job({
        "job_id": "job-1",
        "report_title": "Report",
        "mention_terms": ["Client Name"],
        "search_queries": ["first query"],
        "date_from": "2026-08-01",
        "date_to": "2026-08-31",
    })


class CoverageJobStoreTests(unittest.TestCase):
    def test_existing_job_sheet_is_extended_with_run_tracking_headers(self):
        spreadsheet = FakeSpreadsheet()
        spreadsheet.sheets[JOBS_SHEET] = FakeWorksheet(JOB_HEADERS[:-2])

        CoverageJobStore(spreadsheet)

        self.assertEqual(spreadsheet.sheets[JOBS_SHEET].values[0], JOB_HEADERS)

    def test_canonical_key_removes_tracking_and_common_url_variants(self):
        left = canonical_candidate_key(
            "http://www.example.com/story/?utm_source=x&id=4"
        )
        right = canonical_candidate_key(
            "https://example.com/story?id=4"
        )
        self.assertEqual(left, right)

    def test_discovery_merges_queries_without_duplicate_candidates(self):
        store = make_store()
        create_job(store)
        first = store.upsert_discoveries(
            "job-1",
            [{"article_url": "https://example.com/story?utm_source=one"}],
            query="first query",
            search_source="news",
        )
        second = store.upsert_discoveries(
            "job-1",
            [{"article_url": "https://www.example.com/story"}],
            query="second query",
            search_source="web",
        )

        self.assertEqual(first, (1, 0))
        self.assertEqual(second, (0, 1))
        candidates = store.list_candidates("job-1")
        self.assertEqual(len(candidates), 1)
        self.assertIn("first query", candidates[0]["search_queries"])
        self.assertIn("second query", candidates[0]["search_queries"])

    def test_second_mutating_action_is_rejected(self):
        store = make_store()
        create_job(store)
        store.acquire_action("job-1", "discover")

        with self.assertRaisesRegex(
            CoverageRunInProgress,
            "A coverage run is already in progress",
        ):
            store.acquire_action("job-1", "verify")

    def test_action_run_tracking_is_created_and_cleared(self):
        store = make_store()
        create_job(store)

        acquired = store.acquire_action("job-1", "discover")
        self.assertEqual(acquired["active_action"], "discover")
        self.assertTrue(acquired["active_run_started_at"])

        store.update_job("job-1", active_run_id="123")
        store.release_action(
            "job-1",
            status="cancelled",
            error="GitHub Actions workflow cancelled",
            action="discover",
        )

        released = store.get_job("job-1")
        self.assertEqual(released["active_action"], "")
        self.assertEqual(released["active_run_id"], "")
        self.assertEqual(released["active_run_started_at"], "")
        self.assertEqual(released["status"], "cancelled")

    def test_bulk_decisions_and_country_override_persist(self):
        store = make_store()
        create_job(store)
        store.upsert_discoveries(
            "job-1",
            [{"article_url": "https://example.com/story"}],
        )
        candidate = store.list_candidates("job-1")[0]
        store.update_candidates("job-1", [{
            "url_key": candidate["url_key"],
            "decision": "pending_review",
            "country_lookup_key": "example.com",
        }])
        store.set_review_decisions("job-1", [{
            "url_key": candidate["url_key"],
            "decision": "approved",
        }])
        store.apply_country_override(
            "job-1",
            "example.com",
            "Canada",
            "CA",
        )

        saved = store.list_candidates("job-1")[0]
        self.assertEqual(saved["decision"], "approved")
        self.assertEqual(saved["manually_approved"], "TRUE")
        self.assertEqual(saved["country"], "Canada")
        self.assertEqual(saved["country_reviewed"], "TRUE")

    def test_traffic_cache_is_loaded_once_and_written_in_batches(self):
        store = make_store()
        worksheet = store.traffic
        worksheet.append_row([
            "cached.example",
            "1000",
            "1K",
            "zenrows",
            "2026-08-01T00:00:00Z",
        ])
        worksheet.read_count = 0

        cache = store.load_traffic_cache()
        store.upsert_traffic_many(
            {
                "cached.example": {
                    "monthly_visits": 2000,
                    "monthly_visits_display": "2K",
                    "source": "zenrows",
                },
                "new.example": {
                    "monthly_visits": 3000,
                    "monthly_visits_display": "3K",
                    "source": "zenrows",
                },
            },
            existing=cache,
        )

        self.assertEqual(worksheet.read_count, 1)
        saved = {
            row[0]: row
            for row in worksheet.values[1:]
        }
        self.assertEqual(saved["cached.example"][1:4], ["2000", "2K", "zenrows"])
        self.assertEqual(saved["new.example"][1:4], ["3000", "3K", "zenrows"])
        self.assertEqual(len(saved), 2)


SERVICE_DEPS_AVAILABLE = all(
    importlib.util.find_spec(module) is not None
    for module in ("fastapi", "gspread", "jwt")
)


@unittest.skipUnless(SERVICE_DEPS_AVAILABLE, "trigger service dependencies unavailable")
class CoverageRunReconciliationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        environment = {
            "GITHUB_OWNER": "owner",
            "GITHUB_REPO": "repo",
            "GITHUB_REF": "App-revamp",
            "GITHUB_TOKEN": "token",
            "APP_LOGIN_PASSWORD": "password",
            "JWT_SECRET": "secret",
        }
        with patch.dict(os.environ, environment):
            from backend import trigger_service
        cls.service = trigger_service

    def test_cancelled_run_releases_persisted_action(self):
        store = make_store()
        create_job(store)
        store.acquire_action("job-1", "discover")
        store.update_job("job-1", active_run_id="123")
        cancelled = {"id": 123, "status": "completed", "conclusion": "cancelled"}

        with patch.object(self.service, "_get_run_by_id", return_value=cancelled):
            job, run = self.service._reconcile_coverage_run(
                store,
                store.get_job("job-1"),
            )

        self.assertEqual(run, cancelled)
        self.assertEqual(job["status"], "cancelled")
        self.assertEqual(job["active_action"], "")

    def test_legacy_job_falls_back_to_title_and_persists_run_id(self):
        store = make_store()
        create_job(store)
        store.acquire_action("job-1", "discover")
        queued = {"id": 456, "status": "queued", "conclusion": None}

        with patch.object(self.service, "_find_coverage_run", return_value=queued):
            job, run = self.service._reconcile_coverage_run(
                store,
                store.get_job("job-1"),
            )

        self.assertEqual(run, queued)
        self.assertEqual(job["active_run_id"], "456")

    def test_stale_missing_run_is_released_before_idle_check(self):
        store = make_store()
        create_job(store)
        store.acquire_action("job-1", "discover")
        store.update_job(
            "job-1",
            active_run_started_at="2020-01-01T00:00:00+00:00",
        )

        with patch.object(self.service, "_find_coverage_run", return_value=None):
            job = self.service._require_coverage_job_idle(store, "job-1")

        self.assertEqual(job["active_action"], "")
        self.assertEqual(job["status"], "failed")


class FinalizationTests(unittest.TestCase):
    @patch("backend.coverage_actions.build_coverage_pdf")
    @patch("backend.coverage_actions.lookup_publication_traffic")
    def test_pdf_regeneration_reuses_domain_traffic_cache(
        self,
        mock_traffic,
        mock_pdf,
    ):
        from backend.coverage_actions import finalize_job

        mock_traffic.return_value = {
            "example.com": {
                "monthly_visits": 1000,
                "monthly_visits_display": "1.0K",
                "source": "hypestat_via_zenrows",
            }
        }
        store = make_store()
        create_job(store)
        store.upsert_discoveries(
            "job-1",
            [{"article_url": "https://example.com/story"}],
        )
        candidate = store.list_candidates("job-1")[0]
        store.update_candidates("job-1", [{
            "url_key": candidate["url_key"],
            "article_title": "Story",
            "publication": "Example",
            "domain": "example.com",
            "decision": "approved",
            "country": "Canada",
            "country_code": "CA",
            "country_source": "manual",
            "country_lookup_key": "example.com",
            "country_reviewed": "TRUE",
        }])

        with tempfile.TemporaryDirectory() as directory:
            with patch("backend.coverage_actions.OUTPUT_DIR", Path(directory)):
                finalize_job(store, "job-1")
                finalize_job(store, "job-1")

        mock_traffic.assert_called_once_with(["example.com"])
        self.assertEqual(mock_pdf.call_count, 2)


class VerificationTests(unittest.TestCase):
    def test_confirmed_out_of_range_article_is_rejected(self):
        from backend.coverage_actions import verify_job

        store = make_store()
        create_job(store)
        store.upsert_discoveries(
            "job-1",
            [{"article_url": "https://example.com/story"}],
        )
        page = {
            "ok": True,
            "url": "https://example.com/story",
            "title": "Client Name story",
            "domain": "example.com",
            "published_date": "2026-07-01",
            "extraction_method": "article_element",
        }
        evidence = {
            "matched_terms": ["Client Name"],
            "has_backlink": False,
            "backlink_urls": [],
            "coverage_type": "mention",
            "evidence_snippet": "Client Name",
            "verification_status": "confirmed",
            "verification_reason": "Exact approved term found in body",
            "is_relevant": True,
        }
        with patch("backend.coverage_actions.fetch_page", return_value=page), patch(
            "backend.coverage_actions.extract_evidence",
            return_value=evidence,
        ):
            verify_job(store, "job-1")

        saved = store.list_candidates("job-1")[0]
        self.assertEqual(saved["decision"], "rejected")
        self.assertEqual(saved["verification_status"], "out_of_range")

    def test_extraction_failure_requires_search_evidence_for_review(self):
        from backend.coverage_actions import verify_job

        store = make_store()
        create_job(store)
        store.upsert_discoveries(
            "job-1",
            [
                {
                    "article_url": "https://example.com/with-evidence",
                    "title": "Client Name appears here",
                },
                {
                    "article_url": "https://example.com/no-evidence",
                    "title": "Unrelated story",
                },
            ],
        )
        with patch(
            "backend.coverage_actions.fetch_page",
            return_value={"ok": False, "error": "http_403"},
        ):
            verify_job(store, "job-1")

        decisions = {
            row["article_url"]: row["decision"]
            for row in store.list_candidates("job-1")
        }
        self.assertEqual(
            decisions["https://example.com/with-evidence"],
            "pending_review",
        )
        self.assertEqual(
            decisions["https://example.com/no-evidence"],
            "rejected",
        )


if __name__ == "__main__":
    unittest.main()
