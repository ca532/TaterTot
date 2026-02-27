import json
import os
import threading
import time
from pathlib import Path
from typing import List, Optional
from fastapi.middleware.cors import CORSMiddleware
import requests
import jwt
from fastapi import FastAPI, Header, HTTPException, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from jwt import ExpiredSignatureError, PyJWTError

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "https://repo-trigger.onrender.com",
        "https://annamayya9.github.io",

    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

GITHUB_OWNER = os.environ["GITHUB_OWNER"]
GITHUB_REPO = os.environ["GITHUB_REPO"]
GITHUB_WORKFLOW = os.environ.get("GITHUB_WORKFLOW", "collect-articles.yml")
GITHUB_REF = os.environ.get("GITHUB_REF", "main")
GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
APP_LOGIN_PASSWORD = os.environ["APP_LOGIN_PASSWORD"]
JWT_SECRET = os.environ["JWT_SECRET"]
JWT_EXPIRES_SECONDS = int(os.environ.get("JWT_EXPIRES_SECONDS", "1800"))
JWT_ALG = "HS256"

COOLDOWN_SECONDS = int(os.environ.get("PIPELINE_COOLDOWN_SECONDS", "1800"))
STATE_FILE = Path(os.environ.get("PIPELINE_STATE_FILE", "backend/data/pipeline_state.json"))

GITHUB_API_BASE = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}"

STATE_LOCK = threading.Lock()


class TriggerRequest(BaseModel):
    keywords: Optional[List[str]] = None


class LoginRequest(BaseModel):
    password: str


def _issue_jwt() -> str:
    now = int(time.time())
    payload = {
        "sub": "dashboard-user",
        "iat": now,
        "exp": now + JWT_EXPIRES_SECONDS,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)


def _check_auth(authorization: str) -> None:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")
    token = authorization.split(" ", 1)[1].strip()
    try:
        jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
    except ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except PyJWTError:
        raise HTTPException(status_code=401, detail="Unauthorized")


@app.post("/auth/login")
def auth_login(req: LoginRequest):
    if req.password != APP_LOGIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return {
        "access_token": _issue_jwt(),
        "token_type": "bearer",
        "expires_in": JWT_EXPIRES_SECONDS,
    }


def _normalize_keywords(raw: Optional[List[str]]) -> List[str]:
    if not raw:
        return []
    cleaned = []
    seen = set()
    for k in raw:
        v = str(k).strip().lower()
        if not v:
            continue
        if len(v) > 80:
            raise HTTPException(status_code=400, detail="Keyword too long")
        if v not in seen:
            seen.add(v)
            cleaned.append(v)
    if cleaned and len(cleaned) < 5:
        raise HTTPException(status_code=400, detail="At least 5 keywords required when provided")
    if len(cleaned) > 25:
        raise HTTPException(status_code=400, detail="Maximum 25 keywords")
    return cleaned


def _gh_headers() -> dict:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _gh_request(method: str, url: str, **kwargs):
    last_exc = None
    for attempt in range(3):
        try:
            r = requests.request(method, url, headers=_gh_headers(), timeout=20, **kwargs)
            if r.status_code >= 500 and attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            return r
        except requests.RequestException as exc:
            last_exc = exc
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise HTTPException(status_code=502, detail=f"GitHub request failed: {exc}") from exc
    raise HTTPException(status_code=502, detail=f"GitHub request failed: {last_exc}")


def _ensure_state_file() -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not STATE_FILE.exists():
        _write_state(
            {
                "status": "idle",
                "last_triggered_at": None,
                "last_updated_at": int(time.time()),
                "last_run_id": None,
                "last_conclusion": None,
                "queued_requests": 0,
                "last_error": None,
            }
        )


def _read_state() -> dict:
    _ensure_state_file()
    with STATE_FILE.open("r", encoding="utf-8") as f:
        return json.load(f)


