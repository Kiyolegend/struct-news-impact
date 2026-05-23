@echo off
cd /d "%~dp0"
title STRUCT.ai News Impact - Status Check

echo.
echo  =========================================================
echo   STRUCT.ai News Impact Service - Status Check
echo  =========================================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$url = 'http://localhost:5003/api/impact/health';" ^
  "try {" ^
  "  $r = Invoke-RestMethod -Uri $url -TimeoutSec 3 -ErrorAction Stop;" ^
  "  $age = if ($r.cache_age_secs -ne $null) { [int]$r.cache_age_secs } else { -1 };" ^
  "  $next = if ($r.next_refresh_secs -ne $null) { [int]$r.next_refresh_secs } else { -1 };" ^
  "  $ageStr  = if ($age -ge 0)  { [string][int]($age/60) + ' min ' + ($age%%60) + ' sec ago' } else { 'unknown' };" ^
  "  $nextStr = if ($next -ge 0) { [string][int]($next/60) + ' min ' + ($next%%60) + ' sec' } else { 'unknown' };" ^
  "  $statusColor = if ($r.status -eq 'ok') { 'Green' } else { 'Yellow' };" ^
  "  Write-Host '  Status        : ' -NoNewline; Write-Host $r.status.ToUpper() -ForegroundColor $statusColor;" ^
  "  Write-Host '  Events cached : ' $r.events_cached;" ^
  "  Write-Host '  Last refresh  : ' $r.last_refresh_utc;" ^
  "  Write-Host '  Cache age     : ' $ageStr;" ^
  "  Write-Host '  Next refresh  : ' $nextStr;" ^
  "  Write-Host '  API key set   : ' $r.api_key_set;" ^
  "  Write-Host '  Active pairs  : ' ($r.active_pairs -join ', ');" ^
  "  if ($r.last_error) { Write-Host '  Last error    : ' $r.last_error -ForegroundColor Red; }" ^
  "  Write-Host;" ^
  "  Write-Host '  -------------------------------------------------';" ^
  "  $url2 = 'http://localhost:5003/api/impact/now';" ^
  "  $now = Invoke-RestMethod -Uri $url2 -TimeoutSec 3 -ErrorAction Stop;" ^
  "  Write-Host '  Current pair status:';" ^
  "  foreach ($pair in ($now.PSObject.Properties | Sort-Object Name)) {" ^
  "    $p = $pair.Value;" ^
  "    $blocked = $p.blocked;" ^
  "    $penalty = $p.confidence_penalty;" ^
  "    $impact  = $p.impact_level;" ^
  "    $reason  = $p.reason;" ^
  "    if ($blocked) {" ^
  "      Write-Host ('    {0,-12} BLOCKED  penalty={1,3}  impact={2}/10  {3}' -f $pair.Name, $penalty, $impact, $reason) -ForegroundColor Red;" ^
  "    } else {" ^
  "      Write-Host ('    {0,-12} clear    penalty={1,3}  impact={2}/10' -f $pair.Name, $penalty, $impact) -ForegroundColor Green;" ^
  "    }" ^
  "  };" ^
  "  Write-Host;" ^
  "  $url3 = 'http://localhost:5003/api/impact/upcoming?hours=4';" ^
  "  $up = Invoke-RestMethod -Uri $url3 -TimeoutSec 3 -ErrorAction Stop;" ^
  "  if ($up.total_events -gt 0) {" ^
  "    Write-Host '  -------------------------------------------------';" ^
  "    Write-Host ('  Upcoming events (next 4 hours): ' + $up.total_events);" ^
  "    foreach ($ev in $up.events) {" ^
  "      $mins = $ev.minutes_away;" ^
  "      $name = $ev.event;" ^
  "      $lvl  = $ev.impact_level;" ^
  "      $pairs = $ev.affects_pairs -join ',';" ^
  "      Write-Host ('    {0,4} min  [{1}/10]  {2}  ({3})' -f $mins, $lvl, $name, $pairs);" ^
  "    }" ^
  "    Write-Host;" ^
  "  } else {" ^
  "    Write-Host '  Upcoming events (next 4 hours): none';" ^
  "    Write-Host;" ^
  "  }" ^
  "} catch {" ^
  "  Write-Host '  [ERROR] Could not reach the service.' -ForegroundColor Red;" ^
  "  Write-Host '  Is it running?  Try: start.bat  or  start_background.bat' -ForegroundColor Yellow;" ^
  "  Write-Host;" ^
  "}"

echo  =========================================================
echo.
echo  This window closes in 60 seconds -- press any key to close it now.
timeout /t 60
exit /b
