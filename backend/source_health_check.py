"""
Source health checker for article discovery endpoints.

Usage:
  python source_health_check.py
  python source_health_check.py --topic finance
  python source_health_check.py --topic luxury --out output/source_health_luxury.csv
"""

from __future__ import annotations

import argparse
import csv
import os
from datetime import datetime
from typing import Dict, List, Tuple

import feedparser

from AgentCollector import CustomArticleCollector


def _classify_status(code: int | None, had_parse_error: bool, found: int) -> str:
    if had_parse_error:
        return "parse_error"
    if code is None:
        return "request_error"
    if code == 200 and found == 0:
        return "empty_or_filtered"
    if code == 200 and found > 0:
        return "ok"
    if code in (401, 403):
        return "blocked"
    if code == 404:
        return "not_found"
    if code == 429:
        return "rate_limited"
    if code >= 500:
        return "server_error"
    return f"http_{code}"


def _check_sitemap(collector: CustomArticleCollector, publication: str, sitemap_url: str) -> Dict[str, object]:
    result: Dict[str, object] = {
        "sitemap_status_code": None,
        "sitemap_found": 0,
        "sitemap_state": "not_configured",
        "sitemap_error": "",
    }
    if not sitemap_url:
        return result

    had_parse_error = False
    try:
        resp = collector.make_request(sitemap_url, timeout=15)
        code = getattr(resp, "status_code", None)
        result["sitemap_status_code"] = code
        if code == 200:
            try:
                candidates = collector.fetch_sitemap_articles(publication, sitemap_url)
                result["sitemap_found"] = len(candidates or [])
            except Exception as exc:  # noqa: BLE001
                had_parse_error = True
                result["sitemap_error"] = str(exc)[:200]
        else:
            result["sitemap_error"] = f"HTTP {code}"
    except Exception as exc:  # noqa: BLE001
        result["sitemap_error"] = str(exc)[:200]

    result["sitemap_state"] = _classify_status(
        result["sitemap_status_code"], had_parse_error, int(result["sitemap_found"])
    )
    return result


def _check_rss(collector: CustomArticleCollector, publication: str, rss_feeds: List[str]) -> Dict[str, object]:
    result: Dict[str, object] = {
        "rss_feeds_total": len(rss_feeds or []),
        "rss_feeds_ok": 0,
        "rss_entries_total": 0,
        "rss_candidates": 0,
        "rss_state": "not_configured",
        "rss_error": "",
    }
    if not rss_feeds:
        return result

    last_err = ""
    for feed_url in rss_feeds:
        try:
            resp = collector.make_request(feed_url, timeout=12)
            code = getattr(resp, "status_code", None)
            if code != 200:
                last_err = f"{feed_url} -> HTTP {code}"
                continue

            parsed = feedparser.parse(resp.content)
            entries = getattr(parsed, "entries", []) or []
            result["rss_feeds_ok"] += 1
            result["rss_entries_total"] += len(entries)

            # Reuse collector scoring path for realistic candidate count.
            cands = collector.try_rss_feed(publication, feed_url)
            result["rss_candidates"] += len(cands or [])
        except Exception as exc:  # noqa: BLE001
            last_err = f"{feed_url} -> {str(exc)[:120]}"

    if result["rss_feeds_ok"] > 0 and result["rss_candidates"] > 0:
        result["rss_state"] = "ok"
    elif result["rss_feeds_ok"] > 0 and result["rss_candidates"] == 0:
        result["rss_state"] = "empty_or_filtered"
    elif result["rss_feeds_ok"] == 0:
        result["rss_state"] = "blocked_or_unavailable"

    result["rss_error"] = last_err
    return result


def run_health_check(topic: str, output_path: str) -> Tuple[int, int]:
    collector = CustomArticleCollector(topic=topic)
    rows: List[Dict[str, object]] = []

    for publication, info in collector.target_sources.items():
        sitemap_url = info.get("sitemap_url")
        rss_feeds = info.get("rss_feeds") or []

        sitemap_info = _check_sitemap(collector, publication, sitemap_url)
        rss_info = _check_rss(collector, publication, rss_feeds)

        overall = "healthy"
        if (
            sitemap_info["sitemap_state"] not in {"ok", "not_configured"}
            and rss_info["rss_state"] not in {"ok", "not_configured"}
        ):
            overall = "needs_attention"

        rows.append(
            {
                "publication": publication,
                "topic": topic,
                "sitemap_url": sitemap_url or "",
                "sitemap_state": sitemap_info["sitemap_state"],
                "sitemap_status_code": sitemap_info["sitemap_status_code"] or "",
                "sitemap_found": sitemap_info["sitemap_found"],
                "sitemap_error": sitemap_info["sitemap_error"],
                "rss_feeds_total": rss_info["rss_feeds_total"],
                "rss_feeds_ok": rss_info["rss_feeds_ok"],
                "rss_entries_total": rss_info["rss_entries_total"],
                "rss_candidates": rss_info["rss_candidates"],
                "rss_state": rss_info["rss_state"],
                "rss_error": rss_info["rss_error"],
                "overall": overall,
                "checked_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            }
        )

    # Sort: attention first, then lowest candidate volume.
    rows.sort(key=lambda r: (r["overall"] == "healthy", int(r["sitemap_found"]) + int(r["rss_candidates"])))

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else [])
        writer.writeheader()
        writer.writerows(rows)

    total = len(rows)
    unhealthy = sum(1 for r in rows if r["overall"] == "needs_attention")
    return total, unhealthy


def main() -> None:
    parser = argparse.ArgumentParser(description="Check sitemap/RSS source health for collector sources.")
    parser.add_argument("--topic", choices=["finance", "luxury"], default="finance")
    parser.add_argument("--out", default="", help="Output CSV path")
    args = parser.parse_args()

    out_path = args.out or f"output/source_health_{args.topic}.csv"
    total, unhealthy = run_health_check(args.topic, out_path)
    print(f"Health check complete for topic={args.topic}")
    print(f"Sources checked: {total}")
    print(f"Needs attention: {unhealthy}")
    print(f"CSV report: {out_path}")


if __name__ == "__main__":
    main()

