from __future__ import annotations

import math
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from bertopic import BERTopic
from sentence_transformers import SentenceTransformer
from sklearn.feature_extraction.text import CountVectorizer


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


def _tlog(event: str, **kwargs):
    payload = " ".join(f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in kwargs.items())
    print(f"[TREND_BERTOPIC_{event}] {payload}")


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
    _tlog(
        "START",
        topic=topic,
        target_week_key=target_week_key,
        window_start_date=window_start_date or "-",
        window_end_date=window_end_date or "-",
        baseline_weeks=baseline_weeks,
        min_mentions=min_mentions,
        min_lift=min_lift,
        min_publications=min_publications,
        top_n=top_n,
    )
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
    _tlog(
        "PARSE_WINDOW",
        input_articles=len(articles),
        parsed_rows=len(parsed_rows),
        missing_date=(len(articles) - len(parsed_rows)),
        current_rows=len(current_rows),
        window_start=str(start_dt.date()),
        window_end_exclusive=str(end_dt.date()),
    )
    if not current_rows:
        return []

    sample_docs = []
    for a, dt, wk in current_rows[:8]:
        sample_docs.append({
            "wk": wk,
            "date": dt.strftime("%Y-%m-%d"),
            "publication": (a.get("publication") or "")[:80],
            "title": (a.get("title") or "")[:120],
            "summary_len": len((a.get("summary") or "").strip()),
        })
    _tlog("WINDOW_SAMPLE_DOCS", sample=sample_docs)

    label_week_key = target_week_key or iso_week_key(max(dt for _, dt, _ in current_rows))
    window_days = max(1, (end_dt - start_dt).days)
    history_slices = []
    for i in range(1, baseline_weeks + 1):
        h_end = start_dt - timedelta(days=window_days * (i - 1))
        h_start = start_dt - timedelta(days=window_days * i)
        history_slices.append((h_start, h_end))

    current_set = {(id(a), dt, wk) for a, dt, wk in current_rows}
    relevant_rows = []
    for a, dt, wk in parsed_rows:
        in_current = (id(a), dt, wk) in current_set
        in_hist = any(hs <= dt < he for hs, he in history_slices)
        if in_current or in_hist:
            relevant_rows.append((a, dt, wk))
    docs = [make_doc_text(a) for a, _, _ in relevant_rows]
    stopwords = get_stopwords_for_topic(topic)

    min_topic_size_cfg = 3 if len(docs) < 60 else 5
    _tlog(
        "MODEL_CFG",
        docs=len(docs),
        min_topic_size=min_topic_size_cfg,
        model="all-MiniLM-L6-v2",
        baseline_slices=len(history_slices),
        window_days=window_days,
    )

    embedding_model = SentenceTransformer("all-MiniLM-L6-v2")
    vectorizer = CountVectorizer(ngram_range=(1, 2), min_df=2, stop_words="english")
    topic_model = BERTopic(
        embedding_model=embedding_model,
        language="english",
        calculate_probabilities=False,
        min_topic_size=min_topic_size_cfg,
        vectorizer_model=vectorizer,
        top_n_words=8,
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
    outlier_docs = sum(1 for t in topics if t == -1)
    unique_topics = len(set(t for t in topics if t != -1))
    topic_sizes = Counter(topics)
    top_topic_sizes = topic_sizes.most_common(10)
    _tlog(
        "MODEL_OUT",
        total_docs=len(topics),
        outlier_docs=outlier_docs,
        unique_topics=unique_topics,
        kept_topics=len(topic_info),
        top_topic_sizes=top_topic_sizes,
    )
    outlier_ratio = (outlier_docs / len(topics)) if topics else 1.0
    if outlier_ratio > 0.75 and len(docs) >= 20:
        _tlog("OUTLIER_MITIGATION", action="retry_with_smaller_min_topic_size", outlier_ratio=round(outlier_ratio, 4))
        topic_model = BERTopic(
            embedding_model=embedding_model,
            language="english",
            calculate_probabilities=False,
            min_topic_size=2,
            vectorizer_model=vectorizer,
            top_n_words=8,
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
        outlier_docs = sum(1 for t in topics if t == -1)
        unique_topics = len(set(t for t in topics if t != -1))

    current_counts = Counter()
    hist_counts_by_slice = [Counter() for _ in history_slices]
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
        else:
            for i, (hs, he) in enumerate(history_slices):
                if hs <= dt < he:
                    hist_counts_by_slice[i][sig] += 1
                    break

    rows: List[TrendRow] = []
    gate = {"seen": 0, "pass_mentions": 0, "pass_publications": 0, "pass_lift": 0}
    reject_reasons = Counter()
    reject_examples = {"mentions": [], "publications": [], "lift": []}
    effective_min_mentions = min_mentions
    if len(current_counts) > 0 and outlier_docs / max(1, len(topics)) > 0.75:
        effective_min_mentions = max(1, min_mentions - 1)
        _tlog("THRESHOLD_ADAPT", min_mentions=min_mentions, effective_min_mentions=effective_min_mentions)

    for sig, c in current_counts.items():
        gate["seen"] += 1
        if c < effective_min_mentions:
            reject_reasons["mentions"] += 1
            if len(reject_examples["mentions"]) < 5:
                reject_examples["mentions"].append({"sig": sig, "count_current": c, "min_mentions": effective_min_mentions})
            continue
        gate["pass_mentions"] += 1

        pub_count = len(current_pubsets[sig])
        if pub_count < min_publications:
            reject_reasons["publications"] += 1
            if len(reject_examples["publications"]) < 5:
                reject_examples["publications"].append({"sig": sig, "publication_count": pub_count, "min_publications": min_publications})
            continue
        gate["pass_publications"] += 1

        hist_vals = [c.get(sig, 0) for c in hist_counts_by_slice]
        baseline = sum(hist_vals) / max(1, len(hist_vals))

        lift = (c + 1.0) / (baseline + 1.0)
        if lift < min_lift:
            reject_reasons["lift"] += 1
            if len(reject_examples["lift"]) < 5:
                reject_examples["lift"].append({"sig": sig, "count_current": c, "baseline": round(baseline, 4), "lift": round(lift, 4), "min_lift": min_lift})
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
    _tlog(
        "GATES",
        seen=gate["seen"],
        pass_mentions=gate["pass_mentions"],
        pass_publications=gate["pass_publications"],
        pass_lift=gate["pass_lift"],
        final_rows=min(len(rows), top_n),
    )
    _tlog("REJECT_REASONS", counts=dict(reject_reasons))
    _tlog("REJECT_EXAMPLES", examples=reject_examples)
    preview = [
        {
            "keyword": r.keyword,
            "count_current": r.count_current,
            "baseline_4wk": r.baseline_4wk,
            "trend_score": r.trend_score,
            "publication_count": r.publication_count,
        }
        for r in rows[:3]
    ]
    _tlog("TOP3", rows=preview)
    return rows[:top_n]
