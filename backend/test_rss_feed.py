import sys
from datetime import datetime, timezone
from urllib.parse import urlparse

import feedparser
import requests


def fetch_feed(url: str, timeout: int = 15):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
        "Accept-Language": "en-US,en;q=0.9",
    }
    resp = requests.get(url, headers=headers, timeout=timeout)
    return resp


def main():
    if len(sys.argv) < 2:
        print("Usage: python test_rss_feed.py <feed_url>")
        sys.exit(1)

    feed_url = sys.argv[1].strip()
    print(f"Testing RSS feed: {feed_url}")
    print(f"Domain: {urlparse(feed_url).netloc}")

    try:
        resp = fetch_feed(feed_url)
        print(f"HTTP status: {resp.status_code}")
        print(f"Content-Type: {resp.headers.get('Content-Type', 'N/A')}")

        if resp.status_code != 200:
            print("FAIL: Non-200 response")
            return

        parsed = feedparser.parse(resp.content)
        entries = getattr(parsed, "entries", [])
        print(f"Entries parsed: {len(entries)}")

        if not entries:
            print("FAIL: No entries parsed")
            return

        now = datetime.now(timezone.utc)
        recent_count = 0

        for i, e in enumerate(entries[:10], 1):
            title = e.get("title", "").strip()
            link = e.get("link", "").strip()

            pub = None
            if getattr(e, "published_parsed", None):
                pub = datetime(*e.published_parsed[:6], tzinfo=timezone.utc)
                if (now - pub).days <= 7:
                    recent_count += 1

            print(f"\n[{i}] {title or '(no title)'}")
            print(f"    link: {link or '(no link)'}")
            print(f"    published: {pub.isoformat() if pub else 'N/A'}")

        print("\nResult:")
        print(f"- Parsed entries: {len(entries)}")
        print(f"- Recent entries (<=7 days): {recent_count}")
        print("PASS" if len(entries) > 0 else "FAIL")

    except Exception as e:
        print(f"ERROR: {e}")


if __name__ == "__main__":
    main()
