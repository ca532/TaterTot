import html
import json
import re
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from typing import Optional
from urllib.parse import urlparse, urlunparse

from dateutil import parser as date_parser


BLOCKED_PATHS = {
    "/analysis", "/advertising", "/artificial-intelligence",
    "/a-smarter-way", "/above-and-beyond", "/featured-articles",
    "/diamond-price-list",
}
BLOCKED_PATH_PARTS = (
    "/tag/", "/tags/", "/category/", "/categories/", "/author/",
    "/authors/", "/search", "/topic/", "/topics/", "/newsletter",
    "/subscribe", "/archive/", "/gallery/", "/galleries/", "/video/",
    "/videos/", "/podcast/", "/podcasts/", "/live/", "/events/",
)
BLOCKED_CONTENT_MARKERS = (
    "allow and continue", "enable google custom search",
    "please register for guest access", "already have an account",
    "sign up to receive emails", "click here to subscribe",
    "this area is reserved", "privacy policy and terms of service",
)
CLASSIFIER_INPUT_DEBRIS = (
    "click here", "shop now", "shop today", "all rights reserved",
    "stacked from", "method=", "sign up", "subscribe", "related stories",
    "related articles", "read more", "newsletter", "advertisement",
)
GENERIC_TITLES = {
    "analysis", "advertising", "ai", "a smarter way", "above and beyond",
    "featured articles", "jewels club", "home", "latest news",
}
LOW_SIGNAL_TITLE_PATTERNS = (
    # Puzzles and games
    "wordle", "strands answers", "pips hints", "puzzle",
    "hints and answer", "game review",
    # Food and health
    "recipe", "nutrition", "seed oil", "cooking show",
    "vegetarian alternative", "health advice",
    # Generic shopping and beauty
    "get her look", "shopping basket", "buyers guide", "date night top",
    "party look", "must-buy", "where to shop", "beauty products",
    "makeup", "make-up", "bronzing", "blonzer", "hairstyles",
    "cardigan", "driver shoes", "barrel leg trousers", "jean trends",
    "hiking sandal", "summer dress", "slipdress", "jersey dress",
    "shoe emporium",
    # Generic entertainment and gossip
    "secretly get married", "relationship", "reportedly",
    "abuse allegations", "social media", "music festival",
    "festival", "lollapalooza", "movie", "remake",
    # Generic royal and institutional coverage
    "welcomes her third baby", "newborn baby", "college rankings",
    "sailing regatta",
)
NON_ARTICLE_TITLE_PATTERNS = (
    "price list", "membership directory", "subscriber access",
)
INVALID_AUTHORS = {
    "", "unknown", "every time", "authorizing sanctions", "admin",
    "staff", "editor", "editors",
}
PUBLICATION_NAMES = {
    "businessinsider": "Business Insider", "harpersbazaar": "Harper's Bazaar",
    "nationaljeweler": "National Jeweler", "townandcountrymag": "Town & Country",
    "theguardian": "The Guardian", "thecut": "The Cut",
    "thejewels": "The Jewels Club", "vanityfair": "Vanity Fair",
    "nytimes": "The New York Times", "redonline": "Red Online",
    "stylecaster": "StyleCaster", "standard": "Evening Standard",
}


def canonicalize_url(url: str) -> str:
    parsed = urlparse((url or "").strip())
    path = re.sub(r"/{2,}", "/", parsed.path or "/").rstrip("/") or "/"
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), path, "", "", ""))


def normalize_publication_name(name: str) -> str:
    value = clean_article_text(name).strip()
    return PUBLICATION_NAMES.get(re.sub(r"[^a-z0-9]", "", value.lower()), value or "Unknown")


def validate_candidate_url(url: str, publication: str = "") -> tuple[bool, str]:
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False, "invalid_url"
    path = (parsed.path or "/").rstrip("/").lower() or "/"
    if path == "/" or path in BLOCKED_PATHS:
        return False, "homepage_or_landing_page"
    if any(part in path for part in BLOCKED_PATH_PARTS):
        return False, "non_article_path"
    if publication.lower().replace(" ", "") == "businessinsider" and path in BLOCKED_PATHS:
        return False, "business_insider_landing_page"
    return True, ""


