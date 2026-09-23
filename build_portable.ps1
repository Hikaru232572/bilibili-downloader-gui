$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Bin = Join-Path $Root "bin"
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
New-Item -ItemType Directory -Force -Path $Bin | Out-Null
Set-Location $Root

if (Test-Path -LiteralPath $VenvPython) {
  $Python = $VenvPython
} else {
  $PythonCommand = Get-Command python -ErrorAction SilentlyContinue
  if (-not $PythonCommand) {
    throw "Python was not found. Install Python 3.12+ or create .venv first."
  }
  $Python = $PythonCommand.Source
}

$RequiredTools = @("yt-dlp.exe", "aria2c.exe", "ffmpeg.exe", "ffprobe.exe")
$MissingTools = $RequiredTools | Where-Object { -not (Test-Path -LiteralPath (Join-Path $Bin $_)) }
if ($MissingTools) {
  throw "Missing required files in bin: $($MissingTools -join ', ')"
}

& $Python -c "import PIL" 2>$null
if ($LASTEXITCODE -ne 0) {
  throw "Pillow is not installed. Run: python -m pip install -r requirements.txt"
}

$PreviousNoUserSite = $env:PYTHONNOUSERSITE
$env:PYTHONNOUSERSITE = "1"

$PyInstallerArgs = @(
  "--noconfirm",
  "--clean",
  "--windowed",
  "--name", "BilibiliDownloader",
  "--add-data", "$Bin;bin",
  "--add-data", "$(Join-Path $Root 'README.md');.",
  "--add-data", "$(Join-Path $Root 'LICENSE');."
)
$AppIcon = Join-Path $Root "app.ico"
if (Test-Path -LiteralPath $AppIcon) {
  $PyInstallerArgs += @("--icon", $AppIcon, "--add-data", "$AppIcon;.")
}
$PyInstallerArgs += (Join-Path $Root "app.py")

& $Python -s -m PyInstaller @PyInstallerArgs

$PyInstallerExitCode = $LASTEXITCODE
if ($null -eq $PreviousNoUserSite) {
  Remove-Item Env:PYTHONNOUSERSITE -ErrorAction SilentlyContinue
} else {
  $env:PYTHONNOUSERSITE = $PreviousNoUserSite
}
if ($PyInstallerExitCode -ne 0) {
  throw "PyInstaller failed with exit code $PyInstallerExitCode"
}

Copy-Item -LiteralPath (Join-Path $Root "README.md") -Destination (Join-Path $Root "dist\BilibiliDownloader\README.md") -Force
Copy-Item -LiteralPath (Join-Path $Root "LICENSE") -Destination (Join-Path $Root "dist\BilibiliDownloader\LICENSE") -Force
New-Item -ItemType Directory -Force -Path (Join-Path $Root "dist\BilibiliDownloader\downloads") | Out-Null

Write-Host "Portable build created under: $(Join-Path $Root 'dist\BilibiliDownloader')"
