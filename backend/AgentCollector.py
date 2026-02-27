import feedparser
import requests
from newspaper import Article
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import List, Dict, Optional
import re
from urllib.parse import urlparse, urljoin
import time
import json
from bs4 import BeautifulSoup
import xml.etree.ElementTree as ET
import random

# Import extract_author from AgentSumm
try:
    from AgentSumm import extract_author
    AGENTSUMM_AVAILABLE = True
except ImportError:
    AGENTSUMM_AVAILABLE = False
    print("⚠️  Warning: Could not import extract_author from AgentSumm")
    print("   Author extraction will use fallback method")

# Try to import curl-cffi (most powerful anti-blocking)
try:
    from curl_cffi import requests as curl_requests
    CURL_CFFI_AVAILABLE = True
except ImportError:
    CURL_CFFI_AVAILABLE = False
    print("Note: Install curl-cffi for best anti-blocking: pip install curl-cffi")

# Try to import cloudscraper (fallback)
try:
    import cloudscraper
    CLOUDSCRAPER_AVAILABLE = True
except ImportError:
    CLOUDSCRAPER_AVAILABLE = False
    print("Note: Install cloudscraper for better anti-blocking: pip install cloudscraper")

@dataclass
class ArticleCandidate:
    title: str
    url: str
    publication: str
    published_date: datetime
    summary: str
    author: str = "Unknown"
    relevance_score: float = 0.0
    keywords_found: List[str] = None
    full_content: str = ""

