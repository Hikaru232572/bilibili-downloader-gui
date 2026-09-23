$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Bin = Join-Path $Root "bin"
$TempBase = [System.IO.Path]::GetTempPath()
$Temp = Join-Path $TempBase ("BilibiliDownloaderTools-" + [guid]::NewGuid().ToString("N"))

$YtDlpUrl = "https://github.com/yt-dlp/yt-dlp/releases/download/2026.08.19/yt-dlp.exe"
$YtDlpSha256 = "66674953fe251b89f4d08c5f0e35e0728679bd67ab3d7d05c0562af101dd3e7a"
$Aria2Url = "https://github.com/aria2/aria2/releases/download/release-1.37.0/aria2-1.37.0-win-64bit-build1.zip"
$Aria2Sha256 = "67d015301eef0b612191212d564c5bb0a14b5b9c4796b76454276a4d28d9b288"
$FfmpegName = "ffmpeg-master-latest-win64-lgpl-shared.zip"
$FfmpegBaseUrl = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest"

function Get-VerifiedFile {
  param(
    [Parameter(Mandatory = $true)][string]$Url,
    [Parameter(Mandatory = $true)][string]$Destination,
    [Parameter(Mandatory = $true)][string]$Sha256
  )

  Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $Destination
  $Actual = (Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash.ToLowerInvariant()
  if ($Actual -ne $Sha256.ToLowerInvariant()) {
    throw "SHA256 mismatch for $Url. Expected $Sha256, got $Actual"
  }
}

New-Item -ItemType Directory -Force -Path $Bin | Out-Null
New-Item -ItemType Directory -Force -Path $Temp | Out-Null

try {
  Write-Host "Downloading verified yt-dlp..."
  Get-VerifiedFile -Url $YtDlpUrl -Destination (Join-Path $Bin "yt-dlp.exe") -Sha256 $YtDlpSha256

  Write-Host "Downloading verified aria2..."
  $AriaZip = Join-Path $Temp "aria2.zip"
  $AriaExtract = Join-Path $Temp "aria2"
  Get-VerifiedFile -Url $Aria2Url -Destination $AriaZip -Sha256 $Aria2Sha256
  Expand-Archive -LiteralPath $AriaZip -DestinationPath $AriaExtract
  $AriaExe = Get-ChildItem -LiteralPath $AriaExtract -Recurse -File -Filter "aria2c.exe" | Select-Object -First 1
  if (-not $AriaExe) {
    throw "aria2c.exe was not found in the downloaded archive"
  }
  Copy-Item -LiteralPath $AriaExe.FullName -Destination (Join-Path $Bin "aria2c.exe") -Force

  Write-Host "Downloading verified FFmpeg/FFprobe..."
  $Checksums = Join-Path $Temp "ffmpeg-checksums.sha256"
  Invoke-WebRequest -UseBasicParsing -Uri "$FfmpegBaseUrl/checksums.sha256" -OutFile $Checksums
  $ChecksumLine = Get-Content -LiteralPath $Checksums | Where-Object { $_ -match [regex]::Escape($FfmpegName) + "$" } | Select-Object -First 1
  if (-not $ChecksumLine) {
    throw "No checksum was published for $FfmpegName"
  }
  $FfmpegSha256 = ($ChecksumLine -split "\s+")[0]
  $FfmpegZip = Join-Path $Temp $FfmpegName
  $FfmpegExtract = Join-Path $Temp "ffmpeg"
  Get-VerifiedFile -Url "$FfmpegBaseUrl/$FfmpegName" -Destination $FfmpegZip -Sha256 $FfmpegSha256
  Expand-Archive -LiteralPath $FfmpegZip -DestinationPath $FfmpegExtract
  $FfmpegExe = Get-ChildItem -LiteralPath $FfmpegExtract -Recurse -File -Filter "ffmpeg.exe" | Select-Object -First 1
  $FfprobeExe = Get-ChildItem -LiteralPath $FfmpegExtract -Recurse -File -Filter "ffprobe.exe" | Select-Object -First 1
  if (-not $FfmpegExe -or -not $FfprobeExe) {
    throw "ffmpeg.exe or ffprobe.exe was not found in the downloaded archive"
  }
  $FfmpegBin = $FfmpegExe.Directory.FullName
  Get-ChildItem -LiteralPath $FfmpegBin -File | Copy-Item -Destination $Bin -Force

  Write-Host "Downloader tools installed under: $Bin"
}
finally {
  $ResolvedTempBase = [System.IO.Path]::GetFullPath($TempBase)
  $ResolvedTemp = [System.IO.Path]::GetFullPath($Temp)
  if ($ResolvedTemp.StartsWith($ResolvedTempBase, [System.StringComparison]::OrdinalIgnoreCase) -and
      (Split-Path -Leaf $ResolvedTemp).StartsWith("BilibiliDownloaderTools-")) {
    Remove-Item -LiteralPath $ResolvedTemp -Recurse -Force -ErrorAction SilentlyContinue
  }
}
