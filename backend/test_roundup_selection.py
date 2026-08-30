import unittest
from dataclasses import dataclass
from datetime import datetime, timedelta

from roundup_selection import select_roundup_articles


@dataclass
class FakeArticle:
    title: str
    publication: str
    relevance_score: float
    classifier_category: str = "jewelry_product"
    published_date: datetime = datetime(2026, 8, 28)


def make_articles(
    publications: int,
    per_publication: int,
    category: str = "jewelry_product",
    prefix: str = "Publication",
):
    articles = []
    for publication_index in range(publications):
        for article_index in range(per_publication):
            articles.append(
                FakeArticle(
                    title=f"Article {publication_index}-{article_index}",
                    publication=f"{prefix} {publication_index:02d}",
                    relevance_score=20 - article_index,
                    classifier_category=category,
                    published_date=datetime(2026, 8, 28)
                    - timedelta(days=article_index),
                )
            )
    return articles


class RoundupSelectionTests(unittest.TestCase):
    def test_diverse_pool_can_fill_global_maximum(self):
        selected = select_roundup_articles(make_articles(12, 12))
        self.assertEqual(60, len(selected))
        counts = {}
        for article in selected:
            counts[article.publication] = counts.get(article.publication, 0) + 1
        self.assertTrue(all(count == 5 for count in counts.values()))

    def test_overflow_fills_target_without_exceeding_hard_cap(self):
        selected = select_roundup_articles(make_articles(6, 12))
        self.assertEqual(60, len(selected))
        counts = {}
        for article in selected:
            counts[article.publication] = counts.get(article.publication, 0) + 1
        self.assertLessEqual(max(counts.values()), 10)

    def test_small_source_set_stops_at_hard_cap(self):
        selected = select_roundup_articles(make_articles(4, 12))
        self.assertEqual(40, len(selected))
        counts = {}
        for article in selected:
            counts[article.publication] = counts.get(article.publication, 0) + 1
        self.assertEqual({10}, set(counts.values()))

    def test_primary_articles_precede_capped_royal_reserves(self):
        primary = make_articles(9, 5)
        reserve = make_articles(
            10,
            1,
            category="general_royal_news",
            prefix="Royal",
        )
        selected = select_roundup_articles(primary + reserve)
        self.assertEqual(53, len(selected))
        self.assertTrue(all(
            article.classifier_category == "jewelry_product"
            for article in selected[:45]
        ))
        self.assertTrue(all(
            article.classifier_category == "general_royal_news"
            for article in selected[45:]
        ))

    def test_lifestyle_reserve_group_is_capped_at_five(self):
        selected = select_roundup_articles(
            make_articles(
                10,
                1,
                category="high_street_fashion",
                prefix="Lifestyle",
            )
        )
        self.assertEqual(5, len(selected))


if __name__ == "__main__":
    unittest.main()