def _write_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    state["last_updated_at"] = int(time.time())
    with STATE_FILE.open("w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def _get_latest_run() -> Optional[dict]:
    url = f"{GITHUB_API_BASE}/actions/workflows/{GITHUB_WORKFLOW}/runs?per_page=1"
    r = _gh_request("GET", url)
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=f"GitHub status failed: {r.status_code}")
    runs = r.json().get("workflow_runs", [])
    return runs[0] if runs else None


def _normalize_status(run: Optional[dict]) -> tuple[str, Optional[str]]:
    if not run:
        return "idle", None

    gh_status = run.get("status")
    gh_conclusion = run.get("conclusion")

    if gh_status in {"queued", "in_progress", "waiting", "pending"}:
        return gh_status if gh_status in {"queued", "in_progress"} else "running", None

    if gh_status == "completed":
        if gh_conclusion == "success":
            return "success", gh_conclusion
        return "failed", gh_conclusion

    return "idle", gh_conclusion


@app.post("/pipeline/trigger")
def trigger_pipeline(req: TriggerRequest, response: Response, authorization: str = Header(default="")):
    _check_auth(authorization)

    with STATE_LOCK:
        state = _read_state()
        latest_run = _get_latest_run()
        normalized_status, conclusion = _normalize_status(latest_run)
        now = int(time.time())

        # Sync persisted state with GitHub state first
        state["status"] = normalized_status
        state["last_conclusion"] = conclusion
        state["last_run_id"] = latest_run.get("id") if latest_run else state.get("last_run_id")

        # Idempotency / single-active-run enforcement
        if normalized_status in {"queued", "running", "in_progress"}:
            state["queued_requests"] = int(state.get("queued_requests", 0)) + 1
            _write_state(state)
            response.status_code = 202
            return {
                "ok": True,
                "state": "already_running",
                "message": "A run is already active. Request recorded as queued.",
                "queuedRequests": state["queued_requests"],
                "activeRunId": state["last_run_id"],
            }

        # Cooldown enforcement
        last_triggered = state.get("last_triggered_at")
        if last_triggered is not None:
            elapsed = now - int(last_triggered)
            if elapsed < COOLDOWN_SECONDS:
                retry_after = COOLDOWN_SECONDS - elapsed
                _write_state(state)
                raise HTTPException(
                    status_code=429,
                    detail=f"Cooldown active. Try again in {retry_after} seconds.",
                    headers={"Retry-After": str(retry_after)},
                )

        # Trigger dispatch
        url = f"{GITHUB_API_BASE}/actions/workflows/{GITHUB_WORKFLOW}/dispatches"
        keywords = _normalize_keywords(req.keywords)
        body = {
            "ref": GITHUB_REF,
            "inputs": {
                "test_mode": "false",
                "keywords": ",".join(keywords)
            },
        }


        r = _gh_request("POST", url, json=body)
        if r.status_code != 204:
            state["status"] = "failed"
            state["last_error"] = f"GitHub dispatch failed: {r.status_code} {r.text}"
            _write_state(state)
            raise HTTPException(status_code=502, detail=state["last_error"])

        state["status"] = "queued"
        state["last_triggered_at"] = now
        state["last_error"] = None
        state["queued_requests"] = 0
        _write_state(state)

    return {"ok": True, "state": "queued", "message": "Pipeline triggered"}


@app.get("/pipeline/status")
def pipeline_status(authorization: str = Header(default="")):
    _check_auth(authorization)

    with STATE_LOCK:
        state = _read_state()
        latest_run = _get_latest_run()
        normalized_status, conclusion = _normalize_status(latest_run)

        state["status"] = normalized_status
        state["last_conclusion"] = conclusion
        state["last_run_id"] = latest_run.get("id") if latest_run else state.get("last_run_id")
        _write_state(state)

        return {
            "status": state["status"],  # idle|queued|running|failed|success
            "conclusion": state["last_conclusion"],
            "runId": state["last_run_id"],
            "queuedRequests": state.get("queued_requests", 0),
            "lastTriggeredAt": state.get("last_triggered_at"),
            "lastUpdatedAt": state.get("last_updated_at"),
            "lastError": state.get("last_error"),
            "createdAt": latest_run.get("created_at") if latest_run else None,
            "updatedAt": latest_run.get("updated_at") if latest_run else None,
        }


