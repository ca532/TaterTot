from __future__ import annotations

import unittest

from backend.client_coverage_search import split_search_queries


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


if __name__ == "__main__":
    unittest.main()
