from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from bertopic import BERTopic
from sentence_transformers import SentenceTransformer


BASE_STOPWORDS = {
    "article", "news", "report", "reports", "said", "says", "according",
    "week", "weeks", "today", "latest", "new", "media",
}
TOPIC_STOPWORDS = {
    "luxury": {"luxury", "brand", "brands", "market", "markets", "company", "companies", "group", "global", "industry"},
    "finance": {"finance", "financial", "bank", "banks", "market", "markets", "company", "companies", "group", "global", "industry", "investor", "investors"},
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


def _parse_yyyy_mm_dd(v: str) -> Optional[datetime]:
    if not v:
        return None
    try:
        return datetime.strptime(v.strip(), "%Y-%m-%d")
    except Exception:
        return None


def iso_week_key(dt: datetime) -> str:
    y, w, _ = dt.isocalendar()
    return f"{y}-W{w:02d}"


def week_sequence_desc(week_key: str, n_back: int = 4) -> List[str]:
    y, w = week_key.split("-W")
    y_i, w_i = int(y), int(w)
    d = datetime.fromisocalendar(y_i, w_i, 1)
    out = []
    for i in range(1, n_back + 1):
        prev = d.fromordinal(d.toordinal() - 7 * i)
        out.append(iso_week_key(prev))
    return out


def get_stopwords_for_topic(topic: str) -> set[str]:
    t = (topic or DEFAULT_TOPIC).strip().lower()
    return BASE_STOPWORDS | TOPIC_STOPWORDS.get(t, set())


def make_doc_text(article: Dict) -> str:
    return f"{article.get('title', '')}. {article.get('summary', '')}"


def _topic_signature(topic_words: List[tuple]) -> str:
    terms = [w for w, _ in (topic_words or [])[:5]]
    return " | ".join(terms) if terms else "unknown-topic"


def _topic_label(topic_words: List[tuple]) -> str:
    terms = [w for w, _ in (topic_words or [])[:3]]
    return " | ".join(terms) if terms else "Unknown Topic"


def compute_trends(
    articles: List[Dict],
    target_week_key: Optional[str] = None,
    topic: str = "luxury",
    min_mentions: int = 2,
    min_lift: float = 1.2,
    min_publications: int = 1,
    top_n: int = 25,
    window_start_date: str = "",
    window_end_date: str = "",
    baseline_weeks: int = 4,
) -> List[TrendRow]:
    baseline_weeks = max(1, int(baseline_weeks or 4))

    parsed_rows = []
    by_week: Dict[str, List[Dict]] = defaultdict(list)
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

    if not start_dt and not end_dt:
        now = datetime.now()
        start_dt = datetime(now.year, now.month, 1)
        end_dt = datetime(now.year + 1, 1, 1) if now.month == 12 else datetime(now.year, now.month + 1, 1)
    elif start_dt and not end_dt:
        end_dt = datetime(start_dt.year + 1, 1, 1) if start_dt.month == 12 else datetime(start_dt.year, start_dt.month + 1, 1)
    elif end_dt and not start_dt:
        start_dt = datetime(end_dt.year, end_dt.month, 1)
        end_dt = end_dt + timedelta(days=1)
    else:
        end_dt = end_dt + timedelta(days=1)

    current_rows = [(a, dt, wk) for a, dt, wk in parsed_rows if start_dt <= dt < end_dt]
    if not current_rows:
        return []

    label_week_key = target_week_key or iso_week_key(max(dt for _, dt, _ in current_rows))
    hist_weeks = set(week_sequence_desc(label_week_key, baseline_weeks))

    current_set = {(id(a), dt, wk) for a, dt, wk in current_rows}
    relevant_rows = [(a, dt, wk) for a, dt, wk in parsed_rows if (id(a), dt, wk) in current_set or wk in hist_weeks]
    docs = [make_doc_text(a) for a, _, _ in relevant_rows]
    stopwords = get_stopwords_for_topic(topic)

    print(f"[TREND_BERTOPIC] docs={len(docs)} current_docs={len(current_rows)} hist_weeks={len(hist_weeks)}")

    embedding_model = SentenceTransformer("all-MiniLM-L6-v2")
    topic_model = BERTopic(
        embedding_model=embedding_model,
        language="english",
        calculate_probabilities=False,
        min_topic_size=5,
        verbose=False,
    )

    topics, _ = topic_model.fit_transform(docs)

    topic_info = {}
    for tid in set(topics):
        if tid == -1:
            continue
        words = topic_model.get_topic(tid) or []
        words = [(w, s) for w, s in words if w not in stopwords]
        topic_info[tid] = {
            "signature": _topic_signature(words),
            "label": _topic_label(words),
        }

    current_counts = Counter()
    hist_counts_by_week = {wk: Counter() for wk in hist_weeks}
    current_pubsets = defaultdict(set)
    current_urls = defaultdict(list)

    for (a, dt, wk), tid in zip(relevant_rows, topics):
        if tid == -1 or tid not in topic_info:
            continue
        sig = topic_info[tid]["signature"]
        pub = (a.get("publication") or "").strip()
        url = (a.get("url") or "").strip()

        in_current = start_dt <= dt < end_dt
        if in_current:
            current_counts[sig] += 1
            if pub:
                current_pubsets[sig].add(pub)
            if url and len(current_urls[sig]) < 3:
                current_urls[sig].append(url)
        elif wk in hist_weeks:
            hist_counts_by_week[wk][sig] += 1

    rows: List[TrendRow] = []
    gate = {"seen": 0, "pass_mentions": 0, "pass_publications": 0, "pass_lift": 0}

    for sig, c in current_counts.items():
        gate["seen"] += 1
        if c < min_mentions:
            continue
        gate["pass_mentions"] += 1

        pub_count = len(current_pubsets[sig])
        if pub_count < min_publications:
            continue
        gate["pass_publications"] += 1

        hist_vals = [hist_counts_by_week[wk].get(sig, 0) for wk in hist_weeks]
        baseline = sum(hist_vals) / max(1, len(hist_vals))

        lift = (c + 1.0) / (baseline + 1.0)
        if lift < min_lift:
            continue
        gate["pass_lift"] += 1

        pct_change = ((c - baseline) / (baseline + 1.0)) * 100.0
        trend_score = lift * math.log1p(c)

        label = None
        for info in topic_info.values():
            if info["signature"] == sig:
                label = info["label"]
                break
        label = label or sig

        rows.append(
            TrendRow(
                week_key=label_week_key,
                keyword=label,
                count_current=c,
                baseline_4wk=round(baseline, 3),
                pct_change=round(pct_change, 2),
                trend_score=round(trend_score, 4),
                publication_count=pub_count,
                supporting_urls=" | ".join(current_urls[sig][:3]),
            )
        )

    rows.sort(key=lambda r: (r.trend_score, r.count_current), reverse=True)
    print(f"[TREND_BERTOPIC_GATES] {gate} final_rows={len(rows[:top_n])}")
    return rows[:top_n]
