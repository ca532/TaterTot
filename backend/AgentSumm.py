from newspaper import Article
from transformers import pipeline
from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import urlparse
import os
import re
import json
from bs4 import BeautifulSoup
import random

# Try to import cloudscraper for CloudFlare bypass
try:
    import cloudscraper
    CLOUDSCRAPER_AVAILABLE = True
except ImportError:
    CLOUDSCRAPER_AVAILABLE = False
    print("Note: Install cloudscraper for better anti-blocking: pip install cloudscraper")

@dataclass
class ArticleSummary:
    title: str
    author: str
    summary: str
    url: str
    publication: str
    topics: List[str] = None

class ArticleSummarizer:
    def __init__(self, model: str = None):
        """Initialize summarizer model from env, defaulting to BART."""
        resolved_model = model or os.getenv("SUMMARIZER_MODEL", "facebook/bart-large-cnn")
        print(f"Loading model: {resolved_model} ... this may take a moment.")
        self.summarizer = pipeline("summarization", model=resolved_model)
        
        # Setup CloudScraper if available
        if CLOUDSCRAPER_AVAILABLE:
            self.scraper = cloudscraper.create_scraper(
                browser={
                    'browser': 'chrome',
                    'platform': 'windows',
                    'mobile': False
                }
            )
            print("CloudScraper enabled for anti-blocking")
        else:
            import requests
            self.scraper = requests.Session()
        
        # User-Agent rotation
        self.user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0'
        ]
    
    def get_random_user_agent(self):
        """Get a random User-Agent"""
        return random.choice(self.user_agents)

    def summarize_article(
        self,
        article_content: str,
        article_url: str,
        publication: str,
        title: str,
        author: str
    ) -> Optional[ArticleSummary]:
        """Summarize an article focusing on luxury brands, jewelry pieces, and celebrities"""
        try:
            # BART-CNN doesn't use prompts - just give it the article content
            # Adding a prompt causes hallucinations!
            
            # Just use the article content directly
            input_text = article_content[:4000]  # Use more context
            
            summary_text = self.summarizer(
                input_text,
                max_length=300,      # Longer summaries for more detail
                min_length=120,      # Ensure substantial detail
                do_sample=False,     # Deterministic output
                truncation=True,
                num_beams=6,         # Higher beam search for quality
                length_penalty=1.0,  # No penalty for length
                early_stopping=True
            )[0]["summary_text"]

            return ArticleSummary(
                title=title,
                author=author if author else "Unknown",
                summary=summary_text.strip(),
                url=article_url,
                publication=publication,
                topics=[]
            )

        except Exception as e:
            print(f"Error summarizing article {article_url}: {e}")
            return None

def extract_publication_name(url: str) -> str:
    domain = urlparse(url).netloc.replace("www.", "").split(".")[0]
    return domain.title()


def extract_author(article: Article, text: str) -> str:
    """Extract author name using JSON-LD, meta tags, or regex scanning."""
    author = None

    # 1. Try JSON-LD parsing
    try:
        soup = BeautifulSoup(article.html, "html.parser")
        scripts = soup.find_all("script", type="application/ld+json")
        for script in scripts:
            try:
                data = json.loads(script.string)
                if isinstance(data, list):
                    for entry in data:
                        if isinstance(entry, dict) and "author" in entry:
                            author = _get_author_from_jsonld(entry["author"])
                            if author:
                                return author
                elif isinstance(data, dict) and "author" in data:
                    author = _get_author_from_jsonld(data["author"])
                    if author:
                        return author
            except Exception:
                continue
    except Exception:
        pass

    # 2. Fallback: use newspaper3k's authors field
    if article.authors:
        return article.authors[0]

    # 3. Regex scan of title, meta description, and body text
    combined_text = " ".join([
        article.title or "",
        getattr(article, "meta_description", "") or "",
        article.text or ""
    ])

    match = re.search(r"\b[Bb]y\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)", combined_text)
    if match:
        return match.group(1)

    return "Unknown"


def _get_author_from_jsonld(author_field):
    """Helper to safely parse author name(s) from JSON-LD structures."""
    if isinstance(author_field, dict) and "name" in author_field:
        return author_field["name"]
    elif isinstance(author_field, list):
        for entry in author_field:
            if isinstance(entry, dict) and "name" in entry:
                return entry["name"]
    return None


def main():
    print("Luxury-Focused Article Summarizer (BART CNN)")
    print("=" * 50)

    # Initialize summarizer
    summarizer = ArticleSummarizer()
    
    url = input("\nEnter article URL: ").strip()
    if not url:
        print("URL required!")
        return

    # Extract article using CloudScraper
    try:
        print("Extracting article content...")
        
        # Download HTML using CloudScraper (bypasses blocks)
        headers = {
            'User-Agent': summarizer.get_random_user_agent(),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9'
        }
        
        response = summarizer.scraper.get(url, headers=headers, timeout=20)
        
        if response.status_code != 200:
            print(f"Error: HTTP {response.status_code}")
            return
        
        # Use newspaper to parse the HTML
        article = Article(url)
        article.download_state = 2  # Mark as downloaded
        article.html = response.text  # Feed CloudScraper HTML
        article.parse()
        
        if not article.text or len(article.text) < 100:
            print("Error: Insufficient content extracted")
            return
            
    except Exception as e:
        print(f"Error extracting article: {e}")
        return

    # Extract author
    author = extract_author(article, article.text)

    # Summarize
    summary = summarizer.summarize_article(
        article.text,
        url,
        extract_publication_name(url),
        article.title,
        author
    )

    if not summary:
        print("No summary generated.")
        return

    # Output
    print("\nSUMMARY RESULT:")
    print("=" * 50)
    print(f"Title       : {summary.title}")
    print(f"Author      : {summary.author}")
    print(f"Summary     : {summary.summary}")
    print(f"Link        : {summary.url}")
    print(f"Publication : {summary.publication}")
    
    # Formatted output for integration
    print(f"\nFormatted Output:")
    print(f"* [{summary.title}]({summary.url}) by {summary.author} - {summary.summary}")


if __name__ == "__main__":
    main()