class CustomArticleCollector:
    def __init__(self):
        """Initialize collector with your specific sources and keywords"""
        
        # Your custom keywords for relevance filtering (British English)
        self.luxury_keywords = [
            'luxury', 'jewellery', 'fine jewellery', 'craftsmanship',
            'jewelry', 'diamond', 'engagement ring', 'wedding ring',
            'fashion', 'accessories', 'watches', 'timepiece',
            'necklace', 'bracelet', 'earrings', 'pendant', 'brooch',
            'gold', 'platinum', 'silver', 'emerald', 'sapphire', 'ruby',
            'cartier', 'tiffany', 'bulgari', 'chanel', 'dior', 'van cleef',
            'graff', 'harry winston', 'chopard', 'piaget', 'boucheron',
            'red carpet', 'celebrity', 'haute couture', 'collection',
            'launch', 'collaboration', 'limited edition', 'auction',
            'investment', 'trends', 'style', 'fashion week', 'royal', 'royals',
            'Luxury sector', 'Luxury marketing trends', 'Lab grown diamonds',
            'Diamond price', 'Gold price', 'jewels',
            # English royalty keywords
            'crown', 'tiara', 'coronation', 'queen', 'king', 'prince', 'princess',
            'duchess', 'duke', 'royal family', 'buckingham palace', 'windsor',
            'crown jewels', 'state visit', 'royal wedding', 'monarchy',
            'sovereign', 'regalia', 'royal collection', 'palace'
        ]
        
        self.active_keywords = list(self.luxury_keywords)

        # Your specific publication sources - MULTIPLE RSS FEEDS SUPPORTED
        self.target_sources = {
            'The Guardian': {
                'base_url': 'https://www.theguardian.com/fashion/womens-jewellery',
                'rss_feeds': [
                    'https://www.theguardian.com/fashion/womens-jewellery/rss',
                    'https://www.theguardian.com/uk/rss'
                ],
                'sitemap_url': 'https://www.theguardian.com/sitemaps/news.xml'
            },
            'The Telegraph': {
                'base_url': 'https://www.telegraph.co.uk/luxury/',
                'rss_feeds': [
                    'https://www.telegraph.co.uk/luxury/rss'
                ],
                'sitemap_url': 'https://www.telegraph.co.uk/luxury/sitemap.xml'
            },
            'Evening Standard': {
                'base_url': 'https://www.standard.co.uk/topic/jewellery',
                'rss_feeds': [
                    'https://www.standard.co.uk/rss'
                ],
                'sitemap_url': 'https://www.standard.co.uk/sitemaps/googlenews'
            },
            'The Times': {
                'base_url': 'https://www.thetimes.com/life-style/luxury',
                'rss_feeds': [],
                'sitemap_url': 'https://www.thetimes.com/sitemaps/news'
            },
            'Financial Times': {
                'base_url': 'https://www.ft.com/fashion',
                'rss_feeds': [],
                'sitemap_url': 'https://www.ft.com/sitemaps/news.xml'
            },
            'Forbes': {
                'base_url': 'https://www.forbes.com/business/',
                'rss_feeds': [
                    'https://www.forbes.com/business/feed/'
                ],
                'sitemap_url': 'https://www.forbes.com/news_sitemap.xml'
            },
            'Business of Fashion': {
                'base_url': 'https://www.businessoffashion.com/',
                'rss_feeds': [
                    'https://www.businessoffashion.com/feed/'
                ],
                'sitemap_url': 'https://www.businessoffashion.com/arc/outboundfeeds/sitemap/google-news/'
            },
            'Vogue Business': {
                'base_url': 'https://www.voguebusiness.com/',
                'rss_feeds': [
                    'https://www.voguebusiness.com/feed'
                ],
                'sitemap_url': 'https://www.vogue.com/feed/google-latest-news/sitemap-google-news'
            },
            'Harper\'s Bazaar': {
                'base_url': 'https://www.harpersbazaar.com/',
                'rss_feeds': [],
                'sitemap_url': 'https://www.harpersbazaar.com/sitemap_google_news.xml'
            },
            'Elle': {
                'base_url': 'https://www.elle.com/jewelry/',
                'rss_feeds': [],
                'sitemap_url': 'https://www.elle.com/sitemap_google_news.xml'
            },
            'Vogue UK': {
                'base_url': 'https://www.vogue.co.uk/',
                'rss_feeds': [
                    'https://www.vogue.co.uk/feed/rss'
                ],
                'sitemap_url': 'https://www.vogue.co.uk/feed/sitemap/sitemap-google-news'
            },
            'Vanity Fair': {
                'base_url': 'https://www.vanityfair.com/',
                'rss_feeds': [
                    'https://www.vanityfair.com/feed/rss'
                ],
                'sitemap_url': 'https://www.vanityfair.com/feed/google-latest-news/sitemap-google-news'
            },
            'Tatler': {
                'base_url': 'https://www.tatler.com/',
                'rss_feeds': ['https://www.tatler.com/feed/rss'],
                'sitemap_url': 'https://www.tatler.com/feed/google-latest-news/sitemap-google-news'
            },
            'Red Online': {
                'base_url': 'https://www.redonline.co.uk/',
                'rss_feeds': [],
                'sitemap_url': 'https://www.redonline.co.uk/sitemap_google_news.xml'
            },
            'Town & Country': {
                'base_url': 'https://www.townandcountrymag.com/style/',
                'rss_feeds': [
                    'https://www.townandcountrymag.com/rss/all.xml/'
                ],
                'sitemap_url': 'https://www.townandcountrymag.com/sitemap_google_news.xml'
            },
            'StyleCaster': {
                'base_url': 'https://stylecaster.com/c/fashion/',
                'rss_feeds': [
                    'https://stylecaster.com/feed/'
                ],
                'sitemap_url': 'https://stylecaster.com/news-sitemap.xml'
            },
            'The Handbook': {
                'base_url': 'https://www.thehandbook.com/',
                'rss_feeds': [],
                'sitemap_url': 'https://www.thehandbook.com/sitemap.xml?postType=editorial&offset=0'
            },
            'Something About Rocks': {
                'base_url': 'https://somethingaboutrocks.com/',
                'rss_feeds': [
                    'https://somethingaboutrocks.com/feed/'
                ],
                'sitemap_url': None
            },
            'The Cut': {
                'base_url': 'https://www.thecut.com/',
                'rss_feeds': [
                    'https://www.thecut.com/rss/index.xml'
                ],
                'sitemap_url': 'https://www.thecut.com/sitemaps/sitemap-2025.xml'
            },
            'The Monocle': {
                'base_url': 'https://monocle.com/',
                'rss_feeds': [],
                'sitemap_url': 'https://monocle.com/the-monocle-minute/'
            },
            'The Jewels Club': {
                'base_url': 'https://thejewels.club/',
                'rss_feeds': [],
                'sitemap_url': 'https://thejewels.club/sitemap.xml'
            },
            'Retail Jeweller': {
                'base_url': 'https://www.retail-jeweller.com/',
                'rss_feeds': [
                    'https://www.retail-jeweller.com/feed/'
                ],
                'sitemap_url': None
            },
            'Professional Jeweller': {
                'base_url': 'https://www.professionaljeweller.com/',
                'rss_feeds': ['https://www.professionaljeweller.com/feed/'],
                'sitemap_url': None
            },
            'Rapaport': {
                'base_url': 'https://rapaport.com/',
                'rss_feeds': [
                    'https://rapaport.com/rss/'
                ],
                'sitemap_url': None
            },
            'National Jeweler': {
                'base_url': 'https://nationaljeweler.com/',
                'rss_feeds': [],
                'sitemap_url': 'https://nationaljeweler.com/sitemap.xml'
            },
            'Wall Street Journal': {
                'base_url': 'https://www.wsj.com/news/life-arts/fashion',
                'rss_feeds': [
                    'https://feeds.content.downjones.io/public/rss/RSSWorldNews',
                    'https://feeds.content.downjones.io/public/rss/RSSLifestyle',
                    'https://feeds.content.downjones.io/public/rss/RSSArtsCulture',
                    'https://feeds.content.downjones.io/public/rss/RSSStyle'
                ],
                'sitemap_url': 'https://www.wsj.com/wsjsitemaps/wsj_google_news.xml'
            },
            'New York Times': {
                'base_url': 'https://www.nytimes.com/',
                'rss_feeds': [
                    'https://rss.nytimes.com/services/xml/rss/nyt/World.xml',
                    'https://rss.nytimes.com/services/xml/rss/nyt/Arts.xml',
                    'https://rss.nytimes.com/services/xml/rss/nyt/FashionandStyle.xml'
                ],
                'sitemap_url': 'https://www.nytimes.com/sitemaps/new/news.xml.gz'
            },
            'Business Insider': {
                'base_url': 'https://www.businessinsider.com/',
                'rss_feeds': [],
                'sitemap_url': 'https://www.businessinsider.com/sitemap/google-news.xml'
            }
        }
        
        # Initialize scraper with priority order
        if CURL_CFFI_AVAILABLE:
            self.scraper = curl_requests.Session()
            self.scraper_type = 'curl-cffi'
            print("✅ curl-cffi enabled (most powerful anti-blocking)")
            print("   Can bypass CloudFlare, SSL checks, and bot detection\n")
        elif CLOUDSCRAPER_AVAILABLE:
            self.scraper = cloudscraper.create_scraper(
                browser={
                    'browser': 'chrome',
                    'platform': 'windows',
                    'mobile': False
                }
            )
            self.scraper_type = 'cloudscraper'
            print("✅ CloudScraper enabled for anti-blocking\n")
        else:
            self.scraper = requests.Session()
            self.scraper_type = 'requests'
            print("⚠️  Using basic requests (limited anti-blocking)\n")
        
        # User-Agent rotation
        self.user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15',
            'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36 OPR/107.0.0.0'
        ]
        
        # Rate limiting
        self.request_count = 0
        self.last_request_time = time.time()
        self.min_delay_between_requests = 2.0
        self.max_delay_between_requests = 5.0
        self.requests_per_source = 0
        self.max_requests_per_minute = 20
    
    def set_keywords_override(self, keywords: List[str]) -> None:
        self.active_keywords = keywords if keywords else list(self.luxury_keywords)

    def get_random_user_agent(self):
        return random.choice(self.user_agents)
    
    def apply_rate_limit(self):
        current_time = time.time()
        time_since_last = current_time - self.last_request_time
        
        if time_since_last < self.min_delay_between_requests:
            sleep_time = self.min_delay_between_requests - time_since_last
            time.sleep(sleep_time)
        
        random_delay = random.uniform(0, self.max_delay_between_requests - self.min_delay_between_requests)
        time.sleep(random_delay)
        
        self.last_request_time = time.time()
        self.request_count += 1
        
        if self.request_count % self.max_requests_per_minute == 0:
            print(f"  Rate limit: Processed {self.request_count} requests, brief pause...")
            time.sleep(random.uniform(5, 10))
    
    def make_request(self, url: str, timeout: int = 10):
        """Make HTTP request with curl-cffi for better anti-blocking"""
        self.apply_rate_limit()
        
        headers = {
            'User-Agent': self.get_random_user_agent(),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Referer': 'https://www.google.com/',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Cache-Control': 'max-age=0'
        }
        
        try:
            if self.scraper_type == 'curl-cffi':
                response = self.scraper.get(
                    url,
                    headers=headers,
                    timeout=timeout,
                    impersonate="chrome110",
                    verify=True
                )
            else:
                response = self.scraper.get(url, headers=headers, timeout=timeout)
            
            if response.status_code != 200:
                domain = urlparse(url).netloc
                if 'telegraph' in domain.lower():
                    print(f"    HTTP {response.status_code} - Telegraph blocking detected")
                else:
                    print(f"    HTTP {response.status_code} error for {url}")
            
            return response
            
        except Exception as e:
            error_msg = str(e)
            
            if 'SSL' in error_msg or 'ssl' in error_msg.lower():
                print(f"    SSL Error: {urlparse(url).netloc} is blocking with SSL handshake")
                
                if self.scraper_type == 'curl-cffi':
                    try:
                        print(f"    Retrying without SSL verification...")
                        response = self.scraper.get(
                            url,
                            headers=headers,
                            timeout=timeout,
                            impersonate="chrome110",
                            verify=False
                        )
                        return response
                    except:
                        pass
            
            print(f"    Request error: {error_msg[:100]}")
            raise

    def _fallback_extract_author(self, article, text: str) -> str:
        """Fallback author extraction if AgentSumm is not available"""
        if article.authors:
            return article.authors[0]
        
        combined_text = " ".join([
            article.title or "",
            getattr(article, "meta_description", "") or "",
            article.text or ""
        ])
        
        match = re.search(r"\b[Bb]y\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)", combined_text)
        if match:
            return match.group(1)
        
        return "Unknown"

    def calculate_relevance_score(self, title: str, content: str) -> tuple:
        """Calculate relevance score based on your custom keywords"""
        combined_text = f"{title} {content}".lower()
        found_keywords = []
        score = 0.0
        
        for keyword in self.active_keywords:
            if keyword.lower() in combined_text:
                found_keywords.append(keyword)
                
                # Core priority keywords
                if keyword.lower() in ['luxury', 'jewellery', 'fine jewellery', 'craftsmanship', 'jewels']:
                    score += 4.0
                # Primary jewelry terms + royalty keywords
                elif keyword.lower() in ['jewelry', 'diamond', 'engagement ring', 'wedding ring', 'Lab grown diamonds',
                                         'Diamond price', 'Gold price', 'crown', 'tiara', 'coronation', 'queen', 
                                         'king', 'prince', 'princess', 'duchess', 'duke', 'royal family', 
                                         'buckingham palace', 'windsor', 'crown jewels', 'state visit', 
                                         'royal wedding', 'monarchy', 'sovereign', 'regalia', 'royal collection', 'palace']:
                    score += 3.0
                # Jewelry pieces and materials
                elif keyword.lower() in ['necklace', 'bracelet', 'earrings', 'pendant', 'brooch',
                                         'gold', 'platinum', 'silver', 'emerald', 'sapphire', 'ruby']:
                    score += 2.5
                # Premium luxury brands
                elif keyword.lower() in ['cartier', 'tiffany', 'bulgari', 'chanel', 'dior', 'van cleef',
                                         'graff', 'harry winston', 'chopard', 'piaget', 'boucheron']:
                    score += 3.5
                # Fashion and luxury terms
                elif keyword.lower() in ['fashion', 'accessories', 'watches', 'timepiece', 'collection', 
                                         'launch', 'haute couture', 'limited edition']:
                    score += 2.5
                # Events and celebrity
                elif keyword.lower() in ['red carpet', 'celebrity', 'fashion week', 'auction', 'royal', 'royals']:
                    score += 2.0
                # Industry terms
                elif keyword.lower() in ['collaboration', 'investment', 'trends', 'style', 'Luxury sector', 
                                         'Luxury marketing trends']:
                    score += 1.5
                else:
                    score += 1.0
        
        # Bonus for multiple keyword matches
        if len(found_keywords) > 2:
            score *= 1.2
        if len(found_keywords) > 4:
            score *= 1.4
            
        return score, found_keywords
    
    def try_rss_feed(self, publication: str, feed_url: str) -> List[ArticleCandidate]:
        """Try to fetch articles from a single RSS feed"""
        candidates = []
        
        if not feed_url:
            return candidates
            
        try:
            is_premium = any(domain in feed_url for domain in ['downjones.io', 'wsj.com', 'nytimes.com'])
            
            if is_premium and self.scraper_type == 'curl-cffi':
                response = self.scraper.get(
                    feed_url,
                    timeout=15,
                    impersonate="chrome110",
                    headers={
                        'User-Agent': self.get_random_user_agent(),
                        'Accept': 'application/rss+xml, application/xml, text/xml, */*',
                        'Accept-Language': 'en-US,en;q=0.9',
                    }
                )
            else:
                response = self.make_request(feed_url, timeout=10)
            
            if response.status_code != 200:
                return candidates
            
            feed = feedparser.parse(response.content)
            
            if not hasattr(feed, 'entries') or len(feed.entries) == 0:
                return candidates
            
            for entry in feed.entries[:20]:
                try:
                    if hasattr(entry, 'published_parsed') and entry.published_parsed:
                        pub_date = datetime(*entry.published_parsed[:6])
                    else:
                        pub_date = datetime.now()
                    
                    # Skip articles older than 7 days (weekly collection)
                    if (datetime.now() - pub_date).days > 7:
                        continue
                    
                    title = entry.get('title', '').strip()
                    summary = entry.get('summary', '').strip()
                    url = entry.get('link', '').strip()
                    
                    if not title or not url:
                        continue
                    
                    score, keywords = self.calculate_relevance_score(title, summary)
                    
                    if score >= 1.0:
                        candidate = ArticleCandidate(
                            title=title,
                            url=url,
                            publication=publication,
                            published_date=pub_date,
                            summary=summary,
                            relevance_score=score,
                            keywords_found=keywords
                        )
                        candidates.append(candidate)
                        
                except Exception as e:
                    continue
            
        except Exception as e:
            pass
        
        return candidates
    
    def try_multiple_rss_feeds(self, publication: str, feed_urls: List[str]) -> List[ArticleCandidate]:
        """Try to fetch articles from multiple RSS feeds"""
        all_candidates = []
        
        if not feed_urls:
            return all_candidates
        
        successful_feeds = 0
        feed_count = len(feed_urls)
        
        for idx, feed_url in enumerate(feed_urls, 1):
            candidates = self.try_rss_feed(publication, feed_url)
            
            if candidates:
                successful_feeds += 1
                all_candidates.extend(candidates)
        
        if all_candidates:
            print(f"  RSS: Found {len(all_candidates)} articles from {successful_feeds}/{feed_count} feeds")
        elif feed_count > 0:
            print(f"  RSS: No articles found from any of {feed_count} feeds")
        
        return all_candidates
    
    def is_relevant_url(self, url: str) -> bool:
        """Enhanced URL filtering - must contain at least 1 luxury keyword"""
        url_lower = url.lower()
        
        # Explicitly exclude National Jeweler category/section pages
        national_jeweler_excluded = [
            'https://nationaljeweler.com/',
            'https://nationaljeweler.com/industry',
            'https://nationaljeweler.com/industry/industry-other',
            'https://nationaljeweler.com/industry/independents',
            'https://nationaljeweler.com/industry/events-awards',
            'https://nationaljeweler.com/industry/financials',
            'https://nationaljeweler.com/industry/supplier-bulletin',
            'https://nationaljeweler.com/industry/technology',
            'https://nationaljeweler.com/industry/surveys',
            'https://nationaljeweler.com/industry/policies-issues',
            'https://nationaljeweler.com/industry/crime',
            'https://nationaljeweler.com/industry/majors',
            'https://nationaljeweler.com/diamonds-gems',
            'https://nationaljeweler.com/diamonds-gems/diamonds-gems-other',
            'https://nationaljeweler.com/diamonds-gems/lab-grown',
            'https://nationaljeweler.com/diamonds-gems/grading',
            'https://nationaljeweler.com/diamonds-gems/sourcing',
            'https://nationaljeweler.com/style',
            'https://nationaljeweler.com/style/style-other',
            'https://nationaljeweler.com/style/trends',
            'https://nationaljeweler.com/style/auctions',
            'https://nationaljeweler.com/style/watches',
            'https://nationaljeweler.com/style/collections',
            'https://nationaljeweler.com/opinions',
            'https://nationaljeweler.com/opinions/editors',
            'https://nationaljeweler.com/opinions/columnists'
        ]
        
        url_clean = url.rstrip('/')
        if url_clean in national_jeweler_excluded or url in national_jeweler_excluded:
            return False
        
        # Check for at least 1 luxury keyword in URL
        has_keyword = any(keyword.lower() in url_lower for keyword in self.active_keywords)
        
        # Exclude obviously irrelevant content
        exclude_terms = [
            'recipe', 'food', 'travel', 'politics', 'sports', 'health', 'weather',
            'football', 'soccer', 'cricket', 'tennis'
        ]
        has_excluded = any(term in url_lower for term in exclude_terms)
        
        return has_keyword and not has_excluded
    
    def fetch_urls_from_sitemap(self, sitemap_url: str) -> List[tuple]:
        urls = []
        try:
            response = self.make_request(sitemap_url, timeout=10)
            if response.status_code == 200:
                root = ET.fromstring(response.content)
                for url_elem in root:
                    loc_elem = url_elem.find('.//{http://www.sitemaps.org/schemas/sitemap/0.9}loc')
                    lastmod_elem = url_elem.find('.//{http://www.sitemaps.org/schemas/sitemap/0.9}lastmod')
                    
                    if loc_elem is not None:
                        url = loc_elem.text
                        if lastmod_elem is not None:
                            try:
                                lastmod_str = lastmod_elem.text
                                if 'T' in lastmod_str:
                                    lastmod_date = datetime.fromisoformat(lastmod_str.replace('Z', '+00:00'))
                                else:
                                    lastmod_date = datetime.strptime(lastmod_str[:10], '%Y-%m-%d')
                                lastmod_date = lastmod_date.replace(tzinfo=None)
                            except:
                                lastmod_date = datetime.now()
                        else:
                            lastmod_date = datetime.now()
                        
                        urls.append((url, lastmod_date))
        except:
            pass
        
        return urls
    
    def fetch_sitemap_articles(self, publication: str, sitemap_url: str) -> List[ArticleCandidate]:
        candidates = []
        
        try:
            response = self.make_request(sitemap_url, timeout=15)
            
            if response.status_code != 200:
                return candidates
            
            # Try multiple decoding strategies for problematic sitemaps
            xml_content = None
            
            try:
                xml_content = response.text
                root = ET.fromstring(xml_content)
            except (ET.ParseError, UnicodeDecodeError):
                xml_content = None
            
            if xml_content is None:
                try:
                    xml_content = response.content.decode('utf-8')
                    root = ET.fromstring(xml_content)
                except (ET.ParseError, UnicodeDecodeError):
                    xml_content = None
            
            if xml_content is None:
                try:
                    xml_content = response.content.decode('iso-8859-1')
                    root = ET.fromstring(xml_content)
                except (ET.ParseError, UnicodeDecodeError):
                    xml_content = None
            
            if xml_content is None:
                try:
                    import gzip
                    decompressed = gzip.decompress(response.content)
                    xml_content = decompressed.decode('utf-8')
                    root = ET.fromstring(xml_content)
                except:
                    xml_content = None
            
            if xml_content is None:
                print(f"  Sitemap error: Cannot parse XML")
                return candidates
            
            urls = []
            
            if 'sitemapindex' in root.tag:
                for sitemap in root[:3]:
                    loc_elem = sitemap.find('.//{http://www.sitemaps.org/schemas/sitemap/0.9}loc')
                    if loc_elem is not None:
                        sub_sitemap_url = loc_elem.text
                        urls.extend(self.fetch_urls_from_sitemap(sub_sitemap_url))
            
            elif 'urlset' in root.tag:
                for url_elem in root:
                    loc_elem = url_elem.find('.//{http://www.sitemaps.org/schemas/sitemap/0.9}loc')
                    lastmod_elem = url_elem.find('.//{http://www.sitemaps.org/schemas/sitemap/0.9}lastmod')
                    
                    if loc_elem is not None:
                        url = loc_elem.text
                        
                        if lastmod_elem is not None:
                            try:
                                lastmod_str = lastmod_elem.text
                                if 'T' in lastmod_str:
                                    lastmod_date = datetime.fromisoformat(lastmod_str.replace('Z', '+00:00'))
                                else:
                                    lastmod_date = datetime.strptime(lastmod_str[:10], '%Y-%m-%d')
                                lastmod_date = lastmod_date.replace(tzinfo=None)
                                
                                if (datetime.now() - lastmod_date).days > 7:
                                    continue
                            except:
                                lastmod_date = datetime.now()
                        else:
                            lastmod_date = datetime.now()
                        
                        urls.append((url, lastmod_date))
            
            for url, pub_date in urls[:50]:
                try:
                    if self.is_relevant_url(url):
                        candidate = ArticleCandidate(
                            title="",
                            url=url,
                            publication=publication,
                            published_date=pub_date,
                            summary="",
                            relevance_score=1.0
                        )
                        candidates.append(candidate)
                        
                except Exception:
                    continue
            
            if candidates:
                print(f"  Sitemap: Found {len(candidates)} articles")
            
        except Exception as e:
            print(f"  Sitemap error: {str(e)[:100]}")
        
        return candidates
    
    def collect_from_source(self, publication: str, source_info: dict) -> List[ArticleCandidate]:
        """Collect articles with proper fallback: sitemap → RSS"""
        all_candidates = []
        sitemap_tried = False
        sitemap_succeeded = False
        
        if source_info.get('sitemap_url'):
            sitemap_tried = True
            try:
                sitemap_candidates = self.fetch_sitemap_articles(publication, source_info['sitemap_url'])
                if sitemap_candidates:
                    all_candidates.extend(sitemap_candidates)
                    sitemap_succeeded = True
                else:
                    print(f"  Sitemap yielded 0 articles")
            except Exception as e:
                error_msg = str(e)
                if 'SSL' in error_msg or 'ssl' in error_msg.lower():
                    print(f"  Sitemap failed (SSL error)")
                else:
                    print(f"  Sitemap failed: {error_msg[:60]}")
        
        should_try_rss = (
            not sitemap_tried or
            not sitemap_succeeded or
            len(all_candidates) < 3
        )
        
        if should_try_rss and source_info.get('rss_feeds'):
            if sitemap_tried and len(all_candidates) > 0:
                print(f"  Sitemap yielded only {len(all_candidates)} - trying RSS for more...")
            elif sitemap_tried:
                print(f"  Falling back to RSS feeds...")
            
            try:
                rss_candidates = self.try_multiple_rss_feeds(publication, source_info['rss_feeds'])
                all_candidates.extend(rss_candidates)
            except Exception as e:
                error_msg = str(e)
                if 'SSL' in error_msg or 'ssl' in error_msg.lower():
                    print(f"  ⚠️  RSS also failed (SSL blocking)")
                else:
                    print(f"  RSS error: {error_msg[:100]}")
        
        # Remove duplicates
        unique_candidates = []
        seen_urls = set()
        for candidate in all_candidates:
            if candidate.url not in seen_urls:
                unique_candidates.append(candidate)
                seen_urls.add(candidate.url)
        
        return unique_candidates
    
    def extract_full_content(self, candidate: ArticleCandidate) -> ArticleCandidate:
        try:
            response = self.make_request(candidate.url, timeout=20)
            
            if response.status_code != 200:
                return None
            
            article = Article(candidate.url)
            article.download_state = 2
            article.html = response.text
            article.parse()
            
            if not article.text or len(article.text) < 150:
                return None
            
            candidate.full_content = article.text
            
            if not candidate.title and article.title:
                candidate.title = article.title
            
            full_score, full_keywords = self.calculate_relevance_score(
                candidate.title or "", article.text
            )
            
            candidate.relevance_score = full_score
            candidate.keywords_found = full_keywords

            # Extract author using AgentSumm if available, otherwise fallback
            if AGENTSUMM_AVAILABLE:
                candidate.author = extract_author(article, article.text)
            else:
                candidate.author = self._fallback_extract_author(article, article.text)
            
            if article.meta_description and len(article.meta_description) > len(candidate.summary):
                candidate.summary = article.meta_description
            
            # Threshold 1.0 for weekly collection
            if full_score >= 1.0:
                return candidate
            else:
                return None
            
        except Exception as e:
            error_msg = str(e)
            if '403' in error_msg or 'Forbidden' in error_msg:
                print(f"  Error: HTTP 403 Forbidden - {candidate.publication}")
            elif '404' in error_msg or 'Not Found' in error_msg:
                print(f"  Error: HTTP 404 Not Found - {candidate.publication}")
            elif '429' in error_msg or 'Too Many' in error_msg:
                print(f"  Error: HTTP 429 Rate Limited - {candidate.publication}")
            elif 'timeout' in error_msg.lower():
                print(f"  Error: Timeout - {candidate.publication}")
            elif 'SSL' in error_msg or 'ssl' in error_msg.lower():
                print(f"  Error: SSL blocking - {candidate.publication}")
            else:
                print(f"  Error: {error_msg[:60]} - {candidate.publication}")
            return None
    
    def collect_top_3_per_publication(self, sources_subset: List[str] = None) -> List[ArticleCandidate]:
        """Collect exactly top 3 articles from each publication"""
        print("Weekly Article Collection (Top 3 per Publication)")
        print("=" * 60)
        
        sources_to_use = sources_subset if sources_subset else list(self.target_sources.keys())
        print(f"Targeting {len(sources_to_use)} publications\n")
        
        all_articles = []
        
        for publication in sources_to_use:
            if publication not in self.target_sources:
                continue
                
            print(f"{publication}:")
            self.requests_per_source = 0
            source_info = self.target_sources[publication]
            
            # Initial collection attempt
            candidates = self.collect_from_source(publication, source_info)
            
            if not candidates:
                print(f"  No candidates found\n")
                time.sleep(random.uniform(3, 6))
                continue
            
            candidates.sort(key=lambda x: x.relevance_score, reverse=True)
            
            # Extract full content and collect articles
            publication_articles = []
            max_tries = min(len(candidates), 20)
            
            for candidate in candidates[:max_tries]:
                if len(publication_articles) >= 3:
                    break
                
                enhanced = self.extract_full_content(candidate)
                if enhanced:
                    publication_articles.append(enhanced)
                
                time.sleep(random.uniform(1, 2))
            
            # If we didn't get 3 articles, try RSS as additional fallback
            if len(publication_articles) < 3 and source_info.get('rss_feeds'):
                print(f"  Only collected {len(publication_articles)} articles - trying RSS for more...")
                
                try:
                    rss_candidates = self.try_multiple_rss_feeds(publication, source_info['rss_feeds'])
                    
                    # Remove candidates we already tried
                    tried_urls = {c.url for c in candidates}
                    new_rss_candidates = [c for c in rss_candidates if c.url not in tried_urls]
                    
                    if new_rss_candidates:
                        print(f"  Found {len(new_rss_candidates)} new RSS candidates to try...")
                        new_rss_candidates.sort(key=lambda x: x.relevance_score, reverse=True)
                        
                        # Try to extract from new RSS candidates
                        for candidate in new_rss_candidates[:10]:
                            if len(publication_articles) >= 3:
                                break
                            
                            enhanced = self.extract_full_content(candidate)
                            if enhanced:
                                publication_articles.append(enhanced)
                            
                            time.sleep(random.uniform(1, 2))
                except Exception as e:
                    print(f"  RSS fallback error: {str(e)[:60]}")
            
            publication_articles.sort(key=lambda x: x.relevance_score, reverse=True)
            final_3 = publication_articles[:3]
            
            if final_3:
                scores = [f"{a.relevance_score:.1f}" for a in final_3]
                print(f"  Collected: {len(final_3)} article(s) [scores: {', '.join(scores)}]\n")
            else:
                print(f"  Collected: 0 articles\n")
            
            all_articles.extend(final_3)
            
            time.sleep(random.uniform(3, 6))
        
        print(f"Collection complete: {len(all_articles)} total articles")
        print(f"Publications covered: {len(set(a.publication for a in all_articles))}/{len(sources_to_use)}")
        
        return all_articles
    
    def generate_collection_report(self, articles: List[ArticleCandidate]) -> str:
        if not articles:
            return "No articles collected"
        
        report = []
        report.append("\nARTICLE COLLECTION REPORT")
        report.append("=" * 60)
        report.append(f"Total Articles: {len(articles)}")
        report.append(f"Average Relevance Score: {sum(a.relevance_score for a in articles) / len(articles):.1f}\n")
        
        pub_counts = {}
        for article in articles:
            pub_counts[article.publication] = pub_counts.get(article.publication, 0) + 1
        
        report.append("By Publication:")
        for pub, count in sorted(pub_counts.items(), key=lambda x: x[1], reverse=True):
            report.append(f"  {pub}: {count}")
        
        all_keywords = []
        for article in articles:
            all_keywords.extend(article.keywords_found or [])
        
        keyword_counts = {}
        for keyword in all_keywords:
            keyword_counts[keyword] = keyword_counts.get(keyword, 0) + 1
        
        report.append("\nTop Keywords:")
        for keyword, count in sorted(keyword_counts.items(), key=lambda x: x[1], reverse=True)[:10]:
            report.append(f"  {keyword} ({count})")
        
        report.append("\nArticles:\n")
        for i, article in enumerate(articles, 1):
            report.append(f"{i}. {article.title}")
            report.append(f"   {article.publication} | {article.author} | Score: {article.relevance_score:.1f}")
            report.append(f"   {article.published_date.strftime('%Y-%m-%d')}")
            report.append(f"   {article.url}\n")
        
        return "\n".join(report)
    
    def save_results(self, articles: List[ArticleCandidate], filename: str = "collected_articles.json"):
        data = []
        for article in articles:
            data.append({
                'title': article.title,
                'url': article.url,
                'publication': article.publication,
                'author': article.author,
                'published_date': article.published_date.isoformat(),
                'summary': article.summary,
                'full_content': article.full_content,
                'relevance_score': article.relevance_score,
                'keywords_found': article.keywords_found,
                'content_length': len(article.full_content)
            })
        
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        
        return filename

