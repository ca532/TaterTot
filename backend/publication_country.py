from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import pycountry
import requests


COUNTRY_SHEET = "Publication Country Registry"
GOOGLE_FALLBACK_LIMIT = 20
WIKIDATA_URL = "https://query.wikidata.org/sparql"
SERPAPI_URL = "https://serpapi.com/search.json"
COUNTRY_HEADERS = [
    "lookup_key",
    "publication",
    "country",
    "country_code",
    "source",
    "confidence",
    "manual_override",
    "status",
    "source_reference",
    "checked_at",
    "retry_after",
]
GENERIC_CCTLDS = {"ai", "cc", "co", "fm", "gg", "io", "ly", "me", "tv"}
CODE_ALIASES = {"UK": "GB", "USA": "US"}
SOCIAL_HOSTS = {"facebook.com", "instagram.com"}


def country_from_code(code: str) -> dict | None:
    normalized = CODE_ALIASES.get((code or "").upper(), (code or "").upper())
    country = pycountry.countries.get(alpha_2=normalized)
    if not country:
        return None
    return {"country": country.name, "country_code": country.alpha_2}


def country_from_text(text: str) -> dict | None:
    lowered = (text or "").lower()
    aliases = {
        "united states": "US",
        "u.s.": "US",
        "usa": "US",
        "united kingdom": "GB",
        "u.k.": "GB",
        "britain": "GB",
    }
    for phrase, code in aliases.items():
        if phrase in lowered:
            return country_from_code(code)

    countries = sorted(pycountry.countries, key=lambda item: len(item.name), reverse=True)
    for item in countries:
        names = {
            item.name,
            getattr(item, "official_name", ""),
            getattr(item, "common_name", ""),
        }
        for name in names:
            if name and re.search(rf"\b{re.escape(name.lower())}\b", lowered):
                return {"country": item.name, "country_code": item.alpha_2}
    return None


def publication_lookup_key(url: str) -> str:
    parsed = urlparse(url or "")
    host = parsed.netloc.lower().removeprefix("www.")
    if host == "m.facebook.com":
        host = "facebook.com"
    parts = [part.lower() for part in parsed.path.split("/") if part]

    if host in SOCIAL_HOSTS and parts:
        return f"{host}/{parts[0]}"

    if parts:
        match = re.fullmatch(r"(?:[a-z]{2}-)?([a-z]{2})", parts[0])
        if match and country_from_code(match.group(1)):
            return f"{host}/{parts[0]}"
    return host


def country_from_url(url: str) -> dict | None:
    key = publication_lookup_key(url)
    host, _, edition = key.partition("/")
    if host in SOCIAL_HOSTS:
        return None

    if edition:
        match = re.fullmatch(r"(?:[a-z]{2}-)?([a-z]{2})", edition)
        country = country_from_code(match.group(1)) if match else None
        if country:
            return {
                **country,
                "source": "url_edition",
                "confidence": "high",
                "source_reference": url,
            }

    suffix = host.rsplit(".", 1)[-1].lower()
    if suffix in GENERIC_CCTLDS:
        return None
    country = country_from_code(suffix)
    if not country:
        return None
    return {
        **country,
        "source": "country_domain",
        "confidence": "high",
        "source_reference": host,
    }


def extract_metadata_country(soup) -> dict | None:
    locale = ""
    node = soup.find("meta", attrs={"property": "og:locale"})
    if node:
        locale = str(node.get("content", ""))
    if not locale and soup.html:
        locale = str(soup.html.get("lang", ""))

    match = re.search(r"[-_]([A-Za-z]{2})$", locale)
    country = country_from_code(match.group(1)) if match else None
    if not country:
        return None
    return {
        **country,
        "source": "page_metadata",
        "confidence": "medium",
        "source_reference": locale,
    }


def ensure_country_sheet(db_or_spreadsheet):
    spreadsheet = getattr(
        db_or_spreadsheet,
        "spreadsheet",
        db_or_spreadsheet,
    )
    try:
        ws = spreadsheet.worksheet(COUNTRY_SHEET)
    except Exception:
        ws = spreadsheet.add_worksheet(
            title=COUNTRY_SHEET,
            rows=1000,
            cols=len(COUNTRY_HEADERS),
        )

    if ws.col_count < len(COUNTRY_HEADERS):
        ws.resize(cols=len(COUNTRY_HEADERS))
    values = ws.get_all_values()
    if not values:
        ws.append_row(COUNTRY_HEADERS)
    elif values[0] != COUNTRY_HEADERS:
        ws.update("A1:K1", [COUNTRY_HEADERS])
    return ws


def load_registry(ws) -> dict[str, dict]:
    registry = {}
    for record in ws.get_all_records():
        key = str(record.get("lookup_key", "")).strip().lower()
        if not key:
            continue
        existing = registry.get(key)
        manual = str(record.get("manual_override", "")).upper() == "TRUE"
        existing_manual = (
            str((existing or {}).get("manual_override", "")).upper() == "TRUE"
        )
        if not existing or manual or not existing_manual:
            registry[key] = record
    return registry


