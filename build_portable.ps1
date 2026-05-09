$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Bin = Join-Path $Root "bin"
New-Item -ItemType Directory -Force -Path $Bin | Out-Null
Set-Location $Root

python -m PyInstaller `
  --noconfirm `
  --clean `
  --windowed `
  --name BilibiliDownloader `
  --add-data "$Bin;bin" `
  --add-data "$(Join-Path $Root 'README.md');." `
  --add-data "$(Join-Path $Root 'LICENSE');." `
  (Join-Path $Root "app.py")

Copy-Item -LiteralPath (Join-Path $Root "README.md") -Destination (Join-Path $Root "dist\BilibiliDownloader\README.md") -Force
Copy-Item -LiteralPath (Join-Path $Root "LICENSE") -Destination (Join-Path $Root "dist\BilibiliDownloader\LICENSE") -Force
New-Item -ItemType Directory -Force -Path (Join-Path $Root "dist\BilibiliDownloader\downloads") | Out-Null

Write-Host "Portable build created under: $(Join-Path $Root 'dist\BilibiliDownloader')"
