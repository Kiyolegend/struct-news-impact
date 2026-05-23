@echo off
cd /d "%~dp0"
title STRUCT.ai News Impact - Auto-Start Setup
color 0B

set "SERVICE_DIR=%~dp0"
if "%SERVICE_DIR:~-1%"=="\" set "SERVICE_DIR=%SERVICE_DIR:~0,-1%"
set "PYTHON_EXE=%SERVICE_DIR%\venv\Scripts\python.exe"
set "TASK_NAME=STRUCT_NewsImpactService"

echo.
echo  =========================================================
echo   STRUCT.ai News Impact Service - Auto-Start Setup
echo  =========================================================
echo.
echo  This registers a Windows Task Scheduler task so the service
echo  starts automatically every time you log into Windows.
echo.
echo  Service folder: %SERVICE_DIR%
echo.

:: ── Pre-flight checks ─────────────────────────────────────────────────────────
if not exist "%PYTHON_EXE%" (
    echo  [ERROR] Virtual environment not found at:
    echo    %PYTHON_EXE%
    echo.
    echo  Fix: run install.bat first, then run this file again.
    goto :done
)

if not exist "%SERVICE_DIR%\.env" (
    echo  [ERROR] .env file not found.
    echo.
    echo  Fix: run install.bat first and add your FINNHUB_API_KEY.
    goto :done
)

powershell -NoProfile -Command "exit 0" >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] PowerShell is not available on this system.
    echo  PowerShell is required to register the scheduled task.
    goto :done
)

:: ── Remove previous task if it exists ────────────────────────────────────────
schtasks /query /tn "%TASK_NAME%" >nul 2>&1
if not errorlevel 1 (
    echo  [..] Removing previous auto-start task...
    powershell -NoProfile -ExecutionPolicy Bypass -Command ^
      "Unregister-ScheduledTask -TaskName '%TASK_NAME%' -Confirm:$false -ErrorAction SilentlyContinue"
)

:: ── Register new task ─────────────────────────────────────────────────────────
echo  [..] Registering task with Windows Task Scheduler...

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$exe     = '%PYTHON_EXE%';" ^
  "$args    = 'news_impact_server.py';" ^
  "$workdir = '%SERVICE_DIR%';" ^
  "$action  = New-ScheduledTaskAction -Execute $exe -Argument $args -WorkingDirectory $workdir;" ^
  "$trigger = New-ScheduledTaskTrigger -AtLogOn;" ^
  "$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit 0 -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 2) -MultipleInstances IgnoreNew;" ^
  "Register-ScheduledTask -TaskName '%TASK_NAME%' -Action $action -Trigger $trigger -Settings $settings -RunLevel Highest -Force | Out-Null;" ^
  "Write-Host '[OK] Task registered successfully.'"

if errorlevel 1 (
    echo.
    echo  [ERROR] Could not register the scheduled task.
    echo.
    echo  Fix: right-click setup_autostart.bat and choose "Run as administrator"
    goto :done
)

echo.
echo  =========================================================
echo   Auto-start is now configured!
echo.
echo   The service will start automatically when you log in.
echo.
echo   To remove auto-start : run remove_autostart.bat
echo   To start right now   : run start_background.bat
echo  =========================================================

:done
echo.
echo  This window closes in 60 seconds -- press any key to close it now.
timeout /t 60
exit /b