def clean_article_text(text: str) -> str:
    value = html.unescape(text or "")
    value = re.sub(r"<\s*br\s*/?\s*>", "\n", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\b(?:br|gt|lt)&?;?>", " ", value, flags=re.I)
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def prepare_article_for_classification(text: str) -> str:
    """Remove obvious page debris before sending article text to the model."""
    cleaned = clean_article_text(text)
    parts = []
    seen = set()
    for block in re.split(r"[\r\n]+", cleaned):
        for part in re.split(r"(?<=[.!?])\s+", block.strip()):
            part = part.strip()
            if not part:
                continue
            lowered = part.lower()
            if any(marker in lowered for marker in CLASSIFIER_INPUT_DEBRIS):
                continue
            normalized = re.sub(r"\s+", " ", lowered)
            if normalized in seen:
                continue
            seen.add(normalized)
            parts.append(part)
    return " ".join(parts).strip()


def validate_article_content(text: str) -> tuple[bool, str]:
    cleaned = clean_article_text(text)
    lowered = cleaned.lower()
    if len(cleaned) < 300:
        return False, "short_content"
    if any(marker in lowered for marker in BLOCKED_CONTENT_MARKERS):
        return False, "boilerplate_content"
    if len(re.findall(r"[^.!?\n]{25,}[.!?]", cleaned)) < 3:
        return False, "insufficient_prose"
    if len(re.findall(r"\b\d+\s+min\s+read\b", lowered)) >= 2:
        return False, "article_index_content"
    return True, ""


def validate_title(title: str) -> tuple[bool, str]:
    cleaned = clean_article_text(title)
    if not cleaned or cleaned.lower().strip(" .:-") in GENERIC_TITLES:
        return False, "generic_title"
    if len(cleaned) < 12:
        return False, "short_title"
    return True, ""


def has_low_signal_intent(title: str) -> bool:
    lowered = clean_article_text(title).lower()
    return any(pattern in lowered for pattern in LOW_SIGNAL_TITLE_PATTERNS)


def keyword_matches(text: str, keywords) -> list[str]:
    """Return exact word/phrase matches without substring false positives."""
    normalized = re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()
    matched = []
    for keyword in keywords or []:
        normalized_keyword = re.sub(
            r"[^a-z0-9]+", " ", str(keyword or "").lower()
        ).strip()
        if not normalized_keyword:
            continue
        if re.search(rf"(?<![a-z0-9]){re.escape(normalized_keyword)}(?![a-z0-9])", normalized):
            matched.append(keyword)
    return matched


def relevance_gate_reason(
    score: float,
    matched_keywords,
    anchor_keywords,
    title: str,
    keyword_policy_map: dict,
    minimum_relevance_score: float,
    minimum_distinct_keywords: int,
    topic_entity_matches=None,
    supporting_concept_matches=None,
) -> str:
    normalize = lambda value: re.sub(r"\s+", " ", str(value or "").lower()).strip()
    distinct = {normalize(keyword) for keyword in (matched_keywords or []) if keyword}
    anchors = {normalize(keyword) for keyword in (anchor_keywords or []) if keyword}

    def tier(keyword):
        return keyword_policy_map.get(keyword, {}).get("tier", "weak")

    core = {keyword for keyword in distinct if tier(keyword) == "core"}
    supporting = {keyword for keyword in distinct if tier(keyword) == "supporting"}
    broad = {keyword for keyword in distinct if tier(keyword) in {"broad", "weak"}}
    anchor_core = core.intersection(anchors)
    anchor_supporting = supporting.intersection(anchors)
    anchor_broad = broad.intersection(anchors)
    entity_matches = set(topic_entity_matches or [])
    concept_matches = set(supporting_concept_matches or [])
    low_signal = has_low_signal_intent(title)

    if any(pattern in clean_article_text(title).lower() for pattern in NON_ARTICLE_TITLE_PATTERNS):
        return "low_signal_intent"
    # Curated/generated entities are specific retrieval signals. Final semantic
    # acceptance is still decided by the article-level classifier.
    if entity_matches:
        return ""
    if low_signal:
        return "low_signal_intent"
    if score < minimum_relevance_score:
        return "low_score"

    title_core = set(keyword_matches(title, core))
    standalone_title_core = {
        keyword for keyword in title_core
        if keyword_policy_map.get(keyword, {}).get("standalone_eligible") is True
    }
    if standalone_title_core:
        return ""
    if anchor_core and (concept_matches or anchor_supporting or len(anchor_core) >= 2):
        return ""
    if len(anchor_supporting) >= minimum_distinct_keywords and not low_signal:
        return ""
    if (
        concept_matches
        and (anchor_supporting or anchor_broad)
        and not low_signal
    ):
        return ""
    if not core:
        return "missing_core_keyword"
    if (
        len(anchor_supporting) >= minimum_distinct_keywords
        and not low_signal
    ):
        return ""
    return "missing_early_anchor"


def validate_author(author: str) -> str:
    value = clean_article_text(author).strip(" ,.-")
    lowered = value.lower()
    if lowered in INVALID_AUTHORS:
        return "Unknown"
    if len(value) > 100 or "@" in value or re.search(r"https?://", lowered):
        return "Unknown"
    if re.search(r"(?:\.[a-z]{2,}){1,}$", lowered):
        return "Unknown"
    if len(value.split()) > 8:
        return "Unknown"
    return value or "Unknown"


def _json_ld_objects(value):
    if isinstance(value, list):
        for item in value:
            yield from _json_ld_objects(item)
    elif isinstance(value, dict):
        yield value
        if "@graph" in value:
            yield from _json_ld_objects(value["@graph"])


def extract_page_metadata(html_text: str) -> dict:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html_text or "", "html.parser")
    metadata = {"published_date": None, "published_date_source": "", "canonical_url": "", "title": ""}
    canonical = soup.find("link", rel=lambda value: value and "canonical" in value)
    if canonical:
        metadata["canonical_url"] = str(canonical.get("href", "")).strip()
    og_title = soup.find("meta", property="og:title")
    if og_title:
        metadata["title"] = str(og_title.get("content", "")).strip()

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            raw = json.loads(script.string or script.get_text() or "{}")
        except Exception:
            continue
        for obj in _json_ld_objects(raw):
            article_type = str(obj.get("@type", "")).lower()
            if "article" not in article_type and "news" not in article_type:
                continue
            if not metadata["title"]:
                metadata["title"] = str(obj.get("headline", "")).strip()
            parsed = parse_date(obj.get("datePublished"))
            if parsed:
                metadata["published_date"] = parsed
                metadata["published_date_source"] = "json_ld"
                return metadata

    for attrs in (
        {"property": "article:published_time"}, {"name": "article:published_time"},
        {"name": "pub_date"}, {"name": "publish-date"},
    ):
        tag = soup.find("meta", attrs=attrs)
        parsed = parse_date(tag.get("content")) if tag else None
        if parsed:
            metadata["published_date"] = parsed
            metadata["published_date_source"] = "meta"
            break
    return metadata


