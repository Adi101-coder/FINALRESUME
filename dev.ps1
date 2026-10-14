# Start frontend, backend, and Celery worker with one command.
# Usage (from repo root):  .\dev.ps1
$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
Set-Location $Root

$Backend = Join-Path $Root "backend"
$Frontend = Join-Path $Root "frontend"
$VenvPython = Join-Path $Backend ".venv\Scripts\python.exe"

if (-not (Test-Path (Join-Path $Root ".env")) -and (Test-Path (Join-Path $Root ".env.example"))) {
    Copy-Item (Join-Path $Root ".env.example") (Join-Path $Root ".env")
    Write-Host "==> Created .env from .env.example"
}
$feEnv = Join-Path $Frontend ".env.local"
if (-not (Test-Path $feEnv)) {
    "NEXT_PUBLIC_API_URL=http://localhost:8000" | Set-Content $feEnv
    Write-Host "==> Created frontend/.env.local"
}

if (-not (Test-Path $VenvPython)) {
    Write-Host "==> Creating backend virtualenv…"
    python -m venv (Join-Path $Backend ".venv")
}
Write-Host "==> Installing backend dependencies…"
& $VenvPython -m pip install -q -r (Join-Path $Backend "requirements.txt")
& $VenvPython -m playwright install chromium 2>$null

if (-not (Test-Path (Join-Path $Frontend "node_modules"))) {
    Write-Host "==> Installing frontend packages…"
    Push-Location $Frontend
    npm install
    Pop-Location
}

if (Get-Command docker -ErrorAction SilentlyContinue) {
    Write-Host "==> Starting Redis (worker broker)…"
    docker compose up -d redis | Out-Null
}

$env:PYTHONPATH = "$Backend;$Root"

Write-Host "==> Backend  http://localhost:8000"
Write-Host "==> Frontend http://localhost:3000"
Write-Host "==> Worker   Celery"
Write-Host "==> Ctrl+C stops this window's jobs — close the other terminals too if needed."

$backend = Start-Process -FilePath $VenvPython -ArgumentList "-m","uvicorn","app.main:app","--reload","--app-dir",".","--host","0.0.0.0","--port","8000" -WorkingDirectory $Backend -PassThru
$frontend = Start-Process -FilePath "npm" -ArgumentList "run","dev" -WorkingDirectory $Frontend -PassThru
$worker = Start-Process -FilePath $VenvPython -ArgumentList "-m","celery","-A","worker.tasks:celery_app","worker","--loglevel=info" -WorkingDirectory $Root -PassThru

try {
    Wait-Process -Id $backend.Id, $frontend.Id, $worker.Id
} finally {
    foreach ($p in @($backend, $frontend, $worker)) {
        if ($p -and -not $p.HasExited) { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue }
    }
}
