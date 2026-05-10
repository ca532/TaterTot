import argparse
from urllib.parse import urlparse

from AgentCollector import CustomArticleCollector


def main():
    parser = argparse.ArgumentParser(description="Debug a sitemap response without running the full pipeline.")
    parser.add_argument(
        "--url",
        default="https://www.thetimes.com/sitemaps/news",
        help="Sitemap URL to test",
    )
    parser.add_argument(
        "--topic",
        default="finance",
        choices=["finance", "luxury"],
        help="Collector topic mode",
    )
    parser.add_argument(
        "--preview-chars",
        type=int,
        default=300,
        help="How many body characters to print for preview",
    )
    args = parser.parse_args()

    collector = CustomArticleCollector(topic=args.topic)
    url = args.url.strip()

    print(f"Testing sitemap URL: {url}")
    print(f"Host: {urlparse(url).netloc}")
    print("-" * 80)

    try:
        response = collector.make_request(url, timeout=20)
    except Exception as exc:
        print(f"Request failed: {exc}")
        return

    content_type = response.headers.get("Content-Type", "")
    content_encoding = response.headers.get("Content-Encoding", "")
    server = response.headers.get("Server", "")

    print(f"Status       : {response.status_code}")
    print(f"Content-Type : {content_type}")
    print(f"Encoding     : {content_encoding or '(none)'}")
    print(f"Server       : {server or '(unknown)'}")
    print(f"Bytes        : {len(response.content)}")

    text_preview = response.text[: args.preview_chars].replace("\n", "\\n")
    print(f"Body preview : {text_preview}")

    looks_html = "<html" in response.text[:1000].lower() or "<!doctype html" in response.text[:1000].lower()
    print(f"Looks like HTML: {looks_html}")

    print("-" * 80)
    print("Parser check:")

    try:
        root = collector._parse_xml_with_cleanup(response.content)
        print(f"Direct parse: OK ({root.tag})")
    except Exception as exc:
        print(f"Direct parse: FAIL ({exc})")
        root = None

    if root is None:
        try:
            import gzip

            decompressed = gzip.decompress(response.content)
            root = collector._parse_xml_with_cleanup(decompressed)
            print(f"Gzip fallback parse: OK ({root.tag})")
        except Exception as exc:
            print(f"Gzip fallback parse: FAIL ({exc})")

    if root is not None:
        ns = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
        locs = root.findall(f".//{ns}loc")
        print(f"<loc> entries found: {len(locs)}")
        for i, loc in enumerate(locs[:5], start=1):
            print(f"{i}. {(loc.text or '').strip()}")


if __name__ == "__main__":
    main()

