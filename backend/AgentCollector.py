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
    candidate_source: str = "unknown"

class CustomArticleCollector:
    def __init__(self, topic: str = "finance"):
        """Initialize collector with your specific sources and keywords"""
        self.topic = (topic or "finance").strip().lower()
        if self.topic not in {"finance", "luxury"}:
            self.topic = "finance"
        
        # Your custom keywords for relevance filtering (British English)
        self.finance_keywords = [
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
            'gold market price', 'risk management', 'compliance', "Equivalence regime", 
            "Brexit financial regulations", "European banking", "Capitals of finance", 
            "Post-Brexit clearing", "Institutional confidence", "Stablecoin", "Venture Capital", 
            "Market Infrastructure", "Market Volatility", "Capital Requirements", 
            "Investment banks", "Tokenisation", "Systemic risk", "Davos", "Capitalisation"
        ]
        
        self.finance_keyword_weight_map = {
            # Priority 4.0: core clearing/reg structure
            "eurozone derivatives clearing": 4.0,
            "euro interest rate swaps clearing": 4.0,
            "euro ccp infrastructure": 4.0,
            "emir clearing rules": 4.0,
            "post-brexit clearing": 4.0,
            "central counterparty clearing": 4.0,
            "capital requirements": 4.0,
            "eurozone interest rate derivatives clearing infrastructure": 4.0,
            # Priority 3.0: institutional markets/policy risk
            "otc derivatives": 3.0,
            "interest-rate derivatives": 3.0,
            "interest rate swaps": 3.0,
            "fx swaps": 3.0,
            "credit derivatives": 3.0,
            "systemic risk": 3.0,
            "market infrastructure": 3.0,
            "market volatility": 3.0,
            "brexit financial regulations": 3.0,
            "equivalence regime": 3.0,
            "european banking": 3.0,
            "institutional confidence": 3.0,
            "investment banks": 3.0,
            # Priority 2.5: fintech / digital assets
            "fintech": 2.5,
            "blockchain": 2.5,
            "cryptocurrency": 2.5,
            "stablecoin": 2.5,
            "tokenisation": 2.5,
            "venture capital": 2.5,
            "clearing house": 2.5,
            # Priority 2.0: broad finance
            "mutual funds": 2.0,
            "hedge funds": 2.0,
            "private equity": 2.0,
            "risk management": 2.0,
            "compliance": 2.0,
            "capitalisation": 2.0,
            "gold market price": 2.0,
            # Lower-signal broad phrases
            "capitals of finance": 1.5,
            "davos": 1.5,
        }
        self.finance_combo_bonuses = [
            (("brexit", "clearing"), 1.0),
            (("stablecoin", "compliance"), 0.8),
            (("stablecoin", "regulation"), 0.8),
            (("derivatives", "ccp"), 1.0),
            (("derivatives", "clearing"), 1.0),
        ]
        self.max_hits_per_keyword = 2
        self.max_total_repeat_bonus = 3.0
        self.unique_keyword_bonus = 0.35
        self.max_unique_keyword_bonus = 2.8

        # Your specific publication sources - MULTIPLE RSS FEEDS SUPPORTED
        self.finance_sources = {
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

                        'rss_feeds': ['https://finance.yahoo.com/news/rss-feed'],

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



        'City AM': {

                        'base_url': 'https://www.cityam.com/',

                        'rss_feeds': ['https://www.cityam.com/feed'],

                        'sitemap_url': 'https://www.cityam.com/sitemap.xml'

                    },
        'Financial Times (FT)': {
            'base_url': 'https://www.ft.com/',
            'rss_feeds': ['https://www.ft.com/rss/home/international'],  # section-only / paid; no simple global feed
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
            'sitemap_url': 'https://thefintechtimes.com/sitemap_index.xml'
        },

        'PYMNTS': {
            'base_url': 'https://www.pymnts.com/',
            'rss_feeds': [
                'https://www.pymnts.com/feed/',
                'https://www.pymnts.com/category/banking/feed/'
            ],
            'sitemap_url': 'https://www.pymnts.com/sitemap-news.xml'
        },

        'Futures & Options World (FOW)': {
            'base_url': 'https://www.fow.com/',
            'rss_feeds': [],
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
            'rss_feeds': [
                'https://feeds.reuters.com/reuters/businessNews',
                'https://feeds.reuters.com/news/artsculture'
            ],
            'sitemap_url': 'https://www.reuters.com/arc/outboundfeeds/news-sitemap/?outputType=xml'
        },

        'World Finance': {
            'base_url': 'https://www.worldfinance.com/',
            'rss_feeds': ['https://www.worldfinance.com/feed/'],
            'sitemap_url': 'https://www.worldfinance.com/wp-sitemap.xml'
        },

        'The Times (UK)': {
            'base_url': 'https://www.thetimes.co.uk/',
            'rss_feeds': [],  # paywalled; no broad free RSS
            'sitemap_url': 'https://www.thetimes.com/sitemaps/news'
        },

        'The Telegraph': {
            'base_url': 'https://www.telegraph.co.uk/',
            'rss_feeds': ['https://www.telegraph.co.uk/rss.xml'],  # many section RSS feeds are limited/deprecated
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
            'rss_feeds': [],
            'sitemap_url': 'https://www.fstech.co.uk/sitemap_index.xml'
        },

        'Investment Week': {
            'base_url': 'https://www.investmentweek.co.uk/',
            'rss_feeds': [
                'https://www.investmentweek.co.uk/feeds/rss'
            ],
            'sitemap_url': 'https://www.investmentweek.co.uk/news-sitemap.xml'
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
            'base_url': 'https://www.portfolio-adviser.com/',
            'rss_feeds': [
                'https://www.portfolio-adviser.com/feed/'
            ],
            'sitemap_url': 'https://www.portfolio-adviser.com/sitemap.xml'
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

        'FT Alphaville': {
            'base_url': 'https://www.ft.com/ft-alphaville',
            'rss_feeds': [
                'https://www.ft.com/alphaville?format=rss'
            ],
            'sitemap_url': 'https://www.ft.com/sitemaps/news.xml'
        },

        'fDi Intelligence': {
            'base_url': 'https://www.fdiintelligence.com/',
            'rss_feeds': [],
            'sitemap_url': 'https://www.fdiintelligence.com/sitemap.xml'
        },

        'The Independent': {
            'base_url': 'https://www.independent.co.uk/',
            'rss_feeds': [
                'https://www.independent.co.uk/news/uk/rss',
                'https://www.independent.co.uk/news/world/rss'
            ],
            'sitemap_url': 'https://www.independent.co.uk/sitemaps/googlenews'
        },

        'CNN (Finance & Business)': {
            'base_url': 'https://www.cnn.com/',
            'rss_feeds': [],  # business/markets feeds exist but are now poorly documented; many users generate via third-party tools
            'sitemap_url': 'https://www.cnn.com/sitemap/news.xml'
        },

        'Economist': {
            'base_url': 'https://www.economist.com/',
            'rss_feeds': ['https://www.economist.com/finance-and-economics/rss.xml'],
            'sitemap_url': 'https://www.economist.com/googlenews.xml'
        },
        'CoinDesk': {
            'base_url': 'https://www.coindesk.com/',
            'rss_feeds': ['https://www.coindesk.com/arc/outboundfeeds/rss/'],
            'sitemap_url': 'https://www.coindesk.com/arc/outboundfeeds/news-sitemap-index'
        },
        
        'TechCrunch': {
            'base_url': 'https://techcrunch.com/',
            'rss_feeds': ['https://techcrunch.com/feed/'],
            'sitemap_url': 'https://techcrunch.com/news-sitemap.xml'
        },
        'Securities Finance Times': {
            'base_url': 'https://www.securitiesfinancetimes.com/',
            'rss_feeds': ['https://www.securitiesfinancetimes.com/rssfeed.php'],
            'sitemap_url': None  # No sitemap found; robots.txt has no sitemap directive
        },
        'Bobsguide': {
            'base_url': 'https://www.bobsguide.com/',
            'rss_feeds': ['https://www.bobsguide.com/feed/'],
            'sitemap_url': 'https://www.bobsguide.com/sitemap.xml'
        },
        'Inc': {
            'base_url': 'https://www.inc.com/',
            'rss_feeds': [
                'https://www.inc.com/rss'
            ],
            'sitemap_url': 'https://www.inc.com/sitemap/sitemap_news.xml'
        },
        'Global Treasurer': {
            'base_url': 'https://www.theglobaltreasurer.com/',
            'rss_feeds': [
                'https://www.theglobaltreasurer.com/feed/'
            ],
            'sitemap_url': 'https://www.theglobaltreasurer.com/wp-sitemap.xml'
        },
        'The CFO': {
            'base_url': 'https://the-cfo.io/',
            'rss_feeds': [
                'https://the-cfo.io/feed/'
            ],
            'sitemap_url': 'https://the-cfo.io/sitemap_index.xml'
        },
        'Portfolio Institutional': {
            'base_url': 'https://www.portfolio-institutional.co.uk/',
            'rss_feeds': [
                'https://www.portfolio-institutional.co.uk/feed/'
            ],
            'sitemap_url': 'https://www.portfolio-institutional.co.uk/sitemap_index.xml'
        },
        'The Trade': {
            'base_url': 'https://www.thetradenews.com/',
            'rss_feeds': [],
            'sitemap_url': 'https://www.thetradenews.com/sitemap_index.xml'
        },
        'Asset Servicing Times': {
            'base_url': 'https://www.assetservicingtimes.com/',
            'rss_feeds': [
                'https://www.assetservicingtimes.com/rssfeed.php'
            ],
            'sitemap_url': None
        },
        'Finance Magnates': {
            'base_url': 'https://www.financemagnates.com/',
            'rss_feeds': [
                'https://www.financemagnates.com/feed/'
            ],
            'sitemap_url': 'https://www.financemagnates.com/sitemap.xml'
        },



        }
        
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
            'luxury sector', 'luxury marketing trends', 'lab grown diamonds',
            'diamond price', 'gold price', 'jewels',
            'crown', 'tiara', 'coronation', 'queen', 'king', 'prince', 'princess',
            'duchess', 'duke', 'royal family', 'buckingham palace', 'windsor',
            'crown jewels', 'state visit', 'royal wedding', 'monarchy',
            'sovereign', 'regalia', 'royal collection', 'palace'
        ]
        self.luxury_keyword_weight_map = self._build_luxury_keyword_weight_map()
        self.luxury_combo_bonuses = [
            (("royal", "jewellery"), 1.0),
            (("red carpet", "diamond"), 0.8),
            (("haute couture", "collection"), 0.8),
        ]
        self.luxury_sources = self._build_luxury_sources()

        if self.topic == "luxury":
            self.active_keywords = [k.lower() for k in self.luxury_keywords]
            self.target_sources = self.luxury_sources
            self.keyword_weight_map = self.luxury_keyword_weight_map
            self.keyword_combo_bonuses = self.luxury_combo_bonuses
        else:
            self.active_keywords = [k.lower() for k in self.finance_keywords]
            self.target_sources = self.finance_sources
            self.keyword_weight_map = self.finance_keyword_weight_map
            self.keyword_combo_bonuses = self.finance_combo_bonuses

        # Cap behavior: keep dynamic caps for finance, fixed cap for luxury.
        self.use_dynamic_caps = (self.topic != "luxury")
        # Evaluate a few extra candidates past cap, then trim by full-content score.
        self.post_cap_buffer = 3
        # Stop wasting attempts on a source if it keeps returning 401.
        self.max_401_per_source = 5
        self.max_403_per_source = 8
        self.max_placeholder_skips_per_source = 5
        self.max_curl3_per_source = 5
        self.logged_bad_url_sources = set()

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

    def _build_luxury_keyword_weight_map(self) -> Dict[str, float]:
        return {
            "luxury": 4.0, "jewellery": 4.0, "fine jewellery": 4.0, "craftsmanship": 4.0, "jewels": 4.0,
            "jewelry": 3.0, "diamond": 3.0, "engagement ring": 3.0, "wedding ring": 3.0, "lab grown diamonds": 3.0,
            "diamond price": 3.0, "gold price": 3.0, "crown": 3.0, "tiara": 3.0, "coronation": 3.0, "queen": 3.0,
            "king": 3.0, "prince": 3.0, "princess": 3.0, "duchess": 3.0, "duke": 3.0, "royal family": 3.0,
            "buckingham palace": 3.0, "windsor": 3.0, "crown jewels": 3.0, "state visit": 3.0, "royal wedding": 3.0,
            "monarchy": 3.0, "sovereign": 3.0, "regalia": 3.0, "royal collection": 3.0, "palace": 3.0,
            "cartier": 3.5, "tiffany": 3.5, "bulgari": 3.5, "chanel": 3.5, "dior": 3.5, "van cleef": 3.5,
            "graff": 3.5, "harry winston": 3.5, "chopard": 3.5, "piaget": 3.5, "boucheron": 3.5,
            "necklace": 2.5, "bracelet": 2.5, "earrings": 2.5, "pendant": 2.5, "brooch": 2.5,
            "gold": 2.5, "platinum": 2.5, "silver": 2.5, "emerald": 2.5, "sapphire": 2.5, "ruby": 2.5,
            "fashion": 2.5, "accessories": 2.5, "watches": 2.5, "timepiece": 2.5, "collection": 2.5,
            "launch": 2.5, "haute couture": 2.5, "limited edition": 2.5,
            "red carpet": 2.0, "celebrity": 2.0, "fashion week": 2.0, "auction": 2.0, "royal": 2.0, "royals": 2.0,
            "collaboration": 1.5, "investment": 1.5, "trends": 1.5, "style": 1.5, "luxury sector": 1.5,
            "luxury marketing trends": 1.5
        }

    def _build_luxury_sources(self) -> Dict[str, dict]:
        return {
            'The Guardian': {
                'base_url': 'https://www.theguardian.com/fashion/womens-jewellery',
                'rss_feeds': ['https://www.theguardian.com/fashion/womens-jewellery/rss', 'https://www.theguardian.com/uk/rss'],
                'sitemap_url': 'https://www.theguardian.com/sitemaps/news.xml'
            },
            'The Telegraph': {
                'base_url': 'https://www.telegraph.co.uk/luxury/',
                'rss_feeds': ['https://www.telegraph.co.uk/luxury/rss'],
                'sitemap_url': 'https://www.telegraph.co.uk/luxury/sitemap.xml'
            },
            'Evening Standard': {
                'base_url': 'https://www.standard.co.uk/topic/jewellery',
                'rss_feeds': ['https://www.standard.co.uk/rss'],
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
                'rss_feeds': ['https://www.forbes.com/business/feed/'],
                'sitemap_url': 'https://www.forbes.com/news_sitemap.xml'
            },
            'Business of Fashion': {
                'base_url': 'https://www.businessoffashion.com/',
                'rss_feeds': ['https://www.businessoffashion.com/feed/'],
                'sitemap_url': 'https://www.businessoffashion.com/arc/outboundfeeds/sitemap/google-news/'
            },
            'Vogue Business': {
                'base_url': 'https://www.voguebusiness.com/',
                'rss_feeds': ['https://www.voguebusiness.com/feed'],
                'sitemap_url': 'https://www.vogue.com/feed/google-latest-news/sitemap-google-news'
            },
            "Harper's Bazaar": {
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
                'rss_feeds': ['https://www.vogue.co.uk/feed/rss'],
                'sitemap_url': 'https://www.vogue.co.uk/feed/sitemap/sitemap-google-news'
            },
            'Vanity Fair': {
                'base_url': 'https://www.vanityfair.com/',
                'rss_feeds': ['https://www.vanityfair.com/feed/rss'],
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
                'rss_feeds': ['https://www.townandcountrymag.com/rss/all.xml/'],
                'sitemap_url': 'https://www.townandcountrymag.com/sitemap_google_news.xml'
            },
            'StyleCaster': {
                'base_url': 'https://stylecaster.com/c/fashion/',
                'rss_feeds': ['https://stylecaster.com/feed/'],
                'sitemap_url': 'https://stylecaster.com/news-sitemap.xml'
            },
            'The Handbook': {
                'base_url': 'https://www.thehandbook.com/',
                'rss_feeds': [],
                'sitemap_url': 'https://www.thehandbook.com/sitemap.xml?postType=editorial&offset=0'
            },
            'Something About Rocks': {
                'base_url': 'https://somethingaboutrocks.com/',
                'rss_feeds': ['https://somethingaboutrocks.com/feed/'],
                'sitemap_url': None
            },
            'The Cut': {
                'base_url': 'https://www.thecut.com/',
                'rss_feeds': ['https://www.thecut.com/rss/index.xml'],
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
                'rss_feeds': ['https://www.retail-jeweller.com/feed/'],
                'sitemap_url': None
            },
            'Professional Jeweller': {
                'base_url': 'https://www.professionaljeweller.com/',
                'rss_feeds': ['https://www.professionaljeweller.com/feed/'],
                'sitemap_url': None
            },
            'Rapaport': {
                'base_url': 'https://rapaport.com/',
                'rss_feeds': ['https://rapaport.com/rss/'],
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
    
    def set_keywords_override(self, keywords: List[str]) -> None:
        cleaned = [str(k).strip().lower() for k in (keywords or []) if str(k).strip()]
        if cleaned:
            self.active_keywords = cleaned
            return
        base_keywords = self.luxury_keywords if self.topic == "luxury" else self.finance_keywords
        self.active_keywords = [k.lower() for k in base_keywords]

    def _normalize_text(self, text: str) -> str:
        return re.sub(r"\s+", " ", (text or "").lower()).strip()

    def _keyword_tokens(self, keyword: str) -> List[str]:
        stop = {"the", "and", "for", "with", "from", "into", "of", "to", "in", "on", "a", "an"}
        parts = re.split(r"[^a-z0-9]+", keyword.lower())
        return [p for p in parts if p and p not in stop and len(p) > 2]

    def _weighted_score_for_keyword(self, kw_lower: str) -> float:
        kw = (kw_lower or "").strip().lower()
        return self.keyword_weight_map.get(kw, 1.0)

    def _combo_bonus(self, text_lower: str) -> float:
        bonus = 0.0
        for terms, value in self.keyword_combo_bonuses:
            if all(t in text_lower for t in terms):
                bonus += value
        return bonus

    def _keyword_occurrences(self, text_lower: str, keyword: str) -> int:
        kw = (keyword or "").strip().lower()
        if not kw:
            return 0
        pattern = r'(?<!\w)' + re.escape(kw) + r'(?!\w)'
        return len(re.findall(pattern, text_lower))

    def get_random_user_agent(self):
        return random.choice(self.user_agents)

    def _dynamic_max_articles_for_publication(self, candidates: List[ArticleCandidate]) -> int:
        """
        Dynamic per-publication cap:
        - baseline 5
        - promote to 8 or 10 when top candidates are strong
        """
        baseline = 5
        if not candidates:
            return baseline

        top7 = candidates[:7]
        if not top7:
            return baseline

        avg_top7 = sum(c.relevance_score for c in top7) / len(top7)
        strong7 = sum(1 for c in top7 if c.relevance_score >= 7.0)
        strong10 = sum(1 for c in top7 if c.relevance_score >= 10.0)

        # Very strong source in this run
        if avg_top7 > 10.0 and strong10 >= 5:
            return 10

        # Strong source in this run
        if avg_top7 >= 7.0 and strong7 >= 3:
            return 8

        return baseline

    def _dynamic_max_from_extracted(self, extracted_articles: List[ArticleCandidate], baseline: int = 5) -> int:
        """
        Recompute cap from extracted/full-content scored articles.
        Uses up to top 7 extracted items.
        """
        if not extracted_articles:
            return baseline

        ranked = sorted(extracted_articles, key=lambda x: x.relevance_score, reverse=True)
        top7 = ranked[:7]
        avg_top7 = sum(a.relevance_score for a in top7) / len(top7)
        strong7 = sum(1 for a in top7 if a.relevance_score >= 7.0)
        strong10 = sum(1 for a in top7 if a.relevance_score >= 10.0)

        if avg_top7 > 10.0 and strong10 >= 5:
            return 10
        if avg_top7 >= 7.0 and strong7 >= 3:
            return 8
        return baseline
    
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
        found_keyword_set = set()
        score = 0.0
        repeat_bonus_total = 0.0

        for keyword in self.active_keywords:
            kw_lower = self._normalize_text(keyword)
            base_w = self._weighted_score_for_keyword(kw_lower)

            if kw_lower and kw_lower in combined_text:
                found_keywords.append(keyword)
                found_keyword_set.add(kw_lower)
                score += base_w
                hits = self._keyword_occurrences(combined_text, kw_lower)
                extra_hits = max(0, min(hits, self.max_hits_per_keyword) - 1)
                if extra_hits > 0:
                    repeat_bonus_total += extra_hits * (0.35 * base_w)
                continue

            # Partial token fallback for longer phrases
            tokens = self._keyword_tokens(kw_lower)
            if len(tokens) >= 3:
                hits = sum(1 for t in tokens if t in combined_text)
                if hits >= 2:
                    found_keywords.append(keyword)
                    found_keyword_set.add(kw_lower)
                    score += base_w * 0.5

        score += min(repeat_bonus_total, self.max_total_repeat_bonus)
        breadth_bonus = min(
            max(0, len(found_keyword_set) - 1) * self.unique_keyword_bonus,
            self.max_unique_keyword_bonus
        )
        score += breadth_bonus
        score += self._combo_bonus(combined_text)

        return score, sorted(set(found_keywords))
    
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
                            keywords_found=keywords,
                            candidate_source="rss"
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
    
    def is_relevant_url(self, url: str, publication: str = "") -> bool:
        """URL filtering; luxury additionally requires keyword-in-URL."""
        url_lower = url.lower().rstrip('/')
        parsed = urlparse(url_lower)
        host = parsed.netloc or ""

        # Exclude registration/event hosts and known non-editorial subdomains.
        blocked_host_terms = [
            "register.",
            "events.",
            "conference.",
            "webinars.",
        ]
        if any(term in host for term in blocked_host_terms):
            return False

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
            '/podcasts/', '/gallery/', '/galleries/', '/live/', '/live-blog/', '/events/',
            '/newsletter', '/subscribe', '/privacy', '/terms',
            '/register', '/registration', '/roundtable', '/conference', '/summit',
            '/webinar', '/event-registration'
        ]
        if any(term in url_lower for term in exclude_terms):
            return False

        source_specific_excludes = {
            "Institutional Investor": [
                "/roundtable", "/events", "/awards", "/membership", "/subscribe"
            ],
            "BBC": [
                "/live/", "/av/", "/iplayer", "/sounds", "/sport/",
                "/news/topics/", "/newsround/", "/reel/", "/worklife/"
            ],
            "The Telegraph": [
                "/video/", "/podcast/", "/newsletters/", "/opinion/",
                "/business/live/", "/football/", "/rugby/", "/travel/"
            ],
            "Evening Standard": [
                "/topic/", "/newsletters/", "/comment/", "/esmoney/",
                "/sport/", "/showbiz/", "/culture/"
            ],
        }

        if publication in source_specific_excludes:
            if any(term in url_lower for term in source_specific_excludes[publication]):
                return False

        # For luxury, require at least one active keyword in URL.
        if self.topic == "luxury":
            return any(keyword.lower() in url_lower for keyword in self.active_keywords)

        return True

    def is_valid_fetch_url(self, url: str) -> bool:
        """Validate URL before HTTP fetch to avoid curl parser failures."""
        if not url or not isinstance(url, str):
            return False

        u = url.strip()
        if not u:
            return False

        try:
            parsed = urlparse(u)
        except Exception:
            return False

        if parsed.scheme not in {"http", "https"}:
            return False
        if not parsed.netloc:
            return False

        # Validate port if present in netloc (avoid curl(3) malformed port issues)
        host_port = parsed.netloc.rsplit("@", 1)[-1]
        if ":" in host_port:
            host, port_str = host_port.rsplit(":", 1)
            # Allow IPv6-in-brackets with optional port; skip strict parsing here
            if not host.startswith("["):
                if not port_str.isdigit():
                    return False
                p = int(port_str)
                if p < 1 or p > 65535:
                    return False

        return True

    def sanitize_candidate_url(self, url: str) -> str:
        """Normalize candidate URL before validation/fetch."""
        if not url or not isinstance(url, str):
            return ""

        # Remove control chars, trim, and collapse accidental whitespace.
        u = re.sub(r"[\x00-\x1f\x7f]", "", url).strip()
        u = re.sub(r"\s+", "", u)

        return u

    def _parse_xml_with_cleanup(self, raw_bytes: bytes):
        """Parse XML robustly when hosts prepend junk/whitespace before XML."""
        if not raw_bytes:
            raise ET.ParseError("empty response")

        text = None
        for enc in ("utf-8", "utf-8-sig", "iso-8859-1"):
            try:
                text = raw_bytes.decode(enc, errors="strict")
                break
            except Exception:
                text = None

        if text is None:
            text = raw_bytes.decode("utf-8", errors="replace")

        text = text.lstrip("\ufeff\r\n\t ")

        xml_decl_pos = text.find("<?xml")
        root_pos = text.find("<urlset")
        smi_pos = text.find("<sitemapindex")
        starts = [p for p in (xml_decl_pos, root_pos, smi_pos) if p != -1]
        if starts:
            text = text[min(starts):]

        return ET.fromstring(text)
    
    def fetch_urls_from_sitemap(self, sitemap_url: str) -> List[tuple]:
        urls = []
        try:
            response = self.make_request(sitemap_url, timeout=10)
            if response.status_code == 200:
                root = self._parse_xml_with_cleanup(response.content)
                for url_elem in root:
                    loc_elem = url_elem.find('.//{http://www.sitemaps.org/schemas/sitemap/0.9}loc')
                    lastmod_elem = url_elem.find('.//{http://www.sitemaps.org/schemas/sitemap/0.9}lastmod')
                    
                    if loc_elem is not None:
                        url = (loc_elem.text or "").strip()
                        if url and not url.lower().startswith(("http://", "https://")):
                            url = urljoin(sitemap_url, url)
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
            
            try:
                root = self._parse_xml_with_cleanup(response.content)
            except Exception:
                try:
                    import gzip
                    decompressed = gzip.decompress(response.content)
                    root = self._parse_xml_with_cleanup(decompressed)
                except Exception:
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
                max_child_sitemaps = 17
                for sub_sitemap_url, _ in sitemap_entries[:max_child_sitemaps]:
                    urls.extend(self.fetch_urls_from_sitemap(sub_sitemap_url))
            
            elif 'urlset' in root.tag:
                for url_elem in root:
                    loc_elem = url_elem.find('.//{http://www.sitemaps.org/schemas/sitemap/0.9}loc')
                    lastmod_elem = url_elem.find('.//{http://www.sitemaps.org/schemas/sitemap/0.9}lastmod')
                    
                    if loc_elem is not None:
                        url = (loc_elem.text or "").strip()
                        if url and not url.lower().startswith(("http://", "https://")):
                            base = self.target_sources.get(publication, {}).get("base_url", "")
                            if base:
                                url = urljoin(base, url)
                        
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
                    if self.is_relevant_url(url, publication=publication):
                        candidate = ArticleCandidate(
                            title="",
                            url=url,
                            publication=publication,
                            published_date=pub_date,
                            summary="",
                            relevance_score=1.0,
                            candidate_source="sitemap"
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
            candidate.url = self.sanitize_candidate_url(candidate.url)

            if not self.is_valid_fetch_url(candidate.url):
                self.current_source_curl3_count = getattr(self, "current_source_curl3_count", 0) + 1
                self.source_fail_counts["invalid_url"] += 1
                if candidate.publication not in self.logged_bad_url_sources:
                    print(f"  Invalid URL format sample ({candidate.publication}): {candidate.url}")
                    self.logged_bad_url_sources.add(candidate.publication)
                return None

            response = self.make_request(candidate.url, timeout=20)
            
            if response.status_code != 200:
                if response.status_code == 401:
                    self.current_source_401_count = getattr(self, "current_source_401_count", 0) + 1
                    self.source_fail_counts["http_401"] += 1
                if response.status_code == 403:
                    self.current_source_403_count = getattr(self, "current_source_403_count", 0) + 1
                    self.source_fail_counts["http_403"] += 1
                if response.status_code == 429:
                    self.source_fail_counts["http_429"] += 1
                # Fallback for blocked pages (common on premium domains):
                # keep RSS metadata-only candidates when they are already relevant.
                if response.status_code in {401, 403, 429} and candidate.summary:
                    if not candidate.full_content:
                        candidate.full_content = candidate.summary
                    if not candidate.title:
                        candidate.title = candidate.url
                    # Require minimum relevance before accepting metadata-only fallback.
                    if candidate.relevance_score >= 1.0:
                        return candidate
                return None
            
            article = Article(candidate.url)
            article.download_state = 2
            article.html = response.text
            article.parse()
            
            if not article.text or len(article.text) < 150:
                self.source_fail_counts["short_text"] += 1
                return None

            # Reject obvious placeholder/template pages that poison summaries
            text_lower = article.text.lower()
            placeholder_markers = [
                "lorem ipsum dolor sit amet",
                "consectetur adipiscing elit",
                "donec neque eros",
                "in accumsan, ex a ultrices bibendum",
            ]
            marker_hits = sum(1 for m in placeholder_markers if m in text_lower)

            # If multiple placeholder markers are present, treat as invalid extraction
            if marker_hits >= 2:
                self.current_source_placeholder_skip_count = getattr(
                    self, "current_source_placeholder_skip_count", 0
                ) + 1
                self.source_fail_counts["placeholder"] += 1
                print(f"  Skipping placeholder/template content: {candidate.publication}")
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
                self.source_fail_counts["low_score"] += 1
                return None
            
        except Exception as e:
            error_msg = str(e)
            if 'curl: (3)' in error_msg or 'Port number was not a decimal number' in error_msg:
                self.current_source_curl3_count = getattr(self, "current_source_curl3_count", 0) + 1
                if candidate.publication not in self.logged_bad_url_sources:
                    print(f"  curl(3) sample bad URL ({candidate.publication}): {candidate.url}")
                    self.logged_bad_url_sources.add(candidate.publication)
            self.source_fail_counts["other_error"] += 1
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
        """Collect top articles per publication with dynamic caps."""
        cap_label = "Dynamic cap per publication" if self.use_dynamic_caps else "Fixed cap per publication"
        print(f"Weekly Article Collection ({cap_label})")
        print("=" * 60)
        baseline_max_articles = 5
        
        sources_to_use = sources_subset if sources_subset else list(self.target_sources.keys())
        print(f"Targeting {len(sources_to_use)} publications\n")
        
        all_articles = []
        
        for publication in sources_to_use:
            if publication not in self.target_sources:
                continue
                
            print(f"{publication}:")
            self.requests_per_source = 0
            self.current_source_401_count = 0
            self.current_source_403_count = 0
            self.current_source_placeholder_skip_count = 0
            self.current_source_curl3_count = 0
            self.source_fail_counts = {
                "invalid_url": 0,
                "http_401": 0,
                "http_403": 0,
                "http_429": 0,
                "short_text": 0,
                "placeholder": 0,
                "low_score": 0,
                "other_error": 0,
            }
            source_info = self.target_sources[publication]
            
            # Initial collection attempt
            candidates = self.collect_from_source(publication, source_info)
            
            if not candidates:
                print(f"  No candidates found\n")
                time.sleep(random.uniform(3, 6))
                continue
            
            source_priority = {"sitemap": 0, "rss": 1}
            candidates.sort(
                key=lambda x: (
                    source_priority.get(x.candidate_source, 99),
                    -x.relevance_score
                )
            )
            max_articles_per_publication = baseline_max_articles
            if self.use_dynamic_caps:
                print(
                    f"  Initial cap: {max_articles_per_publication} "
                    f"(baseline {baseline_max_articles}, probe first 5 extracted)"
                )
            else:
                print(f"  Fixed cap: {max_articles_per_publication}")
            
            # Extract full content and collect articles
            publication_articles = []
            max_tries = min(len(candidates), 20)
            probe_count = min(5, max_tries)

            # Pass 1: probe first 5 candidates with full-content scoring
            for candidate in candidates[:probe_count]:
                enhanced = self.extract_full_content(candidate)
                if enhanced:
                    publication_articles.append(enhanced)

                time.sleep(random.uniform(1, 2))

            # Recompute cap only when dynamic caps are enabled (finance pipeline).
            if self.use_dynamic_caps:
                dynamic_cap = self._dynamic_max_from_extracted(publication_articles, baseline_max_articles)
                if dynamic_cap > max_articles_per_publication:
                    print(f"  Raising cap based on extracted quality: {max_articles_per_publication} -> {dynamic_cap}")
                    max_articles_per_publication = dynamic_cap

            # Pass 2: continue until cap + buffer, then finalize by score.
            effective_buffer = self.post_cap_buffer if self.use_dynamic_caps else 0
            target_with_buffer = max_articles_per_publication + effective_buffer
            for candidate in candidates[probe_count:max_tries]:
                if len(publication_articles) >= target_with_buffer:
                    break
                if self.current_source_401_count >= self.max_401_per_source:
                    print(f"  Too many HTTP 401 responses ({self.current_source_401_count}) - skipping rest for {publication}")
                    break
                if self.current_source_403_count >= self.max_403_per_source:
                    print(f"  Too many HTTP 403 responses ({self.current_source_403_count}) - skipping rest for {publication}")
                    break
                if self.current_source_placeholder_skip_count >= self.max_placeholder_skips_per_source:
                    print(
                        f"  Too many placeholder/template skips "
                        f"({self.current_source_placeholder_skip_count}) - skipping rest for {publication}"
                    )
                    break
                if self.current_source_curl3_count >= self.max_curl3_per_source:
                    print(f"  Too many curl(3) URL errors ({self.current_source_curl3_count}) - skipping rest for {publication}")
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
                        
                        # Try extra RSS candidates up to cap + buffer.
                        effective_buffer = self.post_cap_buffer if self.use_dynamic_caps else 0
                        target_with_buffer = max_articles_per_publication + effective_buffer
                        for candidate in new_rss_candidates[:30]:
                            if len(publication_articles) >= target_with_buffer:
                                break
                            if self.current_source_401_count >= self.max_401_per_source:
                                print(f"  Too many HTTP 401 responses ({self.current_source_401_count}) - stopping RSS fallback for {publication}")
                                break
                            if self.current_source_403_count >= self.max_403_per_source:
                                print(f"  Too many HTTP 403 responses ({self.current_source_403_count}) - stopping RSS fallback for {publication}")
                                break
                            if self.current_source_placeholder_skip_count >= self.max_placeholder_skips_per_source:
                                print(
                                    f"  Too many placeholder/template skips "
                                    f"({self.current_source_placeholder_skip_count}) - stopping RSS fallback for {publication}"
                                )
                                break
                            if self.current_source_curl3_count >= self.max_curl3_per_source:
                                print(f"  Too many curl(3) URL errors ({self.current_source_curl3_count}) - stopping RSS fallback for {publication}")
                                break
                            
                            enhanced = self.extract_full_content(candidate)
                            if enhanced:
                                publication_articles.append(enhanced)
                            
                            time.sleep(random.uniform(1, 2))
                except Exception as e:
                    print(f"  RSS fallback error: {str(e)[:60]}")
            
            publication_articles.sort(key=lambda x: x.relevance_score, reverse=True)
            final_3 = publication_articles[:max_articles_per_publication]

            fc = self.source_fail_counts
            print(
                "  Fail reasons:"
                f" invalid={fc['invalid_url']}, 401={fc['http_401']}, 403={fc['http_403']},"
                f" 429={fc['http_429']}, short={fc['short_text']}, placeholder={fc['placeholder']},"
                f" low_score={fc['low_score']}, other={fc['other_error']}"
            )
            
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
    topic = os.getenv("TOPIC", "finance").strip().lower()
    print(f"{topic.title()} Article Collector")
    print("=" * 60)
    
    collector = CustomArticleCollector(topic=topic)
    
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


