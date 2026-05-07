import os
import time
import requests
import feedparser
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse
from backend.google_storage import GoogleSheetsDB

SOURCE_SHEET = os.getenv("SOURCE_CONFIG_SHEET", "Source Lists")
DETAIL_SHEET = os.getenv("SOURCE_REPORT_DETAIL_SHEET", "Source Validation Details")
TIMEOUT = int(os.getenv("SOURCE_VALIDATE_TIMEOUT", "20"))
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; RRD-MetadataBot/1.0)"}


def now_utc():
    return datetime.now(timezone.utc).isoformat()


def host_of(u: str) -> str:
    try:
        h = urlparse(u).netloc.lower()
        return h[4:] if h.startswith("www.") else h
    except Exception:
        return ""


def fetch_text(url: str):
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
        return r.status_code, r.url, r.text
    except Exception as e:
        return 0, url, str(e)


def discover_sitemap_candidates(base_url: str):
    robots_url = urljoin(base_url.rstrip("/") + "/", "robots.txt")
    candidates = []

    status, _, body = fetch_text(robots_url)
    if status == 200 and body:
        for line in body.splitlines():
            line = line.strip()
            if line.lower().startswith("sitemap:"):
                sm = line.split(":", 1)[1].strip()
                if sm:
                    candidates.append(sm)

    commons = ["/sitemap.xml", "/sitemap_index.xml", "/wp-sitemap.xml", "/news-sitemap.xml"]
    for p in commons:
        candidates.append(urljoin(base_url.rstrip("/") + "/", p.lstrip("/")))

    seen, out = set(), []
    for c in candidates:
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def validate_sitemap(url: str, base_host: str):
    status, final_url, body = fetch_text(url)
    if status != 200:
        return False, 0, f"http_{status}", final_url
    if "<html" in body[:1000].lower():
        return False, 0, "html_not_xml", final_url

    try:
        root = ET.fromstring(body.encode("utf-8", errors="ignore"))
        tag = root.tag.lower()
        if not ("urlset" in tag or "sitemapindex" in tag):
            return False, 0, "not_sitemap_xml", final_url
        count = body.lower().count("<url>") + body.lower().count("<sitemap>")
        score = count * 2 + (20 if host_of(final_url) == base_host else 0)
        return True, score, "ok", final_url
    except Exception:
        return False, 0, "xml_parse_error", final_url


def validate_rss(url: str, base_host: str):
    if not url:
        return False, "missing", ""
    status, final_url, body = fetch_text(url)
    if status != 200:
        return False, f"http_{status}", final_url

    fp = feedparser.parse(body)
    if fp.bozo and not fp.entries:
        return False, "feed_parse_error", final_url
    if not fp.entries:
        return False, "no_entries", final_url
    return True, "ok", final_url


def pick_best_sitemap(candidates, base_host):
    best = {"valid": False, "score": -1, "reason": "no_candidates", "url": ""}
    for c in candidates:
        ok, score, reason, final_url = validate_sitemap(c, base_host)
        if ok and score > best["score"]:
            best = {"valid": True, "score": score, "reason": reason, "url": final_url or c}
        elif not best["valid"] and best["score"] < 0:
            best = {"valid": False, "score": score, "reason": reason, "url": final_url or c}
    return best


def ensure_headers(ws, headers):
    vals = ws.get_all_values()
    if not vals:
        ws.append_row(headers)
        return
    if vals[0] != headers:
        ws.update("A1", [headers])


def run_publication_metadata_pipeline(list_name: str, progress_callback=None):
    def emit(phase: str, current: int, total_count: int, message: str):
        if progress_callback:
            try:
                progress_callback(phase, current, total_count, message)
            except Exception:
                pass

    db = GoogleSheetsDB()
    ss = db.spreadsheet

    src_ws = ss.worksheet(SOURCE_SHEET)
    detail_ws = ss.worksheet(DETAIL_SHEET)

    ensure_headers(src_ws, ["list_name", "publication", "base_url", "sitemap_url", "rss_url", "active", "date_added"])
    ensure_headers(detail_ws, [
        "run_id", "list_name", "publication", "base_url",
        "sitemap_url", "sitemap_valid", "sitemap_reason",
        "rss_url", "rss_valid", "rss_reason",
        "active_after", "created_at"
    ])

    all_vals = src_ws.get_all_values()
    header = all_vals[0]
    idx = {h: i for i, h in enumerate(header)}
    run_id = f"meta-{int(time.time())}"

    total = valid_sitemap = valid_rss = both_valid = neither_valid = 0
    scoped_rows = []
    for row_num in range(2, len(all_vals) + 1):
        row = all_vals[row_num - 1]
        rec = {h: (row[idx[h]] if idx[h] < len(row) else "") for h in idx}
        if str(rec.get("list_name", "")).strip() != list_name:
            continue
        if str(rec.get("active", "TRUE")).upper() != "TRUE":
            continue
        scoped_rows.append((row_num, rec))

    emit("initializing", 0, len(scoped_rows), f"Loaded {len(scoped_rows)} active sources")

    for i, (row_num, rec) in enumerate(scoped_rows, start=1):
        total += 1
        pub = str(rec.get("publication", "")).strip()
        base_url = str(rec.get("base_url", "")).strip()
        rss_url = str(rec.get("rss_url", "")).strip()
        bhost = host_of(base_url)
        emit("validating", i, len(scoped_rows), f"Validating {pub or base_url}")

        sm_best = pick_best_sitemap(discover_sitemap_candidates(base_url), bhost)
        rss_ok, rss_reason, rss_final = validate_rss(rss_url, bhost)

        sm_ok = bool(sm_best["valid"])
        if sm_ok:
            valid_sitemap += 1
        if rss_ok:
            valid_rss += 1
        if sm_ok and rss_ok:
            both_valid += 1
        if (not sm_ok) and (not rss_ok):
            neither_valid += 1

        active_after = "FALSE" if (not sm_ok and not rss_ok) else "TRUE"

        src_ws.update(f"D{row_num}:F{row_num}", [[
            sm_best["url"] if sm_ok else "",
            rss_final if rss_ok else rss_url,
            active_after
        ]])

        detail_ws.append_row([
            run_id, list_name, pub, base_url,
            sm_best["url"] if sm_ok else "", str(sm_ok), sm_best["reason"],
            rss_final if rss_ok else rss_url, str(rss_ok), rss_reason,
            active_after, now_utc()
        ])

    emit("complete", len(scoped_rows), len(scoped_rows), "Metadata validation complete")
    return {
        "run_id": run_id,
        "list_name": list_name,
        "total": total,
        "valid_sitemap": valid_sitemap,
        "valid_rss": valid_rss,
        "both_valid": both_valid,
        "neither_valid": neither_valid,
    }
