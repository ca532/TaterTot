import sys

from AgentSumm import ArticleSummarizer


def main():
    summarizer = ArticleSummarizer()
    text = (
        "Cartier launched a new high-jewelry collection in Paris. "
        "The event featured celebrity attendance and highlighted craftsmanship, "
        "rare diamonds, and bespoke design. Analysts noted strong luxury demand."
    )

    output = summarizer.summarize_article(
        article_content=text,
        article_url="https://example.com/article",
        publication="Example",
        title="Cartier launches new collection",
        author="Test Author",
    )

    if output is None or not output.summary:
        print("FAIL: summarizer returned empty output")
        sys.exit(1)

    print("OK: summarizer produced output")
    sys.exit(0)


if __name__ == "__main__":
    main()

