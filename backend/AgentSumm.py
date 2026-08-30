from newspaper import Article
from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import urlparse
import os
import re
import json
from bs4 import BeautifulSoup
import random

from article_quality import clean_article_text, validate_article_content, validate_author, validate_summary
from qwen_runtime import get_qwen_model


SUMMARY_MODEL_REPO = os.getenv(
    "SUMMARY_MODEL_REPO", "Qwen/Qwen2.5-3B-Instruct-GGUF"
).strip()
SUMMARY_MODEL_FILE = os.getenv(
    "SUMMARY_MODEL_FILE", "qwen2.5-3b-instruct-q4_k_m.gguf"
).strip()

SUMMARY_SYSTEM_PROMPT = """
You summarize articles for a luxury and fine-jewelry PR reading roundup.

Write one coherent paragraph of 80 to 140 words.

Rules:
- Use only facts present in the supplied article.
- Preserve the exact spelling of people, brands, collections, currencies,
  numbers, percentages, and magnitude words.
- Do not calculate or convert currencies.
- Do not use ellipses, square-bracket omissions, unfinished sentences,
  boilerplate, first-person language, or promotional calls to action.
- Explain the material luxury, jewelry, business, design, market, or PR value.
- Provide 1 to 5 concise source facts that directly support the summary.
- Do not invent evidence.

Return JSON only in this format:
{
  "summary": "One coherent paragraph.",
  "source_facts": ["A fact supported by the supplied article."]
}
""".strip()

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
    published_date: Optional[str] = None
    source_facts: List[str] = None