def parse_date(value) -> Optional[datetime]:
    if isinstance(value, datetime):
        parsed = value
    elif value:
        try:
            parsed = date_parser.parse(str(value))
        except Exception:
            return None
    else:
        return None
    if parsed.tzinfo:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed.replace(tzinfo=None)


def resolve_published_date(page_metadata: dict, newspaper_date=None, rss_date=None, sitemap_date=None):
    choices = (
        (page_metadata.get("published_date"), page_metadata.get("published_date_source") or "page"),
        (newspaper_date, "newspaper"), (rss_date, "rss"), (sitemap_date, "sitemap"),
    )
    for value, source in choices:
        parsed = parse_date(value)
        if parsed:
            return parsed, source
    return None, "unavailable"


def within_lookback(value, lookback_days: int, now: Optional[datetime] = None) -> bool:
    parsed = parse_date(value)
    if not parsed:
        return False
    current = (now or datetime.now()).replace(tzinfo=None)
    return current - timedelta(days=max(1, lookback_days)) <= parsed <= current + timedelta(hours=6)


def prompt_overlap(summary: str, prompt: str) -> float:
    summary_tokens = set(re.findall(r"[a-z0-9]+", (summary or "").lower()))
    prompt_tokens = set(re.findall(r"[a-z0-9]+", (prompt or "").lower()))
    return len(summary_tokens & prompt_tokens) / max(1, len(prompt_tokens))


def repetition_ratio(text: str) -> float:
    sentences = [s.strip().lower() for s in re.split(r"[.!?]+", text or "") if len(s.split()) >= 2]
    if len(sentences) < 2:
        return 0.0
    unique = []
    repeated = 0
    for sentence in sentences:
        if any(SequenceMatcher(None, sentence, prior).ratio() >= 0.82 for prior in unique):
            repeated += 1
        else:
            unique.append(sentence)
    return repeated / len(sentences)


def validate_summary(summary: str, prompt: str = "") -> tuple[bool, str]:
    cleaned = clean_article_text(summary)
    if len(cleaned) < 80:
        return False, "short_summary"
    if any(marker in cleaned.lower() for marker in BLOCKED_CONTENT_MARKERS):
        return False, "summary_boilerplate"
    if prompt and prompt_overlap(cleaned, prompt) > 0.45:
        return False, "prompt_leak"
    if repetition_ratio(cleaned) > 0.30:
        return False, "repetitive_summary"
    if re.search(r"\bu00[0-9a-f]{2}\b|\ufffd", cleaned, flags=re.I):
        return False, "malformed_summary"
    return True, ""


def normalized_title(title: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (title or "").lower()))


def title_similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, normalized_title(left), normalized_title(right)).ratio()
