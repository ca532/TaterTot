from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from backend import publication_country


class FakeWorksheet:
    def __init__(self, records=None):
        self.records = records or []
        self.appended_rows = []
        self.batch_updates = []

    def get_all_records(self):
        return self.records

    def get_all_values(self):
        return [publication_country.COUNTRY_HEADERS] + [
            [
                record.get(header, "")
                for header in publication_country.COUNTRY_HEADERS
            ]
            for record in self.records
        ]

    def append_rows(self, rows, value_input_option=None):
        self.appended_rows.extend(rows)

    def batch_update(self, updates, value_input_option=None):
        self.batch_updates.extend(updates)


class PublicationCountryTests(unittest.TestCase):
    def test_country_from_url_supports_country_domains_and_editions(self):
        for country in publication_country.pycountry.countries:
            code = country.alpha_2

            edition_urls = (
                f"https://example.com/en-{code.lower()}/story",
                f"https://goal.com/{code.lower()}/story",
            )
            for url in edition_urls:
                with self.subTest(kind="edition", url=url):
                    result = publication_country.country_from_url(
                        url
                    )
                    self.assertIsNotNone(result)
                    self.assertEqual(result["country_code"], code)
                    self.assertEqual(result["source"], "url_edition")

            if code.lower() not in publication_country.GENERIC_CCTLDS:
                with self.subTest(kind="domain", country_code=code):
                    result = publication_country.country_from_url(
                        f"https://publication.{code.lower()}/story"
                    )
                    self.assertIsNotNone(result)
                    self.assertEqual(result["country_code"], code)
                    self.assertEqual(result["source"], "country_domain")

    def test_country_from_url_supports_code_aliases(self):
        cases = {
            "https://www.thesun.co.uk/story": "GB",
            "https://goal.com/uk/story": "GB",
            "https://news.example.com.au/story": "AU",
            "https://news.example.co.nz/story": "NZ",
        }

        for url, expected_code in cases.items():
            with self.subTest(url=url):
                result = publication_country.country_from_url(url)
                actual_code = result["country_code"] if result else None
                self.assertEqual(actual_code, expected_code)

    def test_country_from_url_ignores_ambiguous_and_non_country_urls(self):
        urls = [
            "https://example.com/story",
            "https://facebook.com/publication/story",
            "https://m.facebook.com/publication/story",
            "https://instagram.com/publication/story",
            "https://example.com/en-xx/story",
            "https://example.com/usa/story",
            "https://example.com/english-us/story",
            "https://example.com/fr/story",
            "https://example.com/de/story",
            "https://example.com/pl/story",
            "not-a-url",
            "",
        ]
        urls.extend(
            f"https://publisher.{suffix}/story"
            for suffix in publication_country.GENERIC_CCTLDS
        )

        for url in urls:
            with self.subTest(url=url):
                self.assertIsNone(publication_country.country_from_url(url))

    def test_header_only_country_registry_loads_as_empty(self):
        class HeaderOnlyWorksheet(FakeWorksheet):
            def get_all_records(self):
                raise IndexError("list index out of range")

        worksheet = HeaderOnlyWorksheet()

        registry = publication_country.load_registry(worksheet)

        self.assertEqual(registry, {})

    @patch("backend.publication_country.requests.get")
    def test_google_fallback_reads_organic_result_snippets(self, mock_get):
        response = Mock()
        response.json.return_value = {
            "search_metadata": {"id": "search-123"},
            "organic_results": [{
                "title": "Example publication",
                "snippet": "The newspaper is based in the United Kingdom.",
            }],
        }
        response.raise_for_status.return_value = None
        mock_get.return_value = response

        result = publication_country.lookup_google_country(
            "Example", "example.com", "api-key", "google.com", "us", "en"
        )

        self.assertEqual(result["country_code"], "GB")
        self.assertEqual(result["source"], "serpapi_organic_results")

    @patch("backend.publication_country.ensure_country_sheet")
    @patch("backend.publication_country.lookup_wikidata_country")
    def test_legacy_unresolved_registry_entry_is_retried(
        self,
        mock_wikidata,
        mock_ensure_sheet,
    ):
        worksheet = FakeWorksheet([{
            "lookup_key": "example.com",
            "publication": "Example",
            "country": "",
            "country_code": "",
            "source": "unresolved",
            "confidence": "low",
            "manual_override": "FALSE",
            "status": "unresolved",
            "source_reference": "",
            "checked_at": "2026-08-20",
            "retry_after": "2099-01-01",
        }])
        mock_ensure_sheet.return_value = worksheet
        mock_wikidata.return_value = {
            "country": "Canada",
            "country_code": "CA",
            "source": "wikidata",
            "confidence": "high",
        }
        rows = [{
            "article_url": "https://example.com/story",
            "domain": "example.com",
            "publication": "Example",
        }]

        publication_country.enrich_publication_countries(
            rows,
            db=object(),
            serpapi_api_key="",
        )

        mock_wikidata.assert_called_once_with("example.com")
        self.assertEqual(rows[0]["country"], "Canada")
        self.assertEqual(worksheet.appended_rows, [])
        self.assertEqual(len(worksheet.batch_updates), 1)

    @patch("backend.publication_country.ensure_country_sheet")
    def test_source_label_is_used_before_network_fallback(self, mock_ensure_sheet):
        worksheet = FakeWorksheet()
        mock_ensure_sheet.return_value = worksheet
        rows = [{
            "article_url": "https://goal.com/story",
            "domain": "goal.com",
            "publication": "Goal",
            "_publication_hint": "Goal.com Australia",
        }]

        with patch(
            "backend.publication_country.lookup_wikidata_country"
        ) as mock_wikidata:
            publication_country.enrich_publication_countries(
                rows,
                db=object(),
                serpapi_api_key="",
            )

        mock_wikidata.assert_not_called()
        self.assertEqual(rows[0]["country"], "Australia")
        self.assertEqual(rows[0]["country_source"], "search_source_label")

    @patch("backend.publication_country.ensure_country_sheet")
    def test_page_metadata_does_not_override_source_label(self, mock_ensure_sheet):
        worksheet = FakeWorksheet()
        mock_ensure_sheet.return_value = worksheet
        rows = [{
            "article_url": "https://goal.com/story",
            "domain": "goal.com",
            "publication": "Goal",
            "_country_hint": {
                "country": "United States",
                "country_code": "US",
                "source": "page_metadata",
                "confidence": "medium",
            },
            "_publication_hint": "Goal.com Australia",
        }]

        with patch(
            "backend.publication_country.lookup_wikidata_country"
        ) as mock_wikidata:
            publication_country.enrich_publication_countries(
                rows,
                db=object(),
                serpapi_api_key="",
            )

        mock_wikidata.assert_not_called()
        self.assertEqual(rows[0]["country"], "Australia")
        self.assertEqual(rows[0]["country_source"], "search_source_label")

    @patch("backend.publication_country.ensure_country_sheet")
    def test_manual_override_remains_highest_priority(self, mock_ensure_sheet):
        worksheet = FakeWorksheet([{
            "lookup_key": "example.com",
            "publication": "Example",
            "country": "Qatar",
            "country_code": "QA",
            "source": "page_metadata",
            "confidence": "medium",
            "manual_override": "TRUE",
            "status": "resolved",
            "source_reference": "",
            "checked_at": "2026-08-27",
            "retry_after": "",
        }])
        mock_ensure_sheet.return_value = worksheet
        rows = [{
            "article_url": "https://example.com/story",
            "domain": "example.com",
            "publication": "Example",
        }]

        with patch(
            "backend.publication_country.lookup_wikidata_country"
        ) as mock_wikidata:
            publication_country.enrich_publication_countries(
                rows,
                db=object(),
                serpapi_api_key="",
            )

        mock_wikidata.assert_not_called()
        self.assertEqual(rows[0]["country"], "Qatar")
        self.assertEqual(rows[0]["country_source"], "manual")
        self.assertEqual(worksheet.appended_rows, [])
        self.assertEqual(worksheet.batch_updates, [])

    @patch("backend.publication_country.ensure_country_sheet")
    @patch("backend.publication_country.lookup_wikidata_country")
    def test_page_metadata_registry_record_is_not_trusted(
        self,
        mock_wikidata,
        mock_ensure_sheet,
    ):
        worksheet = FakeWorksheet([{
            "lookup_key": "example.com",
            "publication": "Example",
            "country": "United States",
            "country_code": "US",
            "source": "page_metadata",
            "confidence": "medium",
            "manual_override": "FALSE",
            "status": "resolved",
            "source_reference": "en-US",
            "checked_at": "2026-08-20",
            "retry_after": "",
        }])
        mock_ensure_sheet.return_value = worksheet
        mock_wikidata.return_value = {
            "country": "Qatar",
            "country_code": "QA",
            "source": "wikidata",
            "confidence": "high",
        }
        rows = [{
            "article_url": "https://example.com/story",
            "domain": "example.com",
            "publication": "Example",
        }]

        publication_country.enrich_publication_countries(
            rows,
            db=object(),
            serpapi_api_key="",
        )

        mock_wikidata.assert_called_once_with("example.com")
        self.assertEqual(rows[0]["country"], "Qatar")
        self.assertEqual(rows[0]["country_source"], "wikidata")
        self.assertEqual(worksheet.appended_rows, [])
        self.assertEqual(len(worksheet.batch_updates), 1)

    def test_registry_selects_highest_trust_duplicate(self):
        worksheet = FakeWorksheet([
            {
                "lookup_key": "example.com",
                "country": "Qatar",
                "country_code": "QA",
                "source": "wikidata",
                "manual_override": "FALSE",
            },
            {
                "lookup_key": "example.com",
                "country": "United States",
                "country_code": "US",
                "source": "serpapi_organic_results",
                "manual_override": "FALSE",
            },
        ])

        registry = publication_country.load_registry(worksheet)

        self.assertEqual(registry["example.com"]["country"], "Qatar")
        self.assertEqual(registry["example.com"]["_row_number"], 2)

    @patch("backend.publication_country.ensure_country_sheet")
    def test_stronger_source_updates_weaker_row_in_place(self, mock_ensure_sheet):
        worksheet = FakeWorksheet([{
            "lookup_key": "publisher.qa",
            "publication": "Publisher",
            "country": "United States",
            "country_code": "US",
            "source": "page_metadata",
            "confidence": "medium",
            "manual_override": "FALSE",
            "status": "resolved",
        }])
        mock_ensure_sheet.return_value = worksheet
        rows = [{
            "article_url": "https://publisher.qa/story",
            "domain": "publisher.qa",
            "publication": "Publisher",
        }]

        publication_country.enrich_publication_countries(
            rows,
            db=object(),
            serpapi_api_key="",
        )

        self.assertEqual(rows[0]["country"], "Qatar")
        self.assertEqual(worksheet.appended_rows, [])
        self.assertEqual(len(worksheet.batch_updates), 1)
        self.assertEqual(worksheet.batch_updates[0]["range"], "A2:K2")

    @patch("backend.publication_country.ensure_country_sheet")
    def test_weaker_source_cannot_replace_stronger_row(self, mock_ensure_sheet):
        worksheet = FakeWorksheet([{
            "lookup_key": "goal.com",
            "publication": "Goal",
            "country": "Qatar",
            "country_code": "QA",
            "source": "wikidata",
            "confidence": "high",
            "manual_override": "FALSE",
            "status": "resolved",
        }])
        mock_ensure_sheet.return_value = worksheet
        rows = [{
            "article_url": "https://goal.com/story",
            "domain": "goal.com",
            "publication": "Goal",
            "_publication_hint": "Goal.com Australia",
        }]

        publication_country.enrich_publication_countries(
            rows,
            db=object(),
            serpapi_api_key="",
        )

        self.assertEqual(rows[0]["country"], "Qatar")
        self.assertEqual(rows[0]["country_source"], "wikidata")
        self.assertEqual(worksheet.appended_rows, [])
        self.assertEqual(worksheet.batch_updates, [])

    @patch("backend.publication_country.ensure_country_sheet")
    def test_social_urls_do_not_inherit_country_hints(self, mock_ensure_sheet):
        worksheet = FakeWorksheet()
        mock_ensure_sheet.return_value = worksheet
        rows = [{
            "article_url": "https://instagram.com/p/example",
            "domain": "instagram.com",
            "publication": "Instagram",
            "_country_hint": {
                "country": "United States",
                "country_code": "US",
            },
            "_publication_hint": "Instagram Indonesia",
        }]

        with patch(
            "backend.publication_country.lookup_wikidata_country"
        ) as mock_wikidata, patch(
            "backend.publication_country.lookup_google_country"
        ) as mock_google:
            publication_country.enrich_publication_countries(
                rows,
                db=object(),
                serpapi_api_key="api-key",
            )

        mock_wikidata.assert_not_called()
        mock_google.assert_not_called()
        self.assertEqual(rows[0]["country"], "")
        self.assertEqual(rows[0]["country_source"], "social_unresolved")
        self.assertEqual(rows[0]["country_lookup_key"], "instagram.com")
        self.assertEqual(worksheet.appended_rows, [])


if __name__ == "__main__":
    unittest.main()