def lookup_wikidata_country(domain: str) -> dict | None:
    safe_domain = re.sub(r"[^a-z0-9.-]", "", (domain or "").lower())
    if not safe_domain:
        return None
    domain_pattern = safe_domain.replace(".", "[.]")
    query = f"""
    SELECT ?item ?countryLabel WHERE {{
      ?item wdt:P856 ?website.
      FILTER(REGEX(STR(?website), "^https?://(www[.])?{domain_pattern}(/|$)", "i"))
      OPTIONAL {{ ?item wdt:P17 ?directCountry. }}
      OPTIONAL {{
        ?item wdt:P159 ?headquarters.
        ?headquarters wdt:P17 ?headquartersCountry.
      }}
      BIND(COALESCE(?directCountry, ?headquartersCountry) AS ?country)
      FILTER(BOUND(?country))
      SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
    }}
    LIMIT 1
    """
    try:
        response = requests.get(
            WIKIDATA_URL,
            params={"query": query, "format": "json"},
            headers={"User-Agent": "ClaireAdlerCoverageBot/1.0"},
            timeout=30,
        )
        response.raise_for_status()
        bindings = response.json()["results"]["bindings"]
    except (requests.RequestException, KeyError, ValueError):
        return None
    if not bindings:
        return None

    country = country_from_text(bindings[0].get("countryLabel", {}).get("value", ""))
    if not country:
        return None
    return {
        **country,
        "source": "wikidata",
        "confidence": "high",
        "source_reference": bindings[0].get("item", {}).get("value", ""),
    }


def lookup_google_country(
    publication: str,
    lookup_key: str,
    api_key: str,
    google_domain: str,
    gl: str,
    hl: str,
) -> dict | None:
    if not api_key:
        return None
    response = requests.get(
        SERPAPI_URL,
        params={
            "engine": "google",
            "q": f'"{publication}" "{lookup_key}" country headquarters',
            "api_key": api_key,
            "google_domain": google_domain,
            "gl": gl,
            "hl": hl,
            "num": 10,
        },
        timeout=90,
    )
    response.raise_for_status()
    data = response.json()
    for source, payload in (
        ("serpapi_knowledge_graph", data.get("knowledge_graph", {})),
        ("serpapi_ai_overview", data.get("ai_overview", {})),
    ):
        country = country_from_text(json.dumps(payload))
        if country:
            return {
                **country,
                "source": source,
                "confidence": "medium",
                "source_reference": data.get("search_metadata", {}).get("id", ""),
            }
    return None


def enrich_publication_countries(
    rows: list[dict],
    db,
    serpapi_api_key: str,
    google_domain: str = "google.com",
    gl: str = "us",
    hl: str = "en",
    google_budget: int | None = None,
) -> dict:
    ws = ensure_country_sheet(db)
    registry = load_registry(ws)
    grouped = {}
    for row in rows:
        key = publication_lookup_key(row.get("article_url", ""))
        if key:
            grouped.setdefault(key, []).append(row)

    google_limit = min(
        GOOGLE_FALLBACK_LIMIT,
        max(0, google_budget) if google_budget is not None else GOOGLE_FALLBACK_LIMIT,
    )
    google_searches = 0
    new_records = []
    today = datetime.now(timezone.utc)

    for key, matching_rows in grouped.items():
        stored = registry.get(key)
        result = None
        used_registry = False
        if stored:
            retry_after = str(stored.get("retry_after", "")).strip()
            unresolved_is_fresh = (
                stored.get("status") == "unresolved"
                and retry_after
                and retry_after > today.strftime("%Y-%m-%d")
            )
            manual = str(stored.get("manual_override", "")).upper() == "TRUE"
            if stored.get("country") or unresolved_is_fresh or manual:
                result = {
                    "country": stored.get("country", ""),
                    "country_code": stored.get("country_code", ""),
                    "source": stored.get("source", "registry"),
                    "confidence": stored.get("confidence", ""),
                }
                used_registry = True

        first = matching_rows[0]
        if result is None:
            metadata_hint = next(
                (row.get("_country_hint") for row in matching_rows if row.get("_country_hint")),
                None,
            )
            result = (
                country_from_url(first.get("article_url", ""))
                or metadata_hint
                or lookup_wikidata_country(first.get("domain", ""))
            )

        if result is None and google_searches < google_limit and serpapi_api_key:
            try:
                result = lookup_google_country(
                    first.get("publication", ""),
                    key,
                    serpapi_api_key,
                    google_domain,
                    gl,
                    hl,
                )
                google_searches += 1
            except requests.RequestException:
                result = None

        unresolved = result is None
        if unresolved:
            result = {
                "country": "",
                "country_code": "",
                "source": "unresolved",
                "confidence": "low",
            }

        if not used_registry:
            new_records.append([
                key,
                first.get("publication", ""),
                result["country"],
                result["country_code"],
                result["source"],
                result["confidence"],
                "FALSE",
                "unresolved" if unresolved else "resolved",
                result.get("source_reference", ""),
                today.strftime("%Y-%m-%d"),
                (today + timedelta(days=30)).strftime("%Y-%m-%d") if unresolved else "",
            ])

        for row in matching_rows:
            row["country"] = result["country"]
            row["country_source"] = result["source"]
            row["country_confidence"] = result["confidence"]
            row["country_lookup_key"] = key
            row.pop("_country_hint", None)

    if new_records:
        ws.append_rows(new_records, value_input_option="USER_ENTERED")
    return {
        "publications_checked": len(grouped),
        "google_searches_used": google_searches,
        "new_registry_rows": len(new_records),
    }
