import os
import unittest
from unittest.mock import MagicMock, patch

from AgentCollector import CustomArticleCollector


class SourceHealthCollectorTests(unittest.TestCase):
    def test_disabled_rss_is_removed_without_disabling_sitemap(self):
        collector = CustomArticleCollector.__new__(CustomArticleCollector)
        collector.source_list_name = "Luxury"

        worksheet = MagicMock()
        worksheet.get_all_records.return_value = [{
            "list_name": "Luxury",
            "publication": "Example",
            "base_url": "https://example.com",
            "sitemap_url": "https://example.com/sitemap.xml",
            "rss_url": "https://example.com/feed",
            "rss_active": "FALSE",
            "active": "TRUE",
        }]
        client = MagicMock()
        client.open_by_key.return_value.worksheet.return_value = worksheet

        with patch.dict(os.environ, {
            "GOOGLE_SHEET_ID": "sheet-id",
            "GOOGLE_CREDENTIALS": "{}",
        }, clear=False), patch(
            "AgentCollector.Credentials.from_service_account_info"
        ), patch(
            "AgentCollector.gspread.authorize", return_value=client
        ):
            sources = collector._load_sources_from_sheet()

        self.assertEqual([], sources["Example"]["rss_feeds"])
        self.assertEqual(
            "https://example.com/sitemap.xml",
            sources["Example"]["sitemap_url"],
        )


if __name__ == "__main__":
    unittest.main()