class ArticleSummarizer:
    def __init__(self, model: str = None, custom_prompt: str = None):
        """Configure a lazy local Qwen summarizer."""
        if model:
            print(
                f"Ignoring legacy summarizer model override {model!r}; "
                "using the configured Qwen GGUF runtime."
            )
        self.model_repo = SUMMARY_MODEL_REPO
        self.model_file = SUMMARY_MODEL_FILE
        self.model = None
        print(
            f"Configured summary model: {self.model_repo}/{self.model_file} "
            "(loaded lazily and shared with the classifier when identical)."
        )
        self.custom_prompt = (custom_prompt or os.getenv("SUMMARIZER_PROMPT", "")).strip()
        
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

    def _load(self):
        if self.model is None:
            self.model = get_qwen_model(self.model_repo, self.model_file)
        return self.model

    @staticmethod
    def _parse_json(text: str) -> dict:
        decoder = json.JSONDecoder()
        value = str(text or "")
        for index, character in enumerate(value):
            if character != "{":
                continue
            try:
                parsed, _ = decoder.raw_decode(value[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
        return {}

    @staticmethod
    def _source_facts_valid(source_facts, source_text: str) -> bool:
        if not isinstance(source_facts, list) or not 1 <= len(source_facts) <= 5:
            return False
        source_tokens = set(re.findall(r"[a-z0-9]+", source_text.lower()))
        for fact in source_facts:
            fact_tokens = set(re.findall(r"[a-z0-9]+", str(fact).lower()))
            meaningful = {token for token in fact_tokens if len(token) > 2}
            if not meaningful:
                return False
            if len(meaningful & source_tokens) / len(meaningful) < 0.55:
                return False
        return True

    def _build_extractive_summary(self, input_text: str) -> str:
        """Recover a grounded summary when model generation remains invalid."""
        sentences = [
            sentence.strip()
            for sentence in re.split(r"(?<=[.!?])\s+", clean_article_text(input_text))
            if len(sentence.strip().split()) >= 8
            and sentence.strip().endswith((".", "!", "?"))
        ]
        selected = []
        total_words = 0
        debris = (
            "click here", "subscribe", "sign up", "read more",
            "related articles", "all rights reserved", "advertisement",
        )
        for sentence in sentences:
            if any(marker in sentence.lower() for marker in debris):
                continue
            sentence_words = len(sentence.split())
            # Skip sentences that cannot fit instead of allowing one long
            # sentence to push the fallback beyond the validation ceiling.
            if sentence_words > 140 or total_words + sentence_words > 140:
                continue
            selected.append(sentence)
            total_words += sentence_words
            if total_words >= 80:
                break
        return clean_article_text(" ".join(selected))

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
            input_text = clean_article_text(article_content)[:8000]
            content_valid, content_reason = validate_article_content(input_text)
            if not content_valid:
                print(f"Skipping invalid summary input ({content_reason}): {article_url}")
                return None

            summary_text = ""
            source_facts = []
            reason = "no_summary"
            previous_summary = ""

            for attempt in range(1, 3):
                try:
                    result = self._generate_qwen_summary(
                        input_text=input_text,
                        title=title,
                        publication=publication,
                        previous_summary=previous_summary,
                        repair_reason=reason if attempt > 1 else "",
                    )
                    summary_text = clean_article_text(result.get("summary", ""))
                    source_facts = [
                        clean_article_text(str(fact))
                        for fact in result.get("source_facts", [])
                        if clean_article_text(str(fact))
                    ] if isinstance(result.get("source_facts"), list) else []

                    valid, reason = validate_summary(
                        summary_text,
                        prompt=self.custom_prompt,
                        source_text=input_text,
                    )
                    if valid and not self._source_facts_valid(source_facts, input_text):
                        valid, reason = False, "ungrounded_source_facts"
                    if valid:
                        break
                    previous_summary = summary_text
                    print(
                        f"Retrying Qwen summary after quality failure "
                        f"({reason}): {article_url}"
                    )
                except Exception as exc:
                    valid, reason = False, f"qwen_error:{str(exc)[:120]}"
                    print(
                        f"Retrying Qwen summary after generation failure "
                        f"(attempt {attempt}/2): {article_url}: {str(exc)[:160]}"
                    )

            if not valid:
                print(
                    f"Using extractive fallback after summary failure "
                    f"({reason}): {article_url}"
                )
                summary_text = self._build_extractive_summary(input_text)
                source_facts = [summary_text]
                valid, reason = validate_summary(
                    summary_text,
                    prompt=self.custom_prompt,
                    source_text=input_text,
                )

            if not valid:
                print(
                    f"Skipping article after all summary attempts failed "
                    f"({reason}): {article_url}"
                )
                return None

            return ArticleSummary(
                title=title,
                author=validate_author(author),
                summary=clean_article_text(summary_text),
                url=article_url,
                publication=publication,
                topics=[],
                source_facts=source_facts,
            )

        except Exception as e:
            print(f"Error summarizing article {article_url}: {e}")
            return None

    def _generate_qwen_summary(
        self,
        input_text: str,
        title: str,
        publication: str,
        previous_summary: str = "",
        repair_reason: str = "",
    ) -> dict:
        custom_instructions = self.custom_prompt.replace(
            "{article}", "[article supplied separately]"
        )
        payload = {
            "title": str(title or "").strip(),
            "publication": str(publication or "").strip(),
            "article": input_text,
            "editorial_instructions": custom_instructions,
        }
        if repair_reason:
            payload["repair"] = {
                "previous_summary": previous_summary,
                "failure_reason": repair_reason,
                "instruction": (
                    "Regenerate from the supplied article and correct the failure. "
                    "Do not repeat unsupported or malformed wording."
                ),
            }

        response = self._load().create_chat_completion(
            messages=[
                {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            temperature=0.0,
            max_tokens=550,
            response_format={"type": "json_object"},
        )
        content = response["choices"][0]["message"]["content"]
        result = self._parse_json(content)
        if not result:
            raise ValueError("Qwen returned malformed summary JSON")
        return result

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
                                validated = validate_author(author)
                                if validated != "Unknown":
                                    return validated
                elif isinstance(data, dict) and "author" in data:
                    author = _get_author_from_jsonld(data["author"])
                    if author:
                        validated = validate_author(author)
                        if validated != "Unknown":
                            return validated
            except Exception:
                continue
    except Exception:
        pass

    # 2. Fallback: use newspaper3k's authors field
    if article.authors:
        validated = validate_author(article.authors[0])
        if validated != "Unknown":
            return validated

    # 3. Regex scan of title, meta description, and body text
    combined_text = " ".join([
        article.title or "",
        getattr(article, "meta_description", "") or "",
        article.text or ""
    ])

    match = re.search(r"\b[Bb]y\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)", combined_text)
    if match:
        return validate_author(match.group(1))

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
    print("Luxury-Focused Article Summarizer (Qwen GGUF)")
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
