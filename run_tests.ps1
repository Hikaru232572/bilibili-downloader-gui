$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
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

& $Python -B -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) {
  throw "Tests failed with exit code $LASTEXITCODE"
}
