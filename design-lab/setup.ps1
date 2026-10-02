# Design lab setup for the Windows machine. Idempotent; safe to re-run.
# Needs Python 3 on PATH. Node is optional: without it, mocks render with tools\render.ps1 (Edge, no audit).
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$T = "..\.claude\skills\design-craft\tools"

python -c "import numpy, PIL" 2>$null
if ($LASTEXITCODE -ne 0) { python -m pip install -q -r requirements.txt }

if (Get-Command node -ErrorAction SilentlyContinue) {
  if (-not (Test-Path node_modules\playwright-core)) { npm install --silent --no-audit --no-fund }
  # render.cjs launches the installed Edge (channel msedge); nothing else to download.
} else { Write-Host "node not found: render with $T\render.ps1 (no audit report)" }

python "$T\fonts.py" fonts.txt fonts --install
Write-Host "WPF screenshots: BassStation.exe --render-<screen> out.png  (see src-native/DebugTools/RenderHarness.cs)"
