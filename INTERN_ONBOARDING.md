# App-revamp Handover Documentation

## Executive Summary
App-revamp is the full-stack Reading Roundup platform used to automate media monitoring, article summarization, and reporting workflows for PR operations. It extends the legacy TaterTot implementation with stronger backend boundaries, source metadata validation, trend analysis, and improved run-state orchestration.

Primary outcomes:
- Automated ingestion and summarization workflow across configured publication lists.
- Dashboard-driven run control and status visibility.
- Google Sheets/Drive integrated storage and reporting.
- Source quality checks (RSS/sitemap) and trend analytics.

## 1. Project Background and Goals
### 1.1 Business Problem
Manual source monitoring and article summarization is slow and inconsistent. The system exists to reduce repetitive research effort and provide structured outputs for outreach and reporting.

### 1.2 Goals
- Automate article collection from source lists.
- Generate concise, relevant summaries with author attribution.
- Persist outputs in a shared operational data store.
- Provide an authenticated dashboard for triggering and monitoring runs.
- Produce report artifacts (PDF + metadata).
- Add source-health and trend-analysis capabilities.

### 1.3 Scope Notes
- Core runtime paths are in `backend/` and `frontend/`.
- `Temp/` contains experiments and is not a reliable production path.

## 2. High-Level Architecture
- Frontend: React + Vite UI for auth, run control, status, and views.
- Backend: FastAPI service handling auth, orchestration, integrations, and protected endpoints.
- Pipeline: collector -> summarizer -> storage -> report generation.
- Integrations: GitHub Actions, Google Sheets, Google Drive.

Canonical flow:
1. User authenticates in the dashboard.
2. Frontend calls backend API.
3. Backend validates auth/cooldown and triggers workflow/pipeline.
4. Backend tracks run state and exposes status.
5. Frontend renders progress and results.

## 3. Component Responsibilities
### 3.1 Backend (`backend/`)
- `trigger_service.py`: FastAPI entrypoint and orchestration surface.
- Responsibilities:
- JWT auth, refresh token handling, cookie/token checks.
- Security headers and CORS allowlist.
- Pipeline trigger/status endpoints.
- Source metadata job submission + progress tracking.
- Trend trigger request handling.

- `pipeline_runner.py`: orchestrates execution stages.
- Responsibilities:
- Initialize collector/summarizer/storage.
- Run collection and summarization stages.
- Save structured rows.
- Generate report PDF.

- `AgentCollector.py`: publication scraping/collection logic.
- `AgentSumm.py`: summarization + author extraction logic.
- `google_storage.py`: Google Sheets/Drive persistence and helper operations.
- `publication_metadata_pipeline.py`: source RSS/sitemap validation and reporting.
- `trend_analyzer.py` + `run_trend_analysis.py`: trend detection and execution wrapper.

### 3.2 Frontend (`frontend/src/`)
- `App.jsx`: top-level route/view composition.
- `contexts/AuthContext.jsx`: auth state and session lifecycle.
- `components/`: UI modules (`SummariesView`, `TrendAnalysisView`, layout/navigation, etc.).
- `components/pipeline/`: pipeline status and action components.
- `hooks/usePipelineRunner.js`: run-action and status orchestration.
- `services/*.js`: API wrappers used by the UI.

## 4. App-revamp vs Legacy TaterTot
Key architectural improvements:
1. Backend boundary hardened.
- Legacy frontend directly used privileged GitHub/Google integrations with client-exposed env values.
- App-revamp centralizes privileged operations in backend API endpoints.

2. Source metadata subsystem added.
- RSS/sitemap discovery, validation, and detailed reporting were added via `publication_metadata_pipeline.py`.

3. Trend analysis subsystem added.
- Keyword trend scoring, baseline comparisons, and trend views were added.

4. Operational run-state handling improved.
- Server-side status cache, terminal status polling, cooldown state, and metadata job tracking are more explicit.

