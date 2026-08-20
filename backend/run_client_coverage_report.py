from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

try:
    from client_coverage_search import (
        run_keyword_coverage_report,
        split_csv_or_lines,
        split_lines,
    )
except ImportError:
    from backend.client_coverage_search import (
        run_keyword_coverage_report,
        split_csv_or_lines,
        split_lines,
    )


ARTIFACT_DIR = Path("output/client_coverage_artifact")


def _write_result(payload: dict) -> None:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    (ARTIFACT_DIR / "result.json").write_text(
        json.dumps(payload, indent=2, default=str),
        encoding="utf-8",
    )


def main() -> None:
    job_id = os.environ["COVERAGE_JOB_ID"]
    coverage_run_id = f"coverage-search-{job_id}"

    try:
        result = run_keyword_coverage_report(
            report_title=os.environ["COVERAGE_REPORT_TITLE"],
            mention_terms=split_csv_or_lines(os.environ["COVERAGE_MENTION_TERMS"]),
            search_queries=split_lines(os.environ["COVERAGE_SEARCH_QUERIES"]),
            date_from=os.getenv("COVERAGE_DATE_FROM", ""),
            date_to=os.getenv("COVERAGE_DATE_TO", ""),
            backlink_domains=split_csv_or_lines(
                os.getenv("COVERAGE_BACKLINK_DOMAINS", "")
            ),
            coverage_run_id=coverage_run_id,
        )

        ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(result["pdf_path"], ARTIFACT_DIR / "report.pdf")

        searches_used = int(result.get("searches_used", 0) or 0)
        searched_results = int(result.get("searched_results", 0) or 0)
        completion_message = (
            "Search returned no results"
            if searched_results == 0
            else "Coverage report complete"
        )
        _write_result({
            "status": "complete",
            "phase": "complete",
            "current": searches_used,
            "total": searches_used,
            "message": completion_message,
            "coverage_run_id": coverage_run_id,
            "summary": {
                "total_coverage": result.get("count", 0),
                "needs_review": result.get("needs_review", 0),
                "searched_results": searched_results,
                "searches_used": searches_used,
                "searches_remaining": result.get("searches_remaining", 0),
                "search_stop_reason": result.get("search_stop_reason", ""),
                "country_google_searches_used": result.get(
                    "country_stats", {}
                ).get("google_searches_used", 0),
            },
            "results": result.get("results", []),
            "review_results": result.get("review_results", []),
            "search_diagnostics": result.get("search_diagnostics", []),
            "highlights": result.get("highlights", {}),
            "country_stats": result.get("country_stats", {}),
        })
    except Exception as exc:
        _write_result({
            "status": "failed",
            "phase": "failed",
            "message": f"Coverage report failed: {exc}",
            "error": str(exc),
        })
        raise


if __name__ == "__main__":
    main()
