from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

from sklearn.feature_extraction.text import TfidfVectorizer


WORD_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9\-]{1,}")

BASE_STOPWORDS = {
    "article", "news", "report", "reports", "said", "says", "according",
    "week", "weeks", "today", "latest", "new", "media",
}

TOPIC_STOPWORDS = {
    "luxury": {
        "luxury", "brand", "brands", "market", "markets", "company", "companies",
        "group", "global", "industry",
    },
    "finance": {
        "finance", "financial", "bank", "banks", "market", "markets", "company",
        "companies", "group", "global", "industry", "investor", "investors",
    },
}

DEFAULT_TOPIC = "luxury"


@dataclass
class TrendRow:
    week_key: str
    keyword: str
    count_current: int
    baseline_4wk: float
    pct_change: float
    trend_score: float
    publication_count: int
    supporting_urls: str
    status: str = "trending"


def iso_week_key(dt: datetime) -> str:
    y, w, _ = dt.isocalendar()
    return f"{y}-W{w:02d}"


def parse_dt(s: str) -> Optional[datetime]:
    if not s:
        return None
    s = s.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%m/%d/%Y %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def _parse_extra_stopwords(raw: str) -> set[str]:
    if not raw:
        return set()
    return {x.strip().lower() for x in raw.split(",") if x.strip()}


def get_stopwords_for_topic(topic: str, extra_stopwords_csv: str = "") -> set[str]:
    t = (topic or DEFAULT_TOPIC).strip().lower()
    topic_words = TOPIC_STOPWORDS.get(t, set())
    return BASE_STOPWORDS | topic_words | _parse_extra_stopwords(extra_stopwords_csv)


def make_doc_text(article: Dict) -> str:
    title = article.get("title", "") or ""
    summary = article.get("summary", "") or ""
    publication = article.get("publication", "") or ""
    return f"{title}. {summary}. {publication}"


def extract_keywords_tfidf(
    texts: List[str],
    stopwords: set[str],
    top_k_per_doc: int = 8
) -> List[List[str]]:
    if not texts:
        return []
    vec = TfidfVectorizer(
        ngram_range=(1, 2),
        lowercase=True,
        token_pattern=r"(?u)\b[a-zA-Z][a-zA-Z0-9\-]{1,}\b",
        min_df=1,
        max_df=0.85,
        stop_words=sorted(stopwords),
    )
    mat = vec.fit_transform(texts)
    feats = vec.get_feature_names_out()

    out: List[List[str]] = []
    for i in range(mat.shape[0]):
        row = mat.getrow(i)
        if row.nnz == 0:
            out.append([])
            continue
        pairs = sorted(zip(row.indices, row.data), key=lambda x: x[1], reverse=True)[:top_k_per_doc]
        kws = []
        for idx, _score in pairs:
            kw = feats[idx].strip().lower()
            if kw in stopwords:
                continue
            kws.append(kw)
        out.append(kws)
    return out


def week_sequence_desc(week_key: str, n_back: int = 4) -> List[str]:
    y, w = week_key.split("-W")
    y_i, w_i = int(y), int(w)
    d = datetime.fromisocalendar(y_i, w_i, 1)
    out = []
    for i in range(1, n_back + 1):
        prev = d.fromordinal(d.toordinal() - 7 * i)
        out.append(iso_week_key(prev))
    return out


def compute_trends(
    articles: List[Dict],
    target_week_key: str,
    topic: str = "luxury",
    extra_stopwords_csv: str = "",
    min_mentions: int = 3,
    min_lift: float = 1.5,
    min_publications: int = 2,
    top_n: int = 20,
) -> List[TrendRow]:
    by_week: Dict[str, List[Dict]] = defaultdict(list)
    for a in articles:
        dt = parse_dt(str(a.get("collectedDate", "") or a.get("collected_date", "")))
        if not dt:
            continue
        by_week[iso_week_key(dt)].append(a)

    current = by_week.get(target_week_key, [])
    if not current:
        return []

    hist_weeks = set(week_sequence_desc(target_week_key, 4))
    relevant_articles = [a for wk, arr in by_week.items() for a in arr if wk == target_week_key or wk in hist_weeks]
    texts = [make_doc_text(a) for a in relevant_articles]
    stopwords = get_stopwords_for_topic(topic, extra_stopwords_csv)
    kws_per_doc = extract_keywords_tfidf(texts, stopwords=stopwords, top_k_per_doc=8)

    for a, kws in zip(relevant_articles, kws_per_doc):
        a["_kws"] = kws

    current_counts = Counter()
    hist_counts_by_week = {wk: Counter() for wk in hist_weeks}
    current_pubsets = defaultdict(set)
    current_urls = defaultdict(list)

    for a in relevant_articles:
        dt = parse_dt(str(a.get("collectedDate", "") or a.get("collected_date", "")))
        if not dt:
            continue
        wk = iso_week_key(dt)
        kws = list(dict.fromkeys(a.get("_kws", [])))
        pub = (a.get("publication") or "").strip()
        url = (a.get("url") or "").strip()

        if wk == target_week_key:
            for kw in kws:
                current_counts[kw] += 1
                if pub:
                    current_pubsets[kw].add(pub)
                if url and len(current_urls[kw]) < 3:
                    current_urls[kw].append(url)
        elif wk in hist_weeks:
            for kw in kws:
                hist_counts_by_week[wk][kw] += 1

    rows: List[TrendRow] = []
    for kw, c in current_counts.items():
        if c < min_mentions:
            continue

        pub_count = len(current_pubsets[kw])
        if pub_count < min_publications:
            continue

        hist_vals = [hist_counts_by_week[wk].get(kw, 0) for wk in hist_weeks]
        baseline = sum(hist_vals) / max(1, len(hist_vals))

        lift = (c + 1.0) / (baseline + 1.0)
        if lift < min_lift:
            continue

        pct_change = ((c - baseline) / (baseline + 1.0)) * 100.0
        trend_score = lift * math.log1p(c)

        rows.append(
            TrendRow(
                week_key=target_week_key,
                keyword=kw,
                count_current=c,
                baseline_4wk=round(baseline, 3),
                pct_change=round(pct_change, 2),
                trend_score=round(trend_score, 4),
                publication_count=pub_count,
                supporting_urls=" | ".join(current_urls[kw][:3]),
            )
        )

    rows.sort(key=lambda r: (r.trend_score, r.count_current), reverse=True)
    return rows[:top_n]
