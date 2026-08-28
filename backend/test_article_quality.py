import unittest
from datetime import datetime, timedelta, timezone

from article_quality import (
    extract_page_metadata,
    has_low_signal_intent,
    keyword_matches,
    normalize_publication_name,
    prepare_article_for_classification,
    resolve_published_date,
    validate_article_content,
    validate_author,
    validate_candidate_url,
    validate_summary,
    within_lookback,
)


class ArticleQualityTests(unittest.TestCase):
    def test_rejects_homepages_and_known_landing_pages(self):
        invalid_urls = [
            "https://www.businessinsider.com/",
            "https://www.businessinsider.com/artificial-intelligence",
            "https://www.businessinsider.com/a-smarter-way",
            "https://www.retail-jeweller.com/",
            "https://thejewels.club/",
            "https://example.com/category/luxury",
        ]
        for url in invalid_urls:
            with self.subTest(url=url):
                self.assertFalse(validate_candidate_url(url)[0])

        self.assertTrue(
            validate_candidate_url(
                "https://www.forbes.com/sites/example/2026/08/01/luxury-market-growth/"
            )[0]
        )

    def test_rejects_boilerplate_and_accepts_article_prose(self):
        consent = "To enable Google Custom Search, please click Allow and Continue. " * 20
        listing = "Exclusive Story 2 min read Another Story 3 min read " * 20
        prose = (
            "The jewellery house introduced a new collection in London. "
            "Its designers discussed craftsmanship, materials, and long-term investment. "
            "Executives said the launch reflects demand across Europe and the United States. "
        ) * 8
        self.assertFalse(validate_article_content(consent)[0])
        self.assertFalse(validate_article_content(listing)[0])
        self.assertTrue(validate_article_content(prose)[0])

    def test_rejects_false_authors(self):
        for author in ("Every Time", "Josh Brandinfusion.Co.Za", "news@example.com"):
            with self.subTest(author=author):
                self.assertEqual(validate_author(author), "Unknown")
        self.assertEqual(validate_author("Clara Ludmir"), "Clara Ludmir")

    def test_page_metadata_wins_and_dates_respect_lookback(self):
        now = datetime.now(timezone.utc)
        rss_date = now - timedelta(days=2)
        page_date = now - timedelta(days=1)
        resolved, source = resolve_published_date(
            {"published_date": page_date.isoformat(), "published_date_source": "json_ld"},
            rss_date=rss_date,
        )
        self.assertEqual(source, "json_ld")
        self.assertEqual(resolved.date(), page_date.date())
        self.assertTrue(within_lookback(resolved, 14, now=now))
        self.assertFalse(within_lookback(now - timedelta(days=15), 14, now=now))
        self.assertFalse(within_lookback(now + timedelta(days=1), 14, now=now))

    def test_rejects_prompt_leakage_and_repetition(self):
        prompt = "Focus on luxury brands jewellery houses designers and craftsmanship."
        self.assertFalse(validate_summary(prompt, prompt)[0])
        self.assertFalse(validate_summary("Luxury launch. " * 30, prompt)[0])
        valid = (
            "LVMH reported improved sales led by watches and jewellery, while executives "
            "said demand remained uneven across Europe, Asia, and the United States."
        )
        self.assertTrue(validate_summary(valid, prompt)[0])

    def test_rejects_truncation_and_unsupported_numeric_claims(self):
        source = " ".join([
            "The company reported revenue of EUR 1.44 trillion for the period.",
            "Executives said jewelry demand remained strong across major markets.",
            "European clients continued to favor established jewelry houses and designers.",
            "Asian markets recorded increased interest in colored gemstones and watches.",
            "Retail investment focused on flagship stores and private client experiences.",
            "The group introduced new collections combining craftsmanship with contemporary design.",
            "Management expects marketing activity to support launches during the next quarter.",
            "High jewelry remained an important contributor to the wider luxury portfolio.",
            "The report also described stable demand among clients in the United States.",
            "Executives plan to maintain investment in artisans, workshops, and product development.",
        ])
        truncated = (
            "The company reported strong demand across its major international "
            "markets while jewelry remained central to its strategy [...]"
        )
        invented_number = source.replace("1.44", "9.99")

        self.assertEqual(
            "truncated_summary",
            validate_summary(truncated, source_text=source)[1],
        )
        self.assertEqual(
            "unsupported_numeric_claim",
            validate_summary(invented_number, source_text=source)[1],
        )

    def test_normalizes_known_publication_names(self):
        self.assertEqual(normalize_publication_name("businessinsider"), "Business Insider")
        self.assertEqual(normalize_publication_name("harpersbazaar"), "Harper's Bazaar")
        self.assertEqual(normalize_publication_name("nytimes"), "The New York Times")

    def test_extracts_publication_name_from_open_graph(self):
        metadata = extract_page_metadata(
            '<meta property="og:site_name" content="harpersbazaar">'
        )

        self.assertEqual(metadata["publication_name"], "Harper's Bazaar")
        self.assertEqual(metadata["publication_name_source"], "og_site_name")

    def test_extracts_json_ld_publisher_when_open_graph_is_unavailable(self):
        metadata = extract_page_metadata(
            """
            <script type="application/ld+json">
            {
              "@type": "NewsArticle",
              "headline": "Example story",
              "publisher": {"@type": "Organization", "name": "Robb Report"}
            }
            </script>
            """
        )

        self.assertEqual(metadata["publication_name"], "Robb Report")
        self.assertEqual(metadata["publication_name_source"], "json_ld_publisher")

    def test_ignores_generic_publication_metadata(self):
        metadata = extract_page_metadata(
            '<meta property="og:site_name" content="News">'
        )

        self.assertEqual(metadata["publication_name"], "")
        self.assertEqual(metadata["publication_name_source"], "")

    def test_identifies_low_signal_article_intent(self):
        self.assertTrue(has_low_signal_intent("The perfect date night top for GBP 36"))
        self.assertTrue(has_low_signal_intent("This year's festival party looks"))
        self.assertFalse(has_low_signal_intent("Cartier unveils a high jewellery collection"))

    def test_keyword_matching_uses_word_and_phrase_boundaries(self):
        keywords = ["king", "fine jewellery", "investment"]
        self.assertEqual([], keyword_matches("working and speaking", keywords))
        self.assertEqual(
            ["fine jewellery", "investment"],
            keyword_matches(
                "A fine-jewellery house announced an investment in craftsmanship.",
                keywords,
            ),
        )

    def test_prepares_classifier_text_without_page_debris(self):
        cleaned = prepare_article_for_classification(
            "Boucheron launched a high-jewelry collection. "
            "Click here to subscribe. "
            "The pieces feature diamonds and rock crystal. "
            "All Rights Reserved."
        )
        self.assertIn("Boucheron launched", cleaned)
        self.assertIn("diamonds and rock crystal", cleaned)
        self.assertNotIn("Click here", cleaned)
        self.assertNotIn("All Rights Reserved", cleaned)


if __name__ == "__main__":
    unittest.main()
