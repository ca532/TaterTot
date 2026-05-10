# Pipeline Triage Checklist

## Trigger Layer
- [ ] `POST /pipeline/trigger` returns 200/202
- [ ] `GET /pipeline/status` returns valid state
- [ ] `GET /pipeline/latest-result` returns status + artifact block
- [ ] No unexpected 401/429 behavior

## GitHub Workflow
- [ ] New run appears in Actions
- [ ] Input `keywords` accepted (no 422 unexpected inputs)
- [ ] Job completes without timeout/retry exhaustion

## Data Outputs
- [ ] Google Sheets `Articles` updated
- [ ] `Metadata` updated with latest run info
- [ ] PDF artifact uploaded and downloadable

## UI
- [ ] Status transitions correctly (`queued/running/success/failed`)
- [ ] Keyword validation messages shown
- [ ] Results view and PDF button work

## Security
- [ ] No `VITE_GITHUB_TOKEN` in frontend
- [ ] Backend-only secrets are not exposed
- [ ] Browser key restricted to referrers + Sheets API only

