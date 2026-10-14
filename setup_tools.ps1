param([string]$ArchivePath = "")
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$ToolManifest = Get-Content -LiteralPath (Join-Path $PSScriptRoot "toolchain-manifest.json") -Raw | ConvertFrom-Json
$ToolBin = Join-Path $PSScriptRoot "bin"
$ToolTempBase = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath()).TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar
$ToolStage = Join-Path $ToolTempBase ("BiliTools-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $ToolStage -Force | Out-Null
try {
  $ToolZip = Join-Path $ToolStage "tools.zip"
  if ($ArchivePath) {
    Copy-Item -LiteralPath $ArchivePath -Destination $ToolZip
  } else {
    Invoke-WebRequest -UseBasicParsing -Uri $ToolManifest.url -OutFile $ToolZip -TimeoutSec 300
  }
  if ((Get-FileHash -LiteralPath $ToolZip -Algorithm SHA256).Hash.ToLowerInvariant() -ne $ToolManifest.sha256) { throw "Tool archive SHA256 mismatch" }
  $ToolExtract = Join-Path $ToolStage "extract"
  Expand-Archive -LiteralPath $ToolZip -DestinationPath $ToolExtract
  foreach ($ToolEntry in $ToolManifest.files) {
    if ($ToolEntry.name -notmatch '^[a-zA-Z0-9_.-]+\.(exe|dll)$') { throw "Invalid tool filename" }
    $ToolFile = Join-Path $ToolExtract $ToolEntry.name
    if (-not (Test-Path -LiteralPath $ToolFile -PathType Leaf)) { throw "Missing tool: $($ToolEntry.name)" }
    if ((Get-FileHash -LiteralPath $ToolFile -Algorithm SHA256).Hash.ToLowerInvariant() -ne $ToolEntry.sha256) { throw "Tool file SHA256 mismatch: $($ToolEntry.name)" }
  }
  New-Item -ItemType Directory -Path $ToolBin -Force | Out-Null
  foreach ($ToolEntry in $ToolManifest.files) { Copy-Item -LiteralPath (Join-Path $ToolExtract $ToolEntry.name) -Destination $ToolBin -Force }
  Write-Host "Fixed toolchain verified and installed: $ToolBin"
} finally {
  $ToolResolvedStage = [System.IO.Path]::GetFullPath($ToolStage)
  if ($ToolResolvedStage.StartsWith($ToolTempBase, [System.StringComparison]::OrdinalIgnoreCase) -and
      (Split-Path -Leaf $ToolResolvedStage) -match '^BiliTools-[a-f0-9]{32}$') {
    Remove-Item -LiteralPath $ToolResolvedStage -Recurse -Force -ErrorAction SilentlyContinue
  }
}
