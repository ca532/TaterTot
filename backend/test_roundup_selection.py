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


def make_articles(publications: int, per_publication: int):
    articles = []
    for publication_index in range(publications):
        for article_index in range(per_publication):
            articles.append(
                FakeArticle(
                    title=f"Article {publication_index}-{article_index}",
                    publication=f"Publication {publication_index:02d}",
                    relevance_score=20 - article_index,
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
        self.assertEqual(50, len(selected))
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


if __name__ == "__main__":
    unittest.main()
