# discover_sources.py
# pip install requests beautifulsoup4 feedparser lxml

import re
import requests
import feedparser
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin

HEADERS = {"User-Agent": "Mozilla/5.0 (SourceDiscovery/1.0)"}

COMMON_FEED_PATHS = [
    "/feed", "/rss", "/rss.xml", "/atom.xml",
    "/feeds/posts/default", "/blog/feed", "/news/feed"
]

COMMON_SITEMAP_PATHS = [
    "/sitemap.xml", "/sitemap_index.xml", "/news-sitemap.xml",
    "/sitemap_google_news.xml"
]


TARGETS = [
    # ("Display Name", "base url or domain")
    ("FNLondon", "https://www.fnlondon.com/"), ("Institutional Investor", "https://www.institutionalinvestor.com/"), ("Euromoney", "https://www.euromoney.com/article/bco8ahivmbs4s4cg4gcsw0g8s/sponsored-content/national-bank-of-greece-e6-billion-in-structured-financing-deal-flow-drives-european-expansion/"), ("FX Markets", "https://www.fx-markets.com/"),("CNBC", "https://www.cnbc.com/"), ("Monocle", "https://monocle.com/"), ("BBC", "https://www.bbc.com/"), ("Yahoo Finance", "https://finance.yahoo.com/"), ("This is Money", "https://www.thisismoney.co.uk/money/index.html"), ("Money Week", "https://moneyweek.com/"), ("Banking Technology Magazine", "https://www.fintechfutures.com/publications/banking-technology-magazine"), ("City AM", "https://www.cityam.com/")
    # ("New York Times", "nytimes.com"),
]


def normalize_base(url: str) -> str:
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}/"


def is_valid_feed(url: str, timeout=12):
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        if r.status_code != 200:
            return False
        parsed = feedparser.parse(r.text)
        return (not parsed.bozo) and len(parsed.entries) > 0
    except Exception:
        return False


def is_valid_sitemap(url: str, timeout=12):
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        if r.status_code != 200:
            return False
        ct = (r.headers.get("content-type") or "").lower()
        text_head = r.text[:3000].lower()
        return (
            "xml" in ct
            or "<urlset" in text_head
            or "<sitemapindex" in text_head
        )
    except Exception:
        return False


def discover_feeds(base_url: str):
    candidates = set(urljoin(base_url, p) for p in COMMON_FEED_PATHS)

    # discover from homepage <link rel=alternate ...>
    try:
        r = requests.get(base_url, headers=HEADERS, timeout=12)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            for link in soup.find_all("link", href=True):
                rel = " ".join(link.get("rel", [])).lower()
                typ = (link.get("type") or "").lower()
                if "alternate" in rel and ("rss+xml" in typ or "atom+xml" in typ):
                    candidates.add(urljoin(base_url, link["href"]))
            # regex fallback
            for m in re.findall(r'https?://[^"\']+(?:rss|feed|atom)[^"\']*', r.text, re.I):
                candidates.add(m)
    except Exception:
        pass

    valid = []
    for c in sorted(candidates):
        if is_valid_feed(c):
            valid.append(c)
    return valid


def discover_sitemap(base_url: str):
    # 1) robots.txt first
    robots_url = urljoin(base_url, "/robots.txt")
    try:
        r = requests.get(robots_url, headers=HEADERS, timeout=12)
        if r.status_code == 200:
            lines = r.text.splitlines()
            robot_maps = []
            for line in lines:
                if line.lower().startswith("sitemap:"):
                    sm = line.split(":", 1)[1].strip()
                    robot_maps.append(sm)

            # prefer news-like sitemap if available
            robot_maps_sorted = sorted(
                robot_maps,
                key=lambda x: ("news" not in x.lower(), len(x))
            )
            for sm in robot_maps_sorted:
                if is_valid_sitemap(sm):
                    return sm
    except Exception:
        pass

    # 2) common fallback paths
    for p in COMMON_SITEMAP_PATHS:
        candidate = urljoin(base_url, p)
        if is_valid_sitemap(candidate):
            return candidate

    return None


def to_python_block(name: str, base_url: str, feeds: list, sitemap_url: str | None):
    esc_name = name.replace("\\", "\\\\").replace("'", "\\'")
    feeds_repr = "[" + ", ".join([f"'{f}'" for f in feeds]) + "]"
    sitemap_repr = f"'{sitemap_url}'" if sitemap_url else "None"

    return (
        f"'{esc_name}': {{\n"
        f"                'base_url': '{base_url}',\n"
        f"                'rss_feeds': {feeds_repr},\n"
        f"                'sitemap_url': {sitemap_repr}\n"
        f"            }},"
    )


def main():
    for name, raw_url in TARGETS:
        base = normalize_base(raw_url)
        feeds = discover_feeds(base)
        sitemap = discover_sitemap(base)
        print(to_python_block(name, base, feeds, sitemap))
        print()


if __name__ == "__main__":
    main()