@app.get("/pipeline/latest-artifact")
def latest_artifact(authorization: str = Header(default="")):
    _check_auth(authorization)

    runs_url = f"{GITHUB_API_BASE}/actions/workflows/{GITHUB_WORKFLOW}/runs?per_page=10"
    rr = _gh_request("GET", runs_url)
    if rr.status_code != 200:
        raise HTTPException(status_code=502, detail=f"GitHub runs failed: {rr.status_code}")

    runs = rr.json().get("workflow_runs", [])
    successful = next((r for r in runs if r.get("conclusion") == "success"), None)
    if not successful:
        return {"downloadURL": None}

    artifacts_url = f"{GITHUB_API_BASE}/actions/runs/{successful['id']}/artifacts"
    ar = _gh_request("GET", artifacts_url)
    if ar.status_code != 200:
        raise HTTPException(status_code=502, detail=f"GitHub artifacts failed: {ar.status_code}")

    artifacts = ar.json().get("artifacts", [])
    artifact = next(
        (a for a in artifacts if str(a.get("name", "")).startswith("Reading-Roundup-")),
        None,
    )
    if not artifact:
        return {"downloadURL": None}

    return {
        "downloadURL": artifact.get("archive_download_url"),
        "name": artifact.get("name"),
        "createdAt": artifact.get("created_at"),
        "runNumber": successful.get("run_number"),
    }


@app.get("/pipeline/download-latest-artifact")
def download_latest_artifact(authorization: str = Header(default="")):
    _check_auth(authorization)

    runs_url = f"{GITHUB_API_BASE}/actions/workflows/{GITHUB_WORKFLOW}/runs?per_page=10"
    rr = _gh_request("GET", runs_url)
    if rr.status_code != 200:
        raise HTTPException(status_code=502, detail=f"GitHub runs failed: {rr.status_code}")

    runs = rr.json().get("workflow_runs", [])
    successful = next((r for r in runs if r.get("conclusion") == "success"), None)
    if not successful:
        raise HTTPException(status_code=404, detail="No successful workflow run found")

    artifacts_url = f"{GITHUB_API_BASE}/actions/runs/{successful['id']}/artifacts"
    ar = _gh_request("GET", artifacts_url)
    if ar.status_code != 200:
        raise HTTPException(status_code=502, detail=f"GitHub artifacts failed: {ar.status_code}")

    artifacts = ar.json().get("artifacts", [])
    artifact = next(
        (a for a in artifacts if str(a.get("name", "")).startswith("Reading-Roundup-")),
        None,
    )
    if not artifact:
        raise HTTPException(status_code=404, detail="No matching artifact found")

    download_url = artifact.get("archive_download_url")
    if not download_url:
        raise HTTPException(status_code=404, detail="Artifact download URL unavailable")

    try:
        gh_resp = requests.get(download_url, headers=_gh_headers(), stream=True, timeout=60)
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Artifact download failed: {exc}") from exc

    if gh_resp.status_code != 200:
        detail = None
        try:
            detail = gh_resp.json()
        except ValueError:
            detail = gh_resp.text
        raise HTTPException(status_code=502, detail=f"Artifact download failed: {gh_resp.status_code} {detail}")

    artifact_name = artifact.get("name") or "latest-artifact"
    file_name = f"{artifact_name}.zip"
    return StreamingResponse(
        gh_resp.iter_content(chunk_size=1024 * 64),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{file_name}"'},
    )


@app.get("/pipeline/latest-result")
def latest_result(authorization: str = Header(default="")):
    _check_auth(authorization)

    status = pipeline_status(authorization=authorization)
    artifact = latest_artifact(authorization=authorization)

    return {
        "status": status.get("status"),
        "conclusion": status.get("conclusion"),
        "runId": status.get("runId"),
        "updatedAt": status.get("updatedAt"),
        "artifact": artifact,
    }
