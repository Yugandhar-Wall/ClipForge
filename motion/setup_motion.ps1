# Installs what ClipForge's motion graphics need, inside this folder only:
#   Node.js 22 (portable, only if you don't already have Node 22+), HyperFrames, GSAP,
#   ffprobe, and a headless Chrome (or uses your installed Chrome / Edge).
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$motion = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $motion

function Get-NodeMajor($exe) {
    try { $v = & $exe -v 2>$null; if ($v -match '^v(\d+)\.') { return [int]$Matches[1] } } catch {}
    return 0
}

# 1. Node.js 22+
$node = $null
$local = Join-Path $motion "node\node.exe"
if ((Test-Path $local) -and ((Get-NodeMajor $local) -ge 22)) { $node = $local }
elseif ((Get-Command node -ErrorAction SilentlyContinue) -and ((Get-NodeMajor "node") -ge 22)) {
    $node = (Get-Command node).Source
}
if (-not $node) {
    Write-Host "Downloading portable Node.js 22 (about 30 MB)..."
    $base = "https://nodejs.org/dist/latest-v22.x"
    $sums = (Invoke-WebRequest "$base/SHASUMS256.txt" -UseBasicParsing).Content
    $line = ($sums -split "`n") | Where-Object { $_ -match "node-v[\d.]+-win-x64\.zip\s*$" } | Select-Object -First 1
    if (-not $line) { throw "Couldn't find the Node.js download on nodejs.org" }
    $parts = $line.Trim() -split "\s+"
    $hash = $parts[0]; $zipName = $parts[1]
    $zip = Join-Path $env:TEMP $zipName
    Invoke-WebRequest "$base/$zipName" -OutFile $zip -UseBasicParsing
    if ((Get-FileHash $zip -Algorithm SHA256).Hash.ToLower() -ne $hash.ToLower()) { throw "Node.js download is corrupt (checksum mismatch)" }
    $tmp = Join-Path $motion "_node_tmp"
    if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
    Expand-Archive $zip -DestinationPath $tmp -Force
    if (Test-Path (Join-Path $motion "node")) { Remove-Item (Join-Path $motion "node") -Recurse -Force }
    Move-Item (Join-Path $tmp ($zipName -replace '\.zip$', '')) (Join-Path $motion "node")
    Remove-Item $tmp -Recurse -Force
    Remove-Item $zip -Force
    $node = $local
}
Write-Host "Node.js: $node ($(& $node -v))"
$nodeDir = Split-Path $node
$env:PATH = "$nodeDir;$env:PATH"
$npm = Join-Path $nodeDir "npm.cmd"
if (-not (Test-Path $npm)) { $npm = "npm.cmd" }

# 2. HyperFrames + GSAP + ffprobe
Write-Host "Installing HyperFrames, GSAP and ffprobe (about 150 MB)..."
& $npm install --no-fund --no-audit --loglevel=error
if ($LASTEXITCODE -ne 0) { throw "npm install failed" }
Copy-Item (Join-Path $motion "node_modules\gsap\dist\gsap.min.js") (Join-Path $motion "templates\gsap.min.js") -Force

# 3. Browser for rendering
$env:HYPERFRAMES_NO_TELEMETRY = "1"; $env:DO_NOT_TRACK = "1"
$cli = Join-Path $motion "node_modules\hyperframes\bin\hyperframes.mjs"
$browser = ""
Write-Host "Getting a headless Chrome for rendering..."
& $node $cli browser ensure
if ($LASTEXITCODE -ne 0) {
    Write-Host "Download failed - looking for Chrome or Edge on this PC instead."
    $cands = @("$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
               "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
               "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe",
               "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
               "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe")
    foreach ($c in $cands) { if ($c -and (Test-Path $c)) { $browser = $c; break } }
    if (-not $browser) { throw "No browser available for rendering" }
    Write-Host "Using $browser"
}

@{ node = $node; browser = $browser } | ConvertTo-Json | Set-Content -Encoding UTF8 (Join-Path $motion "ready.json")
Write-Host "Motion graphics installed."
