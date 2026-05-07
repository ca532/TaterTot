from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
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


def _parse_yyyy_mm_dd(v: str) -> Optional[datetime]:
    if not v:
        return None
    try:
        return datetime.strptime(v.strip(), "%Y-%m-%d")
    except Exception:
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


def get_stopwords_for_topic(topic: str) -> set[str]:
    t = (topic or DEFAULT_TOPIC).strip().lower()
    topic_words = TOPIC_STOPWORDS.get(t, set())
    return BASE_STOPWORDS | topic_words


def make_doc_text(article: Dict) -> str:
    title = article.get("title", "") or ""
    summary = article.get("summary", "") or ""
    publication = article.get("publication", "") or ""
    return f"{title}. {summary}. {publication}"


def extract_keywords_tfidf(
    texts: List[str],
    stopwords: set[str],
    top_k_per_doc: int = 12
) -> List[List[str]]:
    if not texts:
        return []
    vec = TfidfVectorizer(
        ngram_range=(1, 2),
        lowercase=True,
        token_pattern=r"(?u)\b[a-zA-Z][a-zA-Z0-9\-]{1,}\b",
        min_df=1,
        max_df=0.95,
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
    target_week_key: Optional[str] = None,
    topic: str = "luxury",
    min_mentions: int = 3,
    min_lift: float = 1.5,
    min_publications: int = 2,
    top_n: int = 20,
    window_start_date: str = "",
    window_end_date: str = "",
    baseline_weeks: int = 4,
) -> List[TrendRow]:
    baseline_weeks = max(1, int(baseline_weeks or 4))
    by_week: Dict[str, List[Dict]] = defaultdict(list)
    parsed_rows = []
    for a in articles:
        dt = parse_dt(str(a.get("collectedDate", "") or a.get("collected_date", "")))
        if not dt:
            continue
        wk = iso_week_key(dt)
        by_week[wk].append(a)
        parsed_rows.append((a, dt, wk))

    if not parsed_rows:
        return []

    start_dt = _parse_yyyy_mm_dd(window_start_date)
    end_dt = _parse_yyyy_mm_dd(window_end_date)

    # Default window: current calendar month [month_start, next_month_start)
    if not start_dt and not end_dt:
        now = datetime.now()
        start_dt = datetime(now.year, now.month, 1)
        if now.month == 12:
            end_dt = datetime(now.year + 1, 1, 1)
        else:
            end_dt = datetime(now.year, now.month + 1, 1)
    elif start_dt and not end_dt:
        # Inclusive end of provided start month -> exclusive next month start
        if start_dt.month == 12:
            end_dt = datetime(start_dt.year + 1, 1, 1)
        else:
            end_dt = datetime(start_dt.year, start_dt.month + 1, 1)
    elif end_dt and not start_dt:
        start_dt = datetime(end_dt.year, end_dt.month, 1)
        end_dt = end_dt + timedelta(days=1)
    else:
        # include user-selected end date by converting to exclusive bound (+1 day)
        end_dt = end_dt + timedelta(days=1)

    current_articles = []
    for a, dt, wk in parsed_rows:
        if start_dt <= dt < end_dt:
            current_articles.append((a, dt, wk))

    if not current_articles:
        return []
    effective_min_publications = min_publications
    window_days = (end_dt - start_dt).days
    if window_days >= 28 and effective_min_publications > 1:
        effective_min_publications = 1
    print(
        "[TREND_WINDOW] "
        f"start={start_dt.strftime('%Y-%m-%d')} end_exclusive={end_dt.strftime('%Y-%m-%d')} "
        f"current_articles={len(current_articles)} baseline_weeks={baseline_weeks} "
        f"effective_min_publications={effective_min_publications}"
    )

    if target_week_key:
        label_week_key = target_week_key
    else:
        latest_dt = max(dt for _, dt, _ in current_articles)
        label_week_key = iso_week_key(latest_dt)

    hist_weeks = set(week_sequence_desc(label_week_key, baseline_weeks))
    current_article_set = {id(a) for a, _, _ in current_articles}
    relevant_articles = [
        a for wk, arr in by_week.items()
        for a in arr
        if id(a) in current_article_set or wk in hist_weeks
    ]
    texts = [make_doc_text(a) for a in relevant_articles]
    stopwords = get_stopwords_for_topic(topic)
    kws_per_doc = extract_keywords_tfidf(texts, stopwords=stopwords, top_k_per_doc=8)

    for a, kws in zip(relevant_articles, kws_per_doc):
        a["_kws"] = kws
    total_kw = sum(len(a.get("_kws", [])) for a in relevant_articles)
    print(f"[TREND_KEYWORDS] relevant_articles={len(relevant_articles)} extracted_keywords_total={total_kw}")

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

        in_current_window = id(a) in current_article_set
        if in_current_window:
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
    gate = {"seen": 0, "pass_mentions": 0, "pass_publications": 0, "pass_lift": 0}
    for kw, c in current_counts.items():
        gate["seen"] += 1
        if c < min_mentions:
            continue
        gate["pass_mentions"] += 1

        pub_count = len(current_pubsets[kw])
        if pub_count < effective_min_publications:
            continue
        gate["pass_publications"] += 1

        hist_vals = [hist_counts_by_week[wk].get(kw, 0) for wk in hist_weeks]
        baseline = sum(hist_vals) / max(1, len(hist_vals))

        lift = (c + 1.0) / (baseline + 1.0)
        if lift < min_lift:
            continue
        gate["pass_lift"] += 1

        pct_change = ((c - baseline) / (baseline + 1.0)) * 100.0
        trend_score = lift * math.log1p(c)

        rows.append(
            TrendRow(
                week_key=label_week_key,
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
    print(f"[TREND_GATES] {gate} final_rows={len(rows[:top_n])}")
    return rows[:top_n]
