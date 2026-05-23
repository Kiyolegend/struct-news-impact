@echo off
cd /d "%~dp0"
title STRUCT.ai News Impact - Background Launcher

echo.
echo  =========================================================
echo   STRUCT.ai News Impact - Background Launcher
echo  =========================================================
echo.

:: ── Pre-flight checks ─────────────────────────────────────────────────────────
if not exist venv\Scripts\python.exe (
    echo  [ERROR] Virtual environment not found.
    echo.
    echo  Fix: run install.bat first, then try again.
    goto :done
)

if not exist .env (
    echo  [ERROR] .env file not found.
    echo.
    echo  Fix: run install.bat first and add your FINNHUB_API_KEY to the .env file.
    goto :done
)

:: ── Launch in background ──────────────────────────────────────────────────────
echo  [..] Launching service in background (minimised window)...
start /min "STRUCT News Impact" venv\Scripts\python news_impact_server.py

echo  [OK] Launch command sent.  Waiting 5 seconds for startup...
timeout /t 5 /nobreak >nul

:: ── Verify it started ─────────────────────────────────────────────────────────
curl -s http://localhost:5003/api/impact/health >nul 2>&1
if errorlevel 1 (
    echo.
    echo  [WARN] Could not confirm the service is up yet.
    echo  It may still be starting.  Check in your browser in ~5 seconds:
    echo    http://localhost:5003/api/impact/health
) else (
    echo  [OK] Service is running at http://localhost:5003
)

echo.
echo  To stop the service: open Task Manager, Details tab, find python.exe
echo  Or run setup_autostart.bat so it starts automatically at login.

:done
echo.
echo  This window closes in 60 seconds -- press any key to close it now.
timeout /t 60
exit /b
