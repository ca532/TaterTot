"""Select a large, balanced set of qualified articles for a roundup."""

from __future__ import annotations

import os
from collections import Counter, defaultdict
from datetime import datetime
from typing import Iterable, List, TypeVar


ArticleT = TypeVar("ArticleT")

MIN_ROUNDUP_ARTICLES = int(os.getenv("MIN_ROUNDUP_ARTICLES", "40"))
TARGET_ROUNDUP_ARTICLES = int(os.getenv("TARGET_ROUNDUP_ARTICLES", "50"))
MAX_ROUNDUP_ARTICLES = int(os.getenv("MAX_ROUNDUP_ARTICLES", "60"))
SOFT_PUBLICATION_CAP = int(os.getenv("SOFT_PUBLICATION_CAP", "5"))
HARD_PUBLICATION_CAP = int(os.getenv("HARD_PUBLICATION_CAP", "10"))

if not 0 < MIN_ROUNDUP_ARTICLES <= TARGET_ROUNDUP_ARTICLES <= MAX_ROUNDUP_ARTICLES:
    raise ValueError(
        "Roundup limits must satisfy 0 < minimum <= target <= maximum"
    )
if not 0 < SOFT_PUBLICATION_CAP <= HARD_PUBLICATION_CAP:
    raise ValueError(
        "Publication caps must satisfy 0 < soft cap <= hard cap"
    )


CATEGORY_PRIORITY = {
    "jewelry_product": 0,
    "luxury_business": 0,
    "luxury_market_trend": 0,
    "luxury_brand": 1,
    "royal_jewelry": 1,
    "designer_or_runway": 2,
    "luxury_product": 2,
    "royal_wardrobe": 3,
    "classifier_unavailable": 4,
}


def _published_timestamp(article: object) -> float:
    value = getattr(article, "published_date", None)
    if isinstance(value, datetime):
        return value.timestamp()
    if value:
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
        except (TypeError, ValueError):
            pass
    return 0.0


def _quality_key(article: object) -> tuple:
    category = str(getattr(article, "classifier_category", "") or "")
    score = float(getattr(article, "relevance_score", 0.0) or 0.0)
    return (
        CATEGORY_PRIORITY.get(category, 5),
        -score,
        -_published_timestamp(article),
        str(getattr(article, "title", "") or "").casefold(),
    )


def select_roundup_articles(articles: Iterable[ArticleT]) -> List[ArticleT]:
    """Select diverse articles first, then fill toward the global target.

    The first pass selects up to ``SOFT_PUBLICATION_CAP`` articles from each
    publication in round-robin order. If that produces fewer than the global
    target, the strongest unused articles fill the remaining slots while the
    absolute ``HARD_PUBLICATION_CAP`` is enforced.
    """
    article_list = list(articles)
    grouped = defaultdict(list)
    for article in article_list:
        grouped[str(getattr(article, "publication", "Unknown") or "Unknown")].append(article)

    for publication in grouped:
        grouped[publication].sort(key=_quality_key)

    selected: List[ArticleT] = []
    selected_ids = set()
    publication_counts = Counter()

    # Round-robin selection stops early sources from consuming the global cap.
    for position in range(SOFT_PUBLICATION_CAP):
        for publication in sorted(grouped, key=str.casefold):
            if len(selected) >= MAX_ROUNDUP_ARTICLES:
                break
            candidates = grouped[publication]
            if position >= len(candidates):
                continue
            article = candidates[position]
            selected.append(article)
            selected_ids.add(id(article))
            publication_counts[publication] += 1

    # Fill toward the target with the strongest remaining articles.
    if len(selected) < TARGET_ROUNDUP_ARTICLES:
        overflow = sorted(
            (article for article in article_list if id(article) not in selected_ids),
            key=_quality_key,
        )
        for article in overflow:
            if len(selected) >= TARGET_ROUNDUP_ARTICLES:
                break
            publication = str(
                getattr(article, "publication", "Unknown") or "Unknown"
            )
            if publication_counts[publication] >= HARD_PUBLICATION_CAP:
                continue
            selected.append(article)
            selected_ids.add(id(article))
            publication_counts[publication] += 1

    return selected[:MAX_ROUNDUP_ARTICLES]
