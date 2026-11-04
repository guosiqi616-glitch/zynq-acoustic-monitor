# Play demo clips in order (3 s gap). Works on PowerShell 5.1 and 7.
# All text here is ASCII on purpose; the Chinese descriptions live in 演示说明.md.
# Usage: powershell -ExecutionPolicy Bypass -File .\播放.ps1

$dir = Split-Path -Parent $MyInvocation.MyCommand.Path

# Hints keyed by filename prefix (ASCII), so no Chinese literals in code.
$hints = @{
    "00" = "baseline silence  -> expect BER 0.753, L0, no alarm"
    "01" = "real speech      -> expect NO alarm (speech-like, suppressed)"
    "02" = "13 kHz squeak    -> expect BER 0.99, L3, popup"
    "03" = "gear rattle      -> expect L3, event +1"
    "04" = "metal impacts    -> expect L2 per impact"
    "05" = "quiet squeak     -> expect L2/L3 still triggers"
}

$files = Get-ChildItem -Path $dir -Filter "*.wav" | Sort-Object Name
if (-not $files) { Write-Host "no wav files in $dir" -ForegroundColor Yellow; exit 1 }

foreach ($f in $files) {
    $key = $f.Name.Substring(0, 2)
    $hint = $hints[$key]
    if (-not $hint) { $hint = "" }

    Write-Host ""
    Write-Host ("[>] " + $f.Name) -ForegroundColor Cyan
    if ($hint) { Write-Host ("    " + $hint) -ForegroundColor Gray }

    $player = New-Object System.Media.SoundPlayer $f.FullName
    $player.PlaySync()
    $player.Dispose()

    Write-Host "    done, waiting 3 s ..." -ForegroundColor DarkGray
    Start-Sleep -Seconds 3
}

Write-Host ""
Write-Host "demo finished." -ForegroundColor Green
