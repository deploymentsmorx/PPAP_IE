param(
    [Parameter(Mandatory = $true)]
    [string]$ApiUrl
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Desktop = Join-Path $Root "desktop"
$Dist = Join-Path $Desktop "dist-exe"
$OutName = "SmorX-PPAP"

Write-Host "Building desktop EXE for API: $ApiUrl"

if (-not (Get-Command pyinstaller -ErrorAction SilentlyContinue)) {
    pip install pyinstaller
}

New-Item -ItemType Directory -Force -Path $Dist | Out-Null
Set-Content -Path (Join-Path $Desktop "api_url.txt") -Value $ApiUrl.TrimEnd("/") -Encoding ascii

Push-Location $Desktop
try {
    pyinstaller `
        --noconfirm `
        --clean `
        --onefile `
        --windowed `
        --name $OutName `
        --distpath $Dist `
        --workpath (Join-Path $Desktop "build") `
        --add-data "api_url.txt;." `
        "app.py"
} finally {
    Pop-Location
}

$Portable = Join-Path $Dist "$OutName.exe"
if (Test-Path $Portable) {
    Copy-Item $Portable (Join-Path $Dist "SmorX-Setup.exe") -Force
    Write-Host "Output: $Portable"
    Write-Host "Also copied to: $(Join-Path $Dist 'SmorX-Setup.exe')"
} else {
    throw "Build finished but EXE was not found."
}
