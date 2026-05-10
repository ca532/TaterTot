import sys

from AgentCollector import CustomArticleCollector


def main():
    collector = CustomArticleCollector()
    sources = list(collector.target_sources.keys())[:2]
    articles = collector.collect_top_3_per_publication(sources_subset=sources)

    if not isinstance(articles, list):
        print("FAIL: collector did not return a list")
        sys.exit(1)

    print(f"OK: collector returned {len(articles)} articles from {len(sources)} source(s)")
    sys.exit(0)


if __name__ == "__main__":
    main()

