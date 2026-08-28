from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

try:
    from client_coverage_search import split_search_queries
    from coverage_actions import discover_job, enrich_countries_job, finalize_job, job_payload, verify_job
    from coverage_job_store import CoverageJobStore
    from google_storage import GoogleSheetsDB
except ImportError:
    from backend.client_coverage_search import split_search_queries
    from backend.coverage_actions import discover_job, enrich_countries_job, finalize_job, job_payload, verify_job
    from backend.coverage_job_store import CoverageJobStore
    from backend.google_storage import GoogleSheetsDB


ARTIFACT_DIR = Path("output/client_coverage_artifact")


def _write_result(payload: dict) -> None:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    (ARTIFACT_DIR / "result.json").write_text(
        json.dumps(payload, indent=2, default=str),
        encoding="utf-8",
    )


def main() -> None:
    job_id = os.environ["COVERAGE_JOB_ID"]
    action = os.getenv("COVERAGE_ACTION", "discover").strip().lower()
    database = GoogleSheetsDB()
    store = CoverageJobStore(database)

    try:
        if action == "discover":
            discovery = discover_job(
                store,
                job_id,
                queries=split_search_queries(os.getenv("COVERAGE_SEARCH_QUERIES", "")) or None,
            )
            verification = verify_job(store, job_id)
            result = {
                **discovery,
                **verification,
                "new_candidates": discovery.get("new_candidates", 0),
                "searches_used": discovery.get("searches_used", 0),
            }
        elif action == "verify":
            result = verify_job(store, job_id)
        elif action == "country":
            result = enrich_countries_job(store, job_id, database)
        elif action == "finalize":
            result = finalize_job(store, job_id)
            ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
            shutil.copy2(result["pdf_path"], ARTIFACT_DIR / "report.pdf")
        else:
            raise ValueError(f"Unsupported coverage action: {action}")

        status = str((store.get_job(job_id) or {}).get("status", ""))
        store.release_action(job_id, status=status, action=action)
        _write_result({
            "status": "complete",
            "phase": action,
            "message": f"Coverage {action} complete",
            "job_id": job_id,
            "coverage_run_id": f"coverage-search-{job_id}",
            **result,
        })
    except Exception as exc:
        store.release_action(
            job_id,
            status="failed",
            error=f"{type(exc).__name__}: {exc}",
            action=action,
        )
        try:
            snapshot = job_payload(store, job_id)
        except Exception:
            snapshot = {}
        _write_result({
            "status": "failed",
            "phase": action,
            "message": f"Coverage {action} failed: {exc}",
            "error": str(exc),
            **snapshot,
        })
        raise


if __name__ == "__main__":
    main()
