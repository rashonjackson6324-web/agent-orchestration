param([switch]$Once = $false, [int]$IntervalSeconds = 30)
$WatchDirs = @("$env:USERPROFILE\Desktop", "$env:USERPROFILE\Downloads")
$CategoryRules = @{
    "NETWORK_WORK" = @{ "path" = "<LOCAL_PATH>"; "keywords" = @("resume", "cover", "interview") }
    "NETWORK_TRADING" = @{ "path" = "<LOCAL_PATH>"; "keywords" = @("trading_executor", "signal", "trade") }
    "NETWORK_FINANCIAL" = @{ "path" = "<LOCAL_PATH>"; "keywords" = @("credit", "tax", "budget") }
    "DASHBOARD" = @{ "path" = "<LOCAL_PATH>"; "keywords" = @("dashboard", "ui") }
    "NETWORK_REFERENCE" = @{ "path" = "<LOCAL_PATH>"; "keywords" = @("guide", "reference") }
}
function Find-Category { param([string]$FileName); $FileName_Lower = $FileName.ToLower(); foreach ($Cat in $CategoryRules.Keys) { foreach ($Kw in $CategoryRules[$Cat]["keywords"]) { if ($FileName_Lower -match $Kw) { return $Cat } } }; return $null }
do { foreach ($WatchDir in $WatchDirs) { if (-not (Test-Path $WatchDir)) { continue }; Get-ChildItem -Path $WatchDir -File -Force -ErrorAction SilentlyContinue | ForEach-Object { if ($_.Name -match "^\.") { return }; $Category = Find-Category -FileName $_.Name; if (-not $Category) { return }; $DestPath = "$($CategoryRules[$Category]['path'])"; if (-not (Test-Path $DestPath)) { New-Item -ItemType Directory -Path $DestPath -Force | Out-Null }; $DestFile = Join-Path $DestPath $_.Name; try { Move-Item -Path $_.FullName -Destination $DestFile -Force; Write-Host "[MOVE] $($_.Name) -> $Category" } catch { Write-Host "[ERROR] $_" } } }; if ($Once) { break }; Start-Sleep -Seconds $IntervalSeconds } while ($true)
