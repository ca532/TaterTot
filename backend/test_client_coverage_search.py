from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from backend import client_coverage_search


split_search_queries = client_coverage_search.split_search_queries


class SearchQueryParsingTests(unittest.TestCase):
    def test_supports_newlines_and_double_pipe_delimiters(self):
        raw = (
            '"Tobias Kormind" "Georgina Rodriguez" || '
            '"77 Diamonds" "Georgina Rodriguez"\n'
            '"Tobias Kormind" Ronaldo "engagement ring"'
        )

        self.assertEqual(
            split_search_queries(raw),
            [
                '"Tobias Kormind" "Georgina Rodriguez"',
                '"77 Diamonds" "Georgina Rodriguez"',
                '"Tobias Kormind" Ronaldo "engagement ring"',
            ],
        )

    def test_removes_empty_and_duplicate_queries(self):
        raw = "First query || || Second query\r\nfirst QUERY"

        self.assertEqual(
            split_search_queries(raw),
            ["First query", "Second query"],
        )


class SerpApiBudgetTests(unittest.TestCase):
    @staticmethod
    def response(results, result_key="organic_results", next_url=""):
        response = Mock()
        response.status_code = 200
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "search_metadata": {"status": "Success"},
            result_key: results,
            "serpapi_pagination": {"next": next_url} if next_url else {},
        }
        return response

    @patch("backend.client_coverage_search.request_serpapi")
    @patch("backend.client_coverage_search.get_serpapi_capacity")
    def test_discovery_stops_at_country_search_reserve(
        self,
        mock_capacity,
        mock_request,
    ):
        mock_capacity.return_value = {
            "monthly_left": 10,
            "hourly_limit": 0,
            "hourly_left": 0,
        }

        result = client_coverage_search.serpapi_search_all(
            queries=["example query"],
            reserved_searches=10,
        )

        mock_request.assert_not_called()
        self.assertEqual(result["searches_used"], 0)
        self.assertEqual(result["searches_remaining"], 10)
        self.assertEqual(
            result["stop_reason"],
            "country_search_reserve_reached",
        )

    @patch("backend.client_coverage_search.request_serpapi")
    @patch("backend.client_coverage_search.get_serpapi_capacity")
    def test_every_entered_query_searches_news_and_web(
        self,
        mock_capacity,
        mock_request,
    ):
        mock_capacity.return_value = {
            "monthly_left": 100,
            "hourly_limit": 0,
            "hourly_left": 100,
        }
        calls = 0

        def result_page(_url, params):
            nonlocal calls
            calls += 1
            key = "news_results" if params.get("tbm") == "nws" else "organic_results"
            return self.response(
                [{"link": f"https://example.com/{calls}", "title": "Story"}],
                result_key=key,
            )

        mock_request.side_effect = result_page
        result = client_coverage_search.serpapi_search_all(
            queries=["query one", "query two", "query three"],
            date_from="2026-08-01",
            date_to="2026-08-14",
        )

        self.assertEqual(mock_request.call_count, 6)
        self.assertEqual(
            {item["search_query"] for item in result["results"]},
            {"query one", "query two", "query three"},
        )
        self.assertEqual(
            {item["search_scope"] for item in result["search_diagnostics"]},
            {"full_range"},
        )

    @patch("backend.client_coverage_search.request_serpapi")
    @patch("backend.client_coverage_search.get_serpapi_capacity")
    def test_stream_stops_after_three_pages_with_no_globally_new_urls(
        self,
        mock_capacity,
        mock_request,
    ):
        mock_capacity.return_value = {
            "monthly_left": 100,
            "hourly_limit": 0,
            "hourly_left": 100,
        }
        links = [
            {"link": f"https://example.com/story-{index}", "title": "Story"}
            for index in range(10)
        ]
        call_number = 0

        def repeated_page(_url, params):
            nonlocal call_number
            call_number += 1
            response = Mock()
            response.status_code = 200
            response.raise_for_status.return_value = None
            response.json.return_value = {
                "search_metadata": {"status": "Success"},
                "news_results": links,
                "organic_results": links,
                "serpapi_pagination": {
                    "next": f"https://serpapi.test/page/{call_number + 1}"
                },
            }
            return response

        mock_request.side_effect = repeated_page
        result = client_coverage_search.serpapi_search_all(
            queries=["query"],
        )

        self.assertEqual(mock_request.call_count, 7)
        self.assertEqual(
            max(
                item["consecutive_global_zero_pages"]
                for item in result["search_diagnostics"]
            ),
            3,
        )


if __name__ == "__main__":
    unittest.main()
