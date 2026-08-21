from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import pycountry
import requests


COUNTRY_SHEET = "Publication Country Registry"
GOOGLE_FALLBACK_LIMIT = 20
COUNTRY_RESOLVER_VERSION = "v2"
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
    values = ws.get_all_values()
    if len(values) <= 1:
        return {}

    headers = values[0]
    registry = {}
    for row in values[1:]:
        record = {
            header: row[index] if index < len(row) else ""
            for index, header in enumerate(headers)
        }
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
    sources = [
        ("serpapi_knowledge_graph", data.get("knowledge_graph", {})),
        ("serpapi_ai_overview", data.get("ai_overview", {})),
        ("serpapi_answer_box", data.get("answer_box", {})),
    ]
    organic_text = " ".join(
        " ".join(
            str(item.get(field, ""))
            for field in ("title", "snippet", "source")
        )
        for item in data.get("organic_results", [])[:5]
    )
    sources.append(("serpapi_organic_results", organic_text))

    search_id = data.get("search_metadata", {}).get("id", "")
    for source, payload in sources:
        serialized = payload if isinstance(payload, str) else json.dumps(payload)
        country = country_from_text(serialized)
        if country:
            return {
                **country,
                "source": source,
                "confidence": "medium",
                "source_reference": search_id,
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
        write_record = True
        unresolved_is_fresh = False
        first = matching_rows[0]

        try:
            manual = (
                str((stored or {}).get("manual_override", "")).upper()
                == "TRUE"
            )
            retry_after = str((stored or {}).get("retry_after", "")).strip()
            unresolved_is_fresh = (
                bool(stored)
                and stored.get("status") == "unresolved"
                and stored.get("source_reference") == COUNTRY_RESOLVER_VERSION
                and retry_after
                and retry_after > today.strftime("%Y-%m-%d")
            )

            if manual:
                result = {
                    "country": stored.get("country", ""),
                    "country_code": stored.get("country_code", ""),
                    "source": "manual",
                    "confidence": "high",
                }
                used_registry = True
            else:
                metadata_hint = next(
                    (
                        row.get("_country_hint")
                        for row in matching_rows
                        if row.get("_country_hint")
                    ),
                    None,
                )
                publication_hint = next(
                    (
                        row.get("_publication_hint")
                        for row in matching_rows
                        if row.get("_publication_hint")
                    ),
                    "",
                )
                label_country = country_from_text(publication_hint)
                if label_country:
                    label_country.update({
                        "source": "search_source_label",
                        "confidence": "medium",
                        "source_reference": publication_hint,
                    })

                result = (
                    country_from_url(first.get("article_url", ""))
                    or metadata_hint
                    or label_country
                )
                if result is None and stored and stored.get("country"):
                    result = {
                        "country": stored.get("country", ""),
                        "country_code": stored.get("country_code", ""),
                        "source": stored.get("source", "registry"),
                        "confidence": stored.get("confidence", ""),
                    }
                    used_registry = True

            if result is None and not unresolved_is_fresh:
                result = lookup_wikidata_country(first.get("domain", ""))

            if (
                result is None
                and not unresolved_is_fresh
                and google_searches < google_limit
                and serpapi_api_key
            ):
                google_searches += 1
                result = lookup_google_country(
                    first.get("publication", ""),
                    key,
                    serpapi_api_key,
                    google_domain,
                    gl,
                    hl,
                )

        except Exception as exc:
            print(
                "[COUNTRY_LOOKUP] "
                f"key={key!r} status=failed "
                f"error={type(exc).__name__}: {exc}"
            )
            result = None
            write_record = False

        unresolved = result is None
        if unresolved:
            result = {
                "country": "",
                "country_code": "",
                "source": "unresolved",
                "confidence": "low",
                "source_reference": COUNTRY_RESOLVER_VERSION,
            }

        if not used_registry and write_record and not unresolved_is_fresh:
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
            row.pop("_publication_hint", None)

        print(json.dumps({
            "event": "COUNTRY_LOOKUP",
            "lookup_key": key,
            "country": result["country"],
            "source": result["source"],
            "cached": used_registry,
            "unresolved_cache_hit": unresolved_is_fresh,
        }, sort_keys=True))

    if new_records:
        ws.append_rows(new_records, value_input_option="RAW")
    return {
        "publications_checked": len(grouped),
        "google_searches_used": google_searches,
        "new_registry_rows": len(new_records),
    }