def main():
    print("Luxury Article Collector")
    print("=" * 60)
    
    collector = CustomArticleCollector()
    
    print(f"\nAvailable Sources ({len(collector.target_sources)}):")
    for i, pub in enumerate(collector.target_sources.keys(), 1):
        print(f"{i:2d}. {pub}")
    
    print("\nCollection Mode:")
    print("1. Weekly Roundup (Top 3 per publication)")
    
    try:
        use_subset = input("\nUse specific sources only? (y/N): ").lower().startswith('y')
        sources_subset = None
        
        if use_subset:
            source_names = input("Enter source numbers (comma-separated): ")
            if source_names.strip():
                indices = [int(x.strip()) - 1 for x in source_names.split(',')]
                sources_list = list(collector.target_sources.keys())
                sources_subset = [sources_list[i] for i in indices if 0 <= i < len(sources_list)]
                print(f"Using: {', '.join(sources_subset)}\n")
        
    except (ValueError, KeyboardInterrupt):
        sources_subset = None
    
    articles = collector.collect_top_3_per_publication(sources_subset=sources_subset)
    
    report = collector.generate_collection_report(articles)
    print(f"{report}")
    
    if articles:
        filename = collector.save_results(articles)
        print(f"Saved to: {filename}")
        
        print(f"\nIntegration Preview:")
        print(f"Articles are ready to feed into your BART summarizer!")
        for i, article in enumerate(articles[:3], 1):
            print(f"{i}. {article.title} ({article.publication})")
            print(f"   Content: {len(article.full_content)} characters")
    else:
        print("No relevant articles found.")

if __name__ == "__main__":
    main()