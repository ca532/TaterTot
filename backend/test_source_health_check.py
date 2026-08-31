import json
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from source_health_check import (
    apply_source_availability_policy,
    apply_rss_result,
    build_report,
    choose_sitemap_replacement,
    ensure_headers,
    markdown_report,
    process_source,
    save_source_records,
    same_domain,
    should_check_source,
    validate_rss,
    validate_sitemap,
)


CHECKED_AT = datetime(2026, 8, 31, 15, 0, tzinfo=timezone.utc)


class FakeResponse:
    def __init__(self, url, body="", status=200, history=None):
        self.url = url
        self.text = body
        self.content = body.encode("utf-8")
        self.encoding = "utf-8"
        self.status_code = status
        self.history = history or []


class FakeSession:
    def __init__(self, responses):
        self.responses = responses

    def get(self, url, **_kwargs):
        value = self.responses[url]
        if isinstance(value, Exception):
            raise value
        return value


def sitemap_result(url, passed=True):
    return {
        "configured_url": url,
        "final_url": url,
        "http_status": 200 if passed else 404,
        "redirects": [],
        "valid_xml": passed,
        "sitemap_type": "urlset" if passed else None,
        "reason": "ok" if passed else "http_404",
        "state": "healthy" if passed else "permanent_error",
        "url_count": 10 if passed else 0,
        "recent_url_count": 4 if passed else 0,
        "latest_last_modified": "2026-08-30T00:00:00Z" if passed else None,
        "sample_extraction": {
            "attempted": passed,
            "url": "https://example.com/story" if passed else None,
            "ok": passed,
            "character_count": 1000 if passed else 0,
            "reason": "ok" if passed else "no_article_urls",
        },
        "passed": passed,
    }


