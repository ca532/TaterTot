Write-Host "Running collector smoke..."
python backend/smoke_collector.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Running summarizer smoke..."
python backend/smoke_summarizer.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "All smoke tests passed."

