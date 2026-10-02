# Render a local HTML mock to PNG with headless Edge (Windows).
# usage: powershell -File render.ps1 -html C:\path\page.html -png C:\path\out.png [-w 1440 -h 810 -scale 1]
# Needs its own --user-data-dir, otherwise headless Edge exits without a screenshot.
param([string]$html, [string]$png, [int]$w = 1440, [int]$h = 810, [double]$scale = 1)
$edge = "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
if (-not (Test-Path $edge)) { $edge = "C:\Program Files\Microsoft\Edge\Application\msedge.exe" }
$prof = Join-Path $env:TEMP "design-craft-edge-prof"
$url = if ($html -match '^(file|https?):') { $html } else { "file:///" + ($html -replace '\\', '/') }
$a = @("--headless", "--disable-gpu", "--hide-scrollbars", "--allow-file-access-from-files", "--user-data-dir=$prof",
  "--force-device-scale-factor=$scale", "--window-size=$w,$h", "--virtual-time-budget=4000", "--screenshot=$png", $url)
Start-Process -FilePath $edge -ArgumentList $a -Wait -NoNewWindow
if (Test-Path $png) { "rendered $png" } else { "FAILED $png" }
