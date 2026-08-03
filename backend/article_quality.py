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
GENERIC_TITLES = {
    "analysis", "advertising", "ai", "a smarter way", "above and beyond",
    "featured articles", "jewels club", "home", "latest news",
}
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
    sentences = [s.strip().lower() for s in re.split(r"[.!?]+", text or "") if len(s.split()) >= 4]
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
