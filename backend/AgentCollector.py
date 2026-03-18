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
    print("âš ï¸  Warning: Could not import extract_author from AgentSumm")
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
            # 'luxury', 'jewellery', 'fine jewellery', 'craftsmanship',
            # 'jewelry', 'diamond', 'engagement ring', 'wedding ring',
            # 'fashion', 'accessories', 'watches', 'timepiece',
            # 'necklace', 'bracelet', 'earrings', 'pendant', 'brooch',
            # 'gold', 'platinum', 'silver', 'emerald', 'sapphire', 'ruby',
            # 'cartier', 'tiffany', 'bulgari', 'chanel', 'dior', 'van cleef',
            # 'graff', 'harry winston', 'chopard', 'piaget', 'boucheron',
            # 'red carpet', 'celebrity', 'haute couture', 'collection',
            # 'launch', 'collaboration', 'limited edition', 'auction',
            # 'investment', 'trends', 'style', 'fashion week', 'royal', 'royals',
            # 'Luxury sector', 'Luxury marketing trends', 'Lab grown diamonds',
            # 'Diamond price', 'Gold price', 'jewels',
            # # English royalty keywords
            # 'crown', 'tiara', 'coronation', 'queen', 'king', 'prince', 'princess',
            # 'duchess', 'duke', 'royal family', 'buckingham palace', 'windsor',
            # 'crown jewels', 'state visit', 'royal wedding', 'monarchy',
            # 'sovereign', 'regalia', 'royal collection', 'palace'
            'eurozone derivatives clearing','euro interest rate swaps clearing',
            'euro ccp infrastructure','emir clearing rules','clearing house',
            'interest-rate derivatives','otc derivatives','interest rate swaps',
            'fx swaps','credit derivatives','central counterparty clearing',
            'eurozone interest rate derivatives clearing infrastructure', 'mutual funds',
            'hedge funds', 'private equity', 'blockchain', 'cryptocurrency', 'fintech',
            'gold market price', 'risk management', 'compliance',
        ]
        
        self.active_keywords = list(self.luxury_keywords)

        # Your specific publication sources - MULTIPLE RSS FEEDS SUPPORTED
        self.target_sources = {
        'FNLondon': {

                        'base_url': 'https://www.fnlondon.com/',

                        'rss_feeds': [],

                        'sitemap_url': 'https://www.fnlondon.com/fn_google_news.xml'

                    },



        'Institutional Investor': {

                        'base_url': 'https://www.institutionalinvestor.com/',

                        'rss_feeds': ['https://www.institutionalinvestor.com/rss.xml'],

                        'sitemap_url': 'https://www.institutionalinvestor.com/sitemap.xml'

                    },



        'Euromoney': {

                        'base_url': 'https://www.euromoney.com/',

                        'rss_feeds': ['https://www.euromoney.com/feed'],

                        'sitemap_url': 'https://www.euromoney.com/sitemap_index.xml'

                    },



        'FX Markets': {

                        'base_url': 'https://www.fx-markets.com/',

                        'rss_feeds': ['https://www.fx-markets.com/feeds/rss'],

                        'sitemap_url': 'https://www.fx-markets.com/sitemap.xml'

                    },



        'CNBC': {

                        'base_url': 'https://www.cnbc.com/',

                        'rss_feeds': ['https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10001147', 'https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=15839069',  'https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=21324812'],

                        'sitemap_url': 'https://www.cnbc.com/sitemap_news.xml'

                    },



        'Monocle': {

                        'base_url': 'https://monocle.com/',

                        'rss_feeds': ['https://monocle.com/feed'],

                        'sitemap_url': 'https://monocle.com/sitemap_index.xml'

                    },



        'BBC': {

                        'base_url': 'https://www.bbc.com/',

                        'rss_feeds': ['https://feeds.bbci.co.uk/news/rss.xml', 'https://feeds.bbci.co.uk/news/business/rss.xml', 'https://feeds.bbci.co.uk/news/world/rss.xml'],

                        'sitemap_url': 'https://www.bbc.com/sitemaps/https-index-com-news.xml'

                    },



        'Yahoo Finance': {

                        'base_url': 'https://finance.yahoo.com/',

                        'rss_feeds': [],

                        'sitemap_url': 'https://www.yahoo.com/news-sitemap.xml'

                    },



        'This is Money': {

                        'base_url': 'https://www.thisismoney.co.uk/',

                        'rss_feeds': ['https://www.dailymail.co.uk/money/articles.rss'],

                        'sitemap_url': 'https://www.thisismoney.co.uk/newssitemap.xml'

                    },



        'Money Week': {

                        'base_url': 'https://moneyweek.com/',

                        'rss_feeds': ['https://moneyweek.com/feeds.xml'],

                        'sitemap_url': 'https://moneyweek.com/sitemap-news.xml'

                    },



        'Banking Technology Magazine': {

                        'base_url': 'https://www.fintechfutures.com/',

                        'rss_feeds': ['https://www.fintechfutures.com/rss.xml'],

                        'sitemap_url': 'https://www.fintechfutures.com/googlenews.xml'

                    },



        'City AM': {

                        'base_url': 'https://www.cityam.com/',

                        'rss_feeds': ['https://www.cityam.com/feed'],

                        'sitemap_url': 'https://www.cityam.com/sitemap.xml'

                    },
        'Financial Times (FT)': {
            'base_url': 'https://www.ft.com/',
            'rss_feeds': [],  # section-only / paid; no simple global feed
            'sitemap_url': 'https://www.ft.com/sitemaps/news.xml'
        },

        'Bloomberg': {
            'base_url': 'https://www.bloomberg.com/',
            'rss_feeds': [
                'https://feeds.bloomberg.com/markets/news.rss',
                'https://feeds.bloomberg.com/politics/news.rss'
            ],
            'sitemap_url': 'https://www.bloomberg.com/feeds/bbiz/sitemap_news.xml'
        },

        'The Fintech Times': {
            'base_url': 'https://thefintechtimes.com/',
            'rss_feeds': [
                'https://thefintechtimes.com/feed',
                'https://thefintechtimes.com/category/news/feed'
            ],
            'sitemap_url': 'https://thefintechtimes.com/sitemap.xml'
        },

        'PYMNTS': {
            'base_url': 'https://www.pymnts.com/',
            'rss_feeds': [
                'https://www.pymnts.com/feed/',
                'https://www.pymnts.com/category/banking/feed/'
            ],
            'sitemap_url': 'https://www.pymnts.com/sitemap.xml'
        },

        'Futures & Options World (FOW)': {
            'base_url': 'https://www.fow.com/',
            'rss_feeds': [
                'https://www.fow.com/rss'
            ],
            'sitemap_url': 'https://www.fow.com/sitemap.xml'
        },

        'Forbes': {
            'base_url': 'https://www.forbes.com/',
            'rss_feeds': [
                'https://www.forbes.com/innovation/feed',
                'https://www.forbes.com/innovation/feed2'
            ],
            'sitemap_url': 'https://www.forbes.com/news_sitemap.xml'
        },

        'Fintech Futures': {
            'base_url': 'https://www.fintechfutures.com/',
            'rss_feeds': [
                'https://www.fintechfutures.com/feed/',
                'https://www.fintechfutures.com/category/news/feed/'
            ],
            'sitemap_url': 'https://www.fintechfutures.com/googlenews.xml'
        },

        'Reuters': {
            'base_url': 'https://www.reuters.com/',
            'rss_feeds': [],  # many legacy feeds deprecated; prefer sitemaps/APIs
            'sitemap_url': 'https://www.reuters.com/sitemap_news_index1.xml'
        },

        'World Finance': {
            'base_url': 'https://www.worldfinance.com/',
            'rss_feeds': ['https://www.worldfinance.com/news/rss-feed'],
            'sitemap_url': 'https://www.worldfinance.com/sitemap.xml'
        },

        'The Times (UK)': {
            'base_url': 'https://www.thetimes.co.uk/',
            'rss_feeds': [],  # paywalled; no broad free RSS
            'sitemap_url': 'https://times.newsprints.co.uk/sitemap/brands/'
        },

        'The Telegraph': {
            'base_url': 'https://www.telegraph.co.uk/',
            'rss_feeds': [],  # many section RSS feeds are limited/deprecated
            'sitemap_url': 'https://www.telegraph.co.uk/sitemap.xml'
        },

        'Evening Standard': {
            'base_url': 'https://www.standard.co.uk/',
            'rss_feeds': [
                'https://www.standard.co.uk/news/rss'
            ],
            'sitemap_url': 'https://www.standard.co.uk/sitemap.xml'
        },

        'Bank Policy Institute': {
            'base_url': 'https://bpi.com/',
            'rss_feeds': [
                'https://bpi.com/feed/',
                'https://bpi.com/category/news/feed/'
            ],
            'sitemap_url': 'https://bpi.com/sitemap.xml'
        },

        'Risk.net': {
            'base_url': 'https://www.risk.net/',
            'rss_feeds': [],  # channel feeds exist but often gated/specialised
            'sitemap_url': 'https://www.risk.net/sitemap.xml'
        },

        'FStech': {
            'base_url': 'https://www.fstech.co.uk/',
            'rss_feeds': [
                'https://www.fstech.co.uk/rss'
            ],
            'sitemap_url': 'https://www.fstech.co.uk/sitemap.xml'
        },

        'Investment Week': {
            'base_url': 'https://www.investmentweek.co.uk/',
            'rss_feeds': [
                'https://www.investmentweek.co.uk/feeds/rss'
            ],
            'sitemap_url': 'https://www.investmentweek.co.uk/sitemap.xml'
        },

        'Wealth & Finance': {
            'base_url': 'https://wealthandfinance.digital/',
            'rss_feeds': [
                'https://wealthandfinance.digital/feed/',
                'https://wealthandfinance.digital/category/personal-finance/feed/'
            ],
            'sitemap_url': 'https://wealthandfinance.digital/sitemap.xml'
        },

        'Portfolio Adviser': {
            'base_url': 'https://portfolio-adviser.com/',
            'rss_feeds': [
                'https://portfolio-adviser.com/feed/',
                'https://portfolio-adviser.com/category/interviews/feed/'
            ],
            'sitemap_url': 'https://portfolio-adviser.com/sitemap.xml'
        },

        'The Banker': {
            'base_url': 'https://www.thebanker.com/',
            'rss_feeds': [],  # per-channel RSS historically; current endpoints opaque
            'sitemap_url': 'https://www.thebanker.com/sitemap.xml'
        },

        'GlobalCapital': {
            'base_url': 'https://www.globalcapital.com/',
            'rss_feeds': [],  # often /rss or /rss.xml per section; not clearly exposed
            'sitemap_url': 'https://www.globalcapital.com/sitemap.xml'
        },

        'TradingTech Insight': {
            'base_url': 'https://a-teaminsight.com/category/tradingtech-insight/',
            'rss_feeds': [
                'https://a-teaminsight.com/category/tradingtech-insight/feed/'
            ],
            'sitemap_url': 'https://a-teaminsight.com/sitemap.xml'
        },

        'RegTech Insight': {
            'base_url': 'https://a-teaminsight.com/regtech-insight/',
            'rss_feeds': [
                'https://a-teaminsight.com/category/regtech-insight/feed/'
            ],
            'sitemap_url': 'https://a-teaminsight.com/sitemap.xml'
        },

        'CoinDesk': {
            'base_url': 'https://www.coindesk.com/',
            'rss_feeds': [
                'https://www.coindesk.com/arc/outboundfeeds/rss/'
            ],
            'sitemap_url': 'https://www.coindesk.com/sitemap-index.xml'
        },

        'FT Alphaville': {
            'base_url': 'https://www.ft.com/ft-alphaville',
            'rss_feeds': [
                'https://www.ft.com/ft-alphaville?format=rss'
            ],
            'sitemap_url': 'https://www.ft.com/sitemaps/news.xml'
        },

        'fDi Intelligence': {
            'base_url': 'https://www.fdiintelligence.com/',
            'rss_feeds': [],
            'sitemap_url': 'https://www.fdiintelligence.com/sitemap.xml'
        },

        'The New York Times (Business)': {
            'base_url': 'https://www.nytimes.com/',
            'rss_feeds': [
                'https://rss.nytimes.com/services/xml/rss/nyt/Business.xml'
            ],
            'sitemap_url': 'https://www.nytimes.com/sitemap/'
        },

        'The Independent': {
            'base_url': 'https://www.independent.co.uk/',
            'rss_feeds': [
                'https://www.independent.co.uk/news/uk/rss',
                'https://www.independent.co.uk/news/world/rss'
            ],
            'sitemap_url': 'https://www.independent.co.uk/sitemap.xml'
        },

        'CNN (Finance & Business)': {
            'base_url': 'https://www.cnn.com/',
            'rss_feeds': [],  # business/markets feeds exist but are now poorly documented; many users generate via third-party tools
            'sitemap_url': 'https://www.cnn.com/sitemap/news.xml'
        }

        }
        
        # Initialize scraper with priority order
        if CURL_CFFI_AVAILABLE:
            self.scraper = curl_requests.Session()
            self.scraper_type = 'curl-cffi'
            print("âœ… curl-cffi enabled (most powerful anti-blocking)")
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
            print("âœ… CloudScraper enabled for anti-blocking\n")
        else:
            self.scraper = requests.Session()
            self.scraper_type = 'requests'
            print("âš ï¸  Using basic requests (limited anti-blocking)\n")
        
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

    def _normalize_text(self, text: str) -> str:
        return re.sub(r"\s+", " ", (text or "").lower()).strip()

    def _keyword_tokens(self, keyword: str) -> List[str]:
        stop = {"the", "and", "for", "with", "from", "into", "of", "to", "in", "on", "a", "an"}
        parts = re.split(r"[^a-z0-9]+", keyword.lower())
        return [p for p in parts if p and p not in stop and len(p) > 2]

    def _weighted_score_for_keyword(self, kw_lower: str) -> float:
        if kw_lower in [
            'eurozone derivatives clearing',
            'euro interest rate swaps clearing',
            'euro ccp infrastructure',
            'emir clearing rules',
            'clearing house',
            'interest-rate derivatives',
            'otc derivatives',
            'interest rate swaps',
            'fx swaps',
            'credit derivatives',
            'central counterparty clearing',
            'eurozone interest rate derivatives clearing infrastructure',
        ]:
            return 4.0
        if kw_lower in ['mutual funds', 'hedge funds', 'private equity']:
            return 3.0
        if kw_lower in ['blockchain', 'cryptocurrency', 'fintech', 'gold market price']:
            return 2.5
        if kw_lower in ['risk management', 'compliance']:
            return 2.0
        return 1.0

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
        """Calculate relevance score based on active keywords."""
        combined_text = self._normalize_text(f"{title} {content}")
        found_keywords = []
        score = 0.0

        for keyword in self.active_keywords:
            kw_lower = self._normalize_text(keyword)
            base_w = self._weighted_score_for_keyword(kw_lower)

            if kw_lower and kw_lower in combined_text:
                found_keywords.append(keyword)
                score += base_w
                continue

            # Partial token fallback for longer phrases
            tokens = self._keyword_tokens(kw_lower)
            if len(tokens) >= 3:
                hits = sum(1 for t in tokens if t in combined_text)
                if hits >= 2:
                    found_keywords.append(keyword)
                    score += base_w * 0.5

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
                    
                    if score >= 0.5:
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
        """Enhanced URL filtering - requires at least one active keyword in URL."""
        url_lower = url.lower().rstrip('/')

        # Explicitly exclude National Jeweler category/section pages
        national_jeweler_excluded = [
            'https://nationaljeweler.com',
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
            'https://nationaljeweler.com/opinions/columnists',
        ]

        if url_lower in national_jeweler_excluded:
            return False

        # Exclude obviously non-article pages
        exclude_terms = [
            '/tag/', '/tags/', '/category/', '/categories/', '/author/', '/authors/',
            '/search', '/topic/', '/topics/', '/video/', '/videos/', '/podcast/',
            '/podcasts/', '/gallery/', '/galleries/', '/live/', '/events/',
            '/newsletter', '/subscribe', '/privacy', '/terms'
        ]
        if any(term in url_lower for term in exclude_terms):
            return False

        # Require at least one active keyword in URL
        has_keyword = any(keyword.lower() in url_lower for keyword in self.active_keywords)
        return has_keyword
    
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
                sitemap_entries = []
                for sitemap in root:
                    loc_elem = sitemap.find('.//{http://www.sitemaps.org/schemas/sitemap/0.9}loc')
                    lastmod_elem = sitemap.find('.//{http://www.sitemaps.org/schemas/sitemap/0.9}lastmod')
                    if loc_elem is None or not loc_elem.text:
                        continue

                    loc = loc_elem.text.strip()

                    # Priority 1: explicit lastmod
                    lastmod_dt = datetime.min
                    if lastmod_elem is not None and lastmod_elem.text:
                        try:
                            lm = lastmod_elem.text.strip().replace('Z', '+00:00')
                            lastmod_dt = datetime.fromisoformat(lm).replace(tzinfo=None)
                        except Exception:
                            pass

                    # Priority 2: infer recency from URL pattern like /archive/2026-3.xml
                    if lastmod_dt == datetime.min:
                        m = re.search(r'/(\d{4})-(\d{1,2})\.xml(?:\.gz)?$', loc)
                        if m:
                            try:
                                y = int(m.group(1))
                                mo = int(m.group(2))
                                lastmod_dt = datetime(y, mo, 1)
                            except Exception:
                                pass

                    sitemap_entries.append((loc, lastmod_dt))

                sitemap_entries.sort(key=lambda x: x[1], reverse=True)
                max_child_sitemaps = 10
                for sub_sitemap_url, _ in sitemap_entries[:max_child_sitemaps]:
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
            
            for url, pub_date in urls[:150]:
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
        """Collect articles with proper fallback: sitemap â†’ RSS"""
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
                    print(f"  âš ï¸  RSS also failed (SSL blocking)")
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
        print("Weekly Article Collection (Top 5 per Publication)")
        print("=" * 60)
        max_articles_per_publication = 5
        
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
                if len(publication_articles) >= max_articles_per_publication:
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
                        for candidate in new_rss_candidates[:30]:
                            if len(publication_articles) >= max_articles_per_publication:
                                break
                            
                            enhanced = self.extract_full_content(candidate)
                            if enhanced:
                                publication_articles.append(enhanced)
                            
                            time.sleep(random.uniform(1, 2))
                except Exception as e:
                    print(f"  RSS fallback error: {str(e)[:60]}")
            
            publication_articles.sort(key=lambda x: x.relevance_score, reverse=True)
            final_3 = publication_articles[:max_articles_per_publication]
            
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


