"""Build a primary-first candidate queue for the weekly roundup."""

from __future__ import annotations

import os
from collections import Counter, defaultdict
from datetime import datetime
from typing import Iterable, List, TypeVar


ArticleT = TypeVar("ArticleT")

MIN_ROUNDUP_ARTICLES = int(os.getenv("MIN_ROUNDUP_ARTICLES", "40"))
TARGET_ROUNDUP_ARTICLES = int(os.getenv("TARGET_ROUNDUP_ARTICLES", "50"))
# Keep the legacy environment-variable name, but use it as a candidate-attempt
# ceiling. The summarizer stops at TARGET_ROUNDUP_ARTICLES successful outputs.
MAX_ROUNDUP_CANDIDATES = int(os.getenv("MAX_ROUNDUP_ARTICLES", "60"))
SOFT_PUBLICATION_CAP = int(os.getenv("SOFT_PUBLICATION_CAP", "5"))
HARD_PUBLICATION_CAP = int(os.getenv("HARD_PUBLICATION_CAP", "10"))
MAX_RESERVE_ARTICLES = int(os.getenv("MAX_RESERVE_ARTICLES", "10"))
MAX_GENERAL_ROYAL_ARTICLES = int(os.getenv("MAX_GENERAL_ROYAL_ARTICLES", "8"))
MAX_LIFESTYLE_RESERVE_ARTICLES = int(
    os.getenv("MAX_LIFESTYLE_RESERVE_ARTICLES", "5")
)

if not 0 < MIN_ROUNDUP_ARTICLES <= TARGET_ROUNDUP_ARTICLES:
    raise ValueError("Roundup limits must satisfy 0 < minimum <= target")
if MAX_ROUNDUP_CANDIDATES < TARGET_ROUNDUP_ARTICLES:
    raise ValueError("Candidate maximum must be at least the roundup target")
if not 0 < SOFT_PUBLICATION_CAP <= HARD_PUBLICATION_CAP:
    raise ValueError("Publication caps must satisfy 0 < soft <= hard")


PRIMARY_CATEGORIES = {
    "jewelry_product",
    "luxury_product",
    "luxury_brand",
    "luxury_business",
    "designer_or_runway",
    "royal_jewelry",
    "royal_wardrobe",
    "luxury_market_trend",
}
RESERVE_CATEGORIES = {
    "general_royal_news",
    "celebrity_style",
    "high_street_fashion",
    "general_beauty_trend",
    "consumer_lifestyle",
    "publication_meta",
    "classifier_unavailable",
}

CATEGORY_PRIORITY = {
    "jewelry_product": 0,
    "luxury_business": 0,
    "luxury_market_trend": 0,
    "luxury_brand": 1,
    "royal_jewelry": 1,
    "designer_or_runway": 2,
    "luxury_product": 2,
    "royal_wardrobe": 3,
    "general_royal_news": 4,
    "celebrity_style": 5,
    "high_street_fashion": 6,
    "consumer_lifestyle": 7,
    "general_beauty_trend": 8,
    "publication_meta": 9,
    "classifier_unavailable": 10,
}


def article_disposition(article: object) -> str:
    category = str(getattr(article, "classifier_category", "") or "")
    if category in PRIMARY_CATEGORIES:
        return "primary"
    if category in RESERVE_CATEGORIES:
        return "reserve"
    return "reject"


def _published_timestamp(article: object) -> float:
    value = getattr(article, "published_date", None)
    if isinstance(value, datetime):
        return value.timestamp()
    if value:
        try:
            return datetime.fromisoformat(
                str(value).replace("Z", "+00:00")
            ).timestamp()
        except (TypeError, ValueError):
            pass
    return 0.0


def _quality_key(article: object) -> tuple:
    category = str(getattr(article, "classifier_category", "") or "")
    score = float(getattr(article, "relevance_score", 0.0) or 0.0)
    return (
        CATEGORY_PRIORITY.get(category, 99),
        -score,
        -_published_timestamp(article),
        str(getattr(article, "title", "") or "").casefold(),
    )


def _reserve_group(article: object) -> str:
    category = str(getattr(article, "classifier_category", "") or "")
    return "general_royal" if category == "general_royal_news" else "lifestyle"


def select_roundup_articles(articles: Iterable[ArticleT]) -> List[ArticleT]:
    """Return primary candidates followed by a capped reserve queue."""

    article_list = list(articles)
    primary = [
        article for article in article_list
        if article_disposition(article) == "primary"
    ]
    reserve = [
        article for article in article_list
        if article_disposition(article) == "reserve"
    ]

    selected: List[ArticleT] = []
    selected_ids = set()
    publication_counts = Counter()
    reserve_group_counts = Counter()

    def may_add(article: ArticleT, reserve_article: bool) -> bool:
        publication = str(
            getattr(article, "publication", "Unknown") or "Unknown"
        )
        if publication_counts[publication] >= HARD_PUBLICATION_CAP:
            return False
        if reserve_article:
            group = _reserve_group(article)
            if (
                group == "general_royal"
                and reserve_group_counts[group] >= MAX_GENERAL_ROYAL_ARTICLES
            ):
                return False
            if (
                group == "lifestyle"
                and reserve_group_counts[group] >= MAX_LIFESTYLE_RESERVE_ARTICLES
            ):
                return False
        return True

    def add(article: ArticleT, reserve_article: bool) -> bool:
        if id(article) in selected_ids or not may_add(article, reserve_article):
            return False
        publication = str(
            getattr(article, "publication", "Unknown") or "Unknown"
        )
        selected.append(article)
        selected_ids.add(id(article))
        publication_counts[publication] += 1
        if reserve_article:
            reserve_group_counts[_reserve_group(article)] += 1
        return True

    def add_diverse(pool, limit, reserve_article=False):
        grouped = defaultdict(list)
        for article in pool:
            publication = str(
                getattr(article, "publication", "Unknown") or "Unknown"
            )
            grouped[publication].append(article)
        for publication in grouped:
            grouped[publication].sort(key=_quality_key)

        for position in range(SOFT_PUBLICATION_CAP):
            for publication in sorted(grouped, key=str.casefold):
                if len(selected) >= limit:
                    return
                candidates = grouped[publication]
                if position < len(candidates):
                    add(candidates[position], reserve_article)

        overflow = sorted(
            (article for article in pool if id(article) not in selected_ids),
            key=_quality_key,
        )
        for article in overflow:
            if len(selected) >= limit:
                return
            add(article, reserve_article)

    # Primary articles always precede reserves. Extra primary candidates serve
    # as higher-quality summary-failure replacements before reserves are tried.
    add_diverse(primary, MAX_ROUNDUP_CANDIDATES, reserve_article=False)

    reserve_limit = min(
        MAX_ROUNDUP_CANDIDATES,
        len(selected) + MAX_RESERVE_ARTICLES,
    )
    add_diverse(reserve, reserve_limit, reserve_article=True)

    primary_count = sum(
        article_disposition(article) == "primary" for article in selected
    )
    print(
        f"Candidate queue: {primary_count} primary + "
        f"{len(selected) - primary_count} reserve = {len(selected)}"
    )
    return selected[:MAX_ROUNDUP_CANDIDATES]