## 5. Security and Data Boundary Model
Non-negotiable rule: browser is untrusted.

- Secrets/tokens must remain server-side.
- Frontend should only call backend endpoints.
- Backend controls third-party API access and credentials.
- JWT access/refresh logic is enforced in `trigger_service.py`.
- CORS is allowlisted and should not be broadened without review.

## 6. Data and Storage
Operational stores:
- Google Sheets for article rows, metadata, starred items, source config/report tabs.
- GitHub Actions artifacts for generated report files.

Backend environment references include:
- GitHub: `GITHUB_OWNER`, `GITHUB_REPO`, `GITHUB_TOKEN`, `GITHUB_WORKFLOW`, `GITHUB_REF`
- Auth: `APP_LOGIN_PASSWORD`, `JWT_SECRET`, cookie/JWT expiry vars
- Google: `GOOGLE_SHEET_ID`, `GOOGLE_CREDENTIALS`
- Runtime controls: cooldown/polling/debug flags

## 7. Runtime Workflows
### 7.1 Pipeline Run
1. Trigger from UI.
2. Backend validates session/cooldown.
3. Pipeline/workflow executes collection -> summarization -> storage -> PDF.
4. Status endpoint/websocket/state cache exposes progress.
5. UI updates and surfaces outputs.

### 7.2 Source Metadata Validation
1. Select list name.
2. Run metadata pipeline.
3. Validate RSS/sitemap endpoints.
4. Write detail rows and update source active status.

### 7.3 Trend Analysis
1. Trigger trend analysis with topic/list/window params.
2. Analyzer computes current-period vs baseline movement.
3. UI renders trend rows and supporting links/counts.

## 8. Local Setup (Intern)
### Backend
1. `cd App-revamp/backend`
2. Create/activate virtual env.
3. Install dependencies from `requirements.txt`.
4. Configure environment variables and credentials.
5. Run FastAPI app via uvicorn.

### Frontend
1. `cd App-revamp/frontend`
2. `npm install`
3. Configure local frontend env (API base only; no secrets).
4. `npm run dev`

## 9. Suggested Reading Order
1. `backend/trigger_service.py`
2. `backend/pipeline_runner.py`
3. `backend/google_storage.py`
4. `backend/publication_metadata_pipeline.py`
5. `backend/trend_analyzer.py`
6. `frontend/src/hooks/usePipelineRunner.js`
7. `frontend/src/contexts/AuthContext.jsx`
8. `frontend/src/components/SummariesView.jsx`
9. `frontend/src/components/TrendAnalysisView.jsx`

## 10. Operational Checks and Troubleshooting
- Trigger fails:
- Verify GitHub env vars/token and workflow ref/name.

- No data in UI:
- Verify Google sheet ID, credentials, and expected tab names.

- Auth issues:
- Verify JWT/cookie settings, secrets, and frontend-backend origin pairing.

- Trend rows empty:
- Verify selected window/topic/list and that source data exists for that period.

- Source metadata failures:
- Inspect validation detail sheet for HTTP/XML/feed parse reasons.

## 11. First-Week Intern Runbook
1. Bring up backend + frontend locally.
2. Confirm login and health/status endpoints.
3. Trigger one pipeline run and trace outputs to Sheets/artifacts.
4. Run source metadata validation on a small list and inspect detail rows.
5. Run trend analysis and trace one result from raw data to UI.
6. Submit one short note with observed risks + recommended cleanup.

## 12. Relevant Legacy Notes Carried Forward
From the prior handover, still relevant in App-revamp:
- Pipeline stage pattern remains collector/summarizer/storage/report oriented.
- Quality of source extraction still depends on publication HTML variability.
- Artifact/report generation remains part of operational output.
- Monitoring and triage remain essential for flaky source endpoints.

Deprecated or changed from legacy assumptions:
- Do not rely on client-side privileged API access patterns.
- Prefer backend-mediated integrations and token handling.
- New metadata/trend modules are now part of normal operational architecture.
