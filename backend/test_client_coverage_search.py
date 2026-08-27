from __future__ import annotations

import unittest
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
