from __future__ import annotations

import asyncio
import os
import random
import re
from urllib.parse import unquote, urlparse

from crawlee import ConcurrencySettings
from crawlee.crawlers import PlaywrightCrawler, PlaywrightCrawlingContext


HYPESTAT_BASE_URL = "https://hypestat.com/info"
HYPESTAT_MIN_DELAY_SECONDS = float(os.getenv("HYPESTAT_MIN_DELAY_SECONDS", "2"))
HYPESTAT_MAX_DELAY_SECONDS = float(os.getenv("HYPESTAT_MAX_DELAY_SECONDS", "4"))


def domain_from_url(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = f"https://{raw}"
    host = urlparse(raw).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def domain_from_hypestat_url(url: str) -> str:
    path = unquote(urlparse(url).path)
    marker = "/info/"
    if marker not in path:
        return ""
    return domain_from_url(path.split(marker, 1)[1].strip("/"))


def parse_visit_count(value: str) -> int | None:
    raw = (value or "").replace(",", "").strip().upper()
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*([KMB]?)", raw)
    if not match:
        return None

    number = float(match.group(1))
    multipliers = {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}
    return int(number * multipliers[match.group(2)])


def format_visits(value: int | None) -> str:
    if value is None:
        return "N/A"
    if value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.1f}B"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return str(value)


def extract_monthly_visits(page_text: str) -> int | None:
    patterns = (
        r"\bMonthly Visits\s*:?\s*([0-9][0-9,.]*\s*[KMB]?)\b",
        r"\babout\s+([0-9][0-9,.]*\s*[KMB]?)\s+monthly visitors\b",
    )
    for pattern in patterns:
        match = re.search(pattern, page_text or "", flags=re.IGNORECASE)
        if match:
            visits = parse_visit_count(match.group(1))
            if visits is not None:
                return visits
    return None


def default_result(error: str) -> dict:
    return {
        "monthly_visits": None,
        "monthly_visits_display": "N/A",
        "source": "hypestat",
        "error": error,
    }


async def _crawl_hypestat(domains: list[str]) -> dict[str, dict]:
    results: dict[str, dict] = {}
    crawler = PlaywrightCrawler(
        headless=True,
        browser_type="chromium",
        browser_launch_options={
            "chromium_sandbox": False,
        },
        concurrency_settings=ConcurrencySettings(
            desired_concurrency=1,
            max_concurrency=1,
        ),
        max_request_retries=2,
        max_requests_per_crawl=len(domains),
    )

    @crawler.router.default_handler
    async def request_handler(context: PlaywrightCrawlingContext) -> None:
        domain = domain_from_hypestat_url(context.request.url)
        try:
            body_text = await context.page.locator("body").inner_text(timeout=20_000)
            lowered = body_text.lower()
            blocked_markers = (
                "verify you are human",
                "access denied",
                "captcha",
                "checking your browser",
                "cloudflare ray id",
            )
            if any(marker in lowered for marker in blocked_markers):
                raise RuntimeError("hypestat_blocked")

            visits = extract_monthly_visits(body_text)
            results[domain] = {
                "monthly_visits": visits,
                "monthly_visits_display": format_visits(visits),
                "source": "hypestat",
                "error": "" if visits is not None else "not_found",
            }
        finally:
            delay = random.uniform(
                HYPESTAT_MIN_DELAY_SECONDS,
                HYPESTAT_MAX_DELAY_SECONDS,
            )
            await asyncio.sleep(delay)

    await crawler.run([f"{HYPESTAT_BASE_URL}/{domain}" for domain in domains])

    for domain in domains:
        results.setdefault(domain, default_result("crawl_failed"))
    return results


def lookup_hypestat_monthly_visits(domains: list[str]) -> dict[str, dict]:
    targets = sorted({
        normalized
        for domain in domains
        if (normalized := domain_from_url(domain))
    })
    if not targets:
        return {}

    try:
        return asyncio.run(_crawl_hypestat(targets))
    except Exception as exc:
        error = type(exc).__name__
        return {domain: default_result(error) for domain in targets}
