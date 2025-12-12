"""
Article Relevance Checker
Test individual article URLs to check their relevance scores
"""

from testCollector import CustomArticleCollector, ArticleCandidate
from newspaper import Article
from datetime import datetime
import sys

def check_article_relevance(url: str):
    """
    Check the relevance score of a single article
    """
    print("=" * 70)
    print("ARTICLE RELEVANCE CHECKER")
    print("=" * 70)
    print(f"\nChecking: {url}\n")
    
    # Initialize collector
    collector = CustomArticleCollector()
    
    try:
        # Download and parse article
        print("Step 1: Downloading article...")
        response = collector.make_request(url, timeout=20)
        
        if response.status_code != 200:
            print(f"❌ Failed to download: HTTP {response.status_code}")
            return
        
        article = Article(url)
        article.download_state = 2
        article.html = response.text
        article.parse()
        
        if not article.text or len(article.text) < 100:
            print("❌ Insufficient content extracted")
            return
        
        print("✅ Article downloaded successfully")
        print(f"   Content length: {len(article.text)} characters\n")
        
        # Extract title
        title = article.title or "No title"
        print(f"Title: {title}\n")
        
        # Step 2: Title relevance score
        print("Step 2: Checking title relevance...")
        title_score, title_keywords = collector.calculate_title_relevance_score(title, url)
        print(f"   Title Score: {title_score:.1f}")
        print(f"   Keywords in title: {', '.join(title_keywords) if title_keywords else 'None'}\n")
        
        # Step 3: Content relevance score
        print("Step 3: Checking full content relevance...")
        content_score, content_keywords = collector.calculate_relevance_score(title, article.text)
        print(f"   Content Score: {content_score:.1f}")
        print(f"   Keywords in content: {', '.join(content_keywords[:10])}{'...' if len(content_keywords) > 10 else ''}")
        print(f"   Total keywords found: {len(content_keywords)}\n")
        
        # Step 4: Luxury content validation
        print("Step 4: Validating luxury/jewelry relevance...")
        is_relevant = collector.is_luxury_relevant_content(title, article.text)
        print(f"   Passes luxury validation: {'✅ YES' if is_relevant else '❌ NO'}\n")
        
        # Step 5: Extract author
        print("Step 5: Extracting author...")
        author = collector.extract_author(article, article.text)
        print(f"   Author: {author}\n")
        
        # Final verdict
        print("=" * 70)
        print("FINAL VERDICT")
        print("=" * 70)
        
        threshold = 3.0
        
        if content_score > threshold and is_relevant:
            print(f"✅ WOULD BE COLLECTED")
            print(f"   Content score ({content_score:.1f}) > threshold ({threshold})")
            print(f"   Passes luxury validation: YES")
        elif content_score > threshold:
            print(f"⚠️  WOULD BE REJECTED (fails luxury validation)")
            print(f"   Content score ({content_score:.1f}) > threshold ({threshold})")
            print(f"   Passes luxury validation: NO")
        elif is_relevant:
            print(f"⚠️  WOULD BE REJECTED (score too low)")
            print(f"   Content score ({content_score:.1f}) <= threshold ({threshold})")
            print(f"   Passes luxury validation: YES")
        else:
            print(f"❌ WOULD BE REJECTED")
            print(f"   Content score ({content_score:.1f}) <= threshold ({threshold})")
            print(f"   Passes luxury validation: NO")
        
        print("\nArticle Details:")
        print(f"   Title: {title}")
        print(f"   Author: {author}")
        print(f"   URL: {url}")
        print(f"   Content Score: {content_score:.1f}")
        print(f"   Keywords: {', '.join(content_keywords[:15])}")
        print("=" * 70)
        
    except Exception as e:
        print(f"\n❌ Error checking article: {str(e)}")
        import traceback
        traceback.print_exc()


def interactive_mode():
    """
    Interactive mode - keep checking URLs
    """
    print("\n" + "=" * 70)
    print("INTERACTIVE ARTICLE RELEVANCE CHECKER")
    print("=" * 70)
    print("\nCheck multiple article URLs to see their relevance scores")
    print("Type 'quit' or 'exit' to stop\n")
    
    while True:
        url = input("\nEnter article URL (or 'quit' to exit): ").strip()
        
        if url.lower() in ['quit', 'exit', 'q']:
            print("\nGoodbye!")
            break
        
        if not url:
            continue
        
        if not url.startswith('http'):
            print("❌ Invalid URL - must start with http:// or https://")
            continue
        
        print()
        check_article_relevance(url)
        print()


def batch_mode(urls: list[str]):
    """
    Batch mode - check multiple URLs at once
    """
    print("\n" + "=" * 70)
    print(f"BATCH RELEVANCE CHECK - {len(urls)} URLs")
    print("=" * 70)
    
    results = []
    
    for idx, url in enumerate(urls, 1):
        print(f"\n[{idx}/{len(urls)}] Checking: {url}")
        try:
            collector = CustomArticleCollector()
            
            # Quick check
            response = collector.make_request(url, timeout=20)
            article = Article(url)
            article.download_state = 2
            article.html = response.text
            article.parse()
            
            if article.text and len(article.text) >= 100:
                score, keywords = collector.calculate_relevance_score(article.title or "", article.text)
                is_relevant = collector.is_luxury_relevant_content(article.title or "", article.text)
                author = collector.extract_author(article, article.text)
                
                results.append({
                    'url': url,
                    'title': article.title or "No title",
                    'author': author,
                    'score': score,
                    'keywords': len(keywords),
                    'passes': score > 3.0 and is_relevant
                })
                
                status = "✅ PASS" if score > 3.0 and is_relevant else "❌ FAIL"
                print(f"   {status} | Score: {score:.1f} | {article.title[:50]}...")
        except Exception as e:
            print(f"   ❌ ERROR: {str(e)[:60]}")
    
    # Summary
    print("\n" + "=" * 70)
    print("BATCH SUMMARY")
    print("=" * 70)
    
    passed = [r for r in results if r['passes']]
    failed = [r for r in results if not r['passes']]
    
    print(f"\nTotal checked: {len(results)}")
    print(f"Would be collected: {len(passed)}")
    print(f"Would be rejected: {len(failed)}\n")
    
    if passed:
        print("ARTICLES THAT WOULD BE COLLECTED:")
        for idx, article in enumerate(passed, 1):
            print(f"{idx}. [{article['score']:.1f}] {article['title'][:70]}...")
            print(f"   {article['author']} | {article['keywords']} keywords")
            print(f"   {article['url']}\n")


def main():
    """
    Main entry point
    """
    if len(sys.argv) > 1:
        # Command line mode
        if sys.argv[1] == '--batch':
            # Batch mode with multiple URLs
            urls = sys.argv[2:]
            if urls:
                batch_mode(urls)
            else:
                print("Usage: python ArticleRelevanceChecker.py --batch <url1> <url2> <url3>...")
        else:
            # Single URL mode
            check_article_relevance(sys.argv[1])
    else:
        # Interactive mode
        interactive_mode()


if __name__ == "__main__":
    main()