class SourceHealthTests(unittest.TestCase):
    def test_replacement_selects_one_or_a_clear_high_confidence_winner(self):
        one, reason = choose_sitemap_replacement([
            sitemap_result("https://example.com/news.xml"),
            sitemap_result("https://example.com/missing.xml", passed=False),
        ])
        self.assertEqual("https://example.com/news.xml", one)
        self.assertEqual("one_valid_replacement", reason)

        multiple, reason = choose_sitemap_replacement([
            sitemap_result("https://example.com/news.xml"),
            sitemap_result("https://example.com/news-two.xml"),
        ])
        self.assertIsNone(multiple)
        self.assertEqual("multiple_ambiguous_replacements", reason)

        declared = sitemap_result("https://example.com/current.xml")
        declared["declared_in_robots"] = True
        selected, reason = choose_sitemap_replacement([
            declared,
            sitemap_result("https://example.com/other.xml"),
        ])
        self.assertEqual("https://example.com/current.xml", selected)
        self.assertEqual("highest_confidence_replacement", reason)

    def test_same_domain_allows_subdomains_but_not_other_domains(self):
        self.assertTrue(same_domain("https://news.example.com/a", "https://example.com"))
        self.assertFalse(same_domain("https://example.net/a", "https://example.com"))

    @patch("source_health_check.extract_sample_article")
    def test_sitemap_requires_extractable_sample(self, sample):
        sample.return_value = {
            "attempted": True,
            "url": "https://example.com/story",
            "ok": False,
            "character_count": 20,
            "reason": "short_content",
        }
        xml = """<?xml version="1.0"?>
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          <url><loc>https://example.com/story</loc><lastmod>2026-08-30</lastmod></url>
        </urlset>"""
        session = FakeSession({
            "https://example.com/sitemap.xml": FakeResponse(
                "https://example.com/sitemap.xml", xml
            )
        })
        result = validate_sitemap(
            session,
            "https://example.com/sitemap.xml",
            "https://example.com",
            CHECKED_AT,
        )
        self.assertFalse(result["passed"])
        self.assertEqual("sample_extraction_failed", result["reason"])

    def test_disables_rss_only_after_second_permanent_failure(self):
        record = {
            "rss_active": "TRUE",
            "rss_permanent_failures": "0",
        }
        first = {"state": "permanent_error", "reason": "http_404"}
        self.assertIsNone(apply_rss_result(record, first))
        self.assertEqual("TRUE", record["rss_active"])
        self.assertEqual("1", record["rss_permanent_failures"])

        second = {"state": "permanent_error", "reason": "http_404"}
        action = apply_rss_result(record, second)
        self.assertEqual("rss_disabled", action["type"])
        self.assertEqual("FALSE", record["rss_active"])
        self.assertEqual("2", record["rss_permanent_failures"])

    def test_temporary_rss_failure_never_increments_or_disables(self):
        for reason in ("http_403", "http_429", "timeout", "server_error"):
            with self.subTest(reason=reason):
                record = {
                    "rss_active": "TRUE",
                    "rss_permanent_failures": "1",
                }
                result = {"state": "temporary_error", "reason": reason}
                action = apply_rss_result(record, result)
                self.assertIsNone(action)
                self.assertEqual("TRUE", record["rss_active"])
                self.assertEqual("0", record["rss_permanent_failures"])

    def test_reactivates_an_rss_feed_disabled_by_the_diagnostic(self):
        record = {
            "rss_active": "FALSE",
            "rss_permanent_failures": "2",
            "rss_disabled_reason": "http_404",
        }
        result = {"state": "healthy", "reason": "ok"}
        action = apply_rss_result(record, result)
        self.assertEqual("rss_reactivated", action["type"])
        self.assertEqual("TRUE", record["rss_active"])
        self.assertEqual("0", record["rss_permanent_failures"])

    @patch("source_health_check.extract_sample_article")
    def test_feed_without_recent_entries_is_stale_not_disabled(self, sample):
        sample.return_value = {
            "attempted": True,
            "url": "https://example.com/old-story",
            "ok": True,
            "character_count": 1200,
            "reason": "ok",
        }
        feed = """<?xml version="1.0"?>
        <rss version="2.0"><channel><title>Example</title>
        <item><title>Old</title><link>https://example.com/old-story</link>
        <pubDate>Mon, 01 Jun 2026 10:00:00 GMT</pubDate></item>
        </channel></rss>"""
        session = FakeSession({
            "https://example.com/feed": FakeResponse(
                "https://example.com/feed", feed
            )
        })
        result = validate_rss(
            session,
            "https://example.com/feed",
            "https://example.com",
            CHECKED_AT,
        )
        self.assertEqual("stale", result["state"])
        self.assertEqual("no_recent_entries", result["reason"])

    @patch("source_health_check.extract_sample_article")
    def test_configured_cross_domain_rss_is_allowed(self, sample):
        sample.return_value = {
            "attempted": True,
            "url": "https://publisher.example/story",
            "ok": True,
            "character_count": 1200,
            "reason": "ok",
        }
        feed = """<?xml version="1.0"?>
        <rss version="2.0"><channel><title>Example</title>
        <item><title>Current</title><link>https://publisher.example/story</link>
        <pubDate>Sun, 30 Aug 2026 10:00:00 GMT</pubDate></item>
        </channel></rss>"""
        session = FakeSession({
            "https://feeds.example.net/publisher": FakeResponse(
                "https://feeds.example.net/publisher", feed
            )
        })
        result = validate_rss(
            session,
            "https://feeds.example.net/publisher",
            "https://publisher.example",
            CHECKED_AT,
        )
        self.assertEqual("healthy", result["state"])

    def test_dry_run_header_extension_does_not_write_sheet(self):
        class Worksheet:
            def __init__(self):
                self.updated = False

            def row_values(self, _row):
                return ["publication", "base_url"]

            def update(self, *_args):
                self.updated = True

        worksheet = Worksheet()
        headers = ensure_headers(
            worksheet, ("rss_active",), write=False
        )
        self.assertIn("rss_active", headers)
        self.assertFalse(worksheet.updated)

    def test_sheet_updates_do_not_overwrite_unrelated_or_formula_columns(self):
        class Worksheet:
            def batch_update(self, batch):
                self.batch = batch

        worksheet = Worksheet()
        headers = [
            "publication", "formula_column", "active", "sitemap_url", "rss_active",
            "sitemap_health_status", "rss_health_status",
            "rss_permanent_failures", "rss_disabled_reason",
            "source_last_checked_at",
        ]
        record = {
            "publication": "Example",
            "formula_column": "computed value",
            "sitemap_url": "https://example.com/sitemap.xml",
            "rss_active": "TRUE",
        }
        save_source_records(worksheet, headers, [(2, record)])
        ranges = {item["range"] for item in worksheet.batch}
        self.assertNotIn("A2", ranges)
        self.assertNotIn("B2", ranges)
        self.assertIn("C2", ranges)
        self.assertIn("D2", ranges)

    @patch("source_health_check.validate_sitemap")
    @patch("source_health_check.discover_sitemap_urls")
    def test_process_source_proposes_only_unambiguous_replacement(
        self, discover, validate
    ):
        discover.return_value = (
            "https://example.com/robots.txt",
            ["https://example.com/news-sitemap.xml"],
            {"https://example.com/news-sitemap.xml"},
        )
        validate.side_effect = lambda _session, url, *_args: sitemap_result(
            url, passed=url.endswith("news-sitemap.xml")
        )
        record = {
            "list_name": "Luxury",
            "publication": "Example",
            "base_url": "https://example.com",
            "sitemap_url": "https://example.com/broken.xml",
            "rss_url": "",
        }
        result = process_source(
            record, 2, object(), CHECKED_AT, {}, {}, apply_fixes=False
        )
        self.assertEqual("sitemap_replaced", result["actions"][0]["type"])
        self.assertFalse(
            result["sitemap"]["replacement_search"]["configuration_updated"]
        )

    def test_healthy_rss_keeps_source_usable_when_sitemap_is_broken(self):
        record = {"active": "TRUE", "source_permanent_failures": "1"}
        status = apply_source_availability_policy(
            record,
            {"state": "permanent_error"},
            [{"state": "healthy", "active_after": True}],
            "Example", [], [],
        )
        self.assertEqual("degraded", status)
        self.assertEqual("0", record["source_permanent_failures"])
        self.assertEqual("TRUE", record["active"])

    def test_source_is_quarantined_after_two_permanent_failures(self):
        record = {
            "active": "TRUE",
            "base_url": "https://example.com",
            "source_permanent_failures": "1",
        }
        actions = []
        attention = []
        status = apply_source_availability_policy(
            record, {"state": "permanent_error"}, [], "Example",
            actions, attention,
        )
        self.assertEqual("genuinely_unresolved", status)
        self.assertEqual("FALSE", record["active"])
        self.assertEqual("TRUE", record["source_health_managed"])
        self.assertEqual("source_quarantined", actions[0]["type"])
        self.assertTrue(attention[0]["unresolved"])

    def test_temporary_failure_never_quarantines_source(self):
        record = {
            "active": "TRUE", "source_permanent_failures": "1"
        }
        status = apply_source_availability_policy(
            record, {"state": "temporary_error"}, [], "Example", [], []
        )
        self.assertEqual("retry_pending", status)
        self.assertEqual("TRUE", record["active"])
        self.assertEqual("0", record["source_permanent_failures"])

    def test_managed_source_reactivates_but_manual_inactive_source_is_skipped(self):
        managed = {
            "active": "FALSE",
            "base_url": "https://example.com",
            "source_health_managed": "TRUE",
            "source_permanent_failures": "2",
        }
        manual = {
            "active": "FALSE",
            "base_url": "https://example.com",
            "source_health_managed": "FALSE",
        }
        self.assertTrue(should_check_source(managed))
        self.assertFalse(should_check_source(manual))
        actions = []
        status = apply_source_availability_policy(
            managed, {"state": "healthy"}, [], "Example", actions, []
        )
        self.assertEqual("repaired", status)
        self.assertEqual("TRUE", managed["active"])
        self.assertEqual("source_reactivated", actions[0]["type"])

    def test_report_is_json_serializable_and_markdown_has_sections(self):
        source = {
            "list_name": "Luxury",
            "publication": "Example",
            "sheet_row": 2,
            "base_url": "https://example.com",
            "active": True,
            "overall_status": "healthy",
            "checked_at": "2026-08-31T15:00:00Z",
            "sitemap": sitemap_result("https://example.com/sitemap.xml"),
            "rss_feeds": [],
            "actions": [],
            "attention": [],
        }
        report = build_report([source], CHECKED_AT, False, "Source Lists")
        encoded = json.dumps(report)
        self.assertIn('"schema_version": 3', encoded)
        self.assertEqual("Luxury", report["topics"][0]["topic"])
        self.assertEqual(1, report["topics"][0]["summary"]["sources_checked"])
        self.assertEqual(
            ["Example"],
            report["topics"][0]["publications_by_status"]["healthy"],
        )
        markdown = markdown_report(report)
        self.assertIn("# Source Health Diagnostic", markdown)
        self.assertIn("## Topic summary", markdown)
        self.assertIn("| Luxury | 1 | 1 |", markdown)
        self.assertIn("## Source status", markdown)


if __name__ == "__main__":
    unittest.main()
