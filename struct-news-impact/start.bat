@echo off
cd /d "%~dp0"
title STRUCT.ai News Impact Service
color 0B

echo.
echo  =========================================================
echo   STRUCT.ai News Impact Service
echo  =========================================================
echo.

:: ── Pre-flight checks ─────────────────────────────────────────────────────────
if not exist venv\Scripts\python.exe (
    echo  [ERROR] Virtual environment not found.
    echo.
    echo  Fix: run install.bat first, then try start.bat again.
    goto :done
)

if not exist .env (
    echo  [ERROR] .env file not found.
    echo.
    echo  Fix: run install.bat first and add your FINNHUB_API_KEY to the .env file.
    goto :done
)

echo  [OK] Environment ready.
echo.
echo  Starting service on http://localhost:5003
echo  Open that address in your browser to see the live dashboard.
echo  Keep this window open while trading.  Press Ctrl+C to stop.
echo.

venv\Scripts\python news_impact_server.py

echo.
echo  [STOPPED] The service has stopped.

:done
echo.
echo  This window closes in 60 seconds -- press any key to close it now.
timeout /t 60
exit /b
