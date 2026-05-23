@echo off
cd /d "%~dp0"
title STRUCT.ai News Impact - Remove Auto-Start
color 0C

set "TASK_NAME=STRUCT_NewsImpactService"

echo.
echo  =========================================================
echo   STRUCT.ai News Impact Service - Remove Auto-Start
echo  =========================================================
echo.

schtasks /query /tn "%TASK_NAME%" >nul 2>&1
if errorlevel 1 (
    echo  [INFO] No auto-start task found.  Nothing to remove.
    goto :done
)

echo  [..] Removing scheduled task: %TASK_NAME%

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Unregister-ScheduledTask -TaskName '%TASK_NAME%' -Confirm:$false -ErrorAction SilentlyContinue;" ^
  "Write-Host '[OK] Task removed.'"

echo.
echo  Auto-start has been disabled.
echo  The service will no longer start automatically at login.
echo.
echo  Run setup_autostart.bat to re-enable it.

:done
echo.
echo  This window closes in 60 seconds -- press any key to close it now.
timeout /t 60
exit /b
