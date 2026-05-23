@echo off
cd /d "%~dp0"
title STRUCT.ai News Impact Service - Tests
color 0A

echo.
echo  =========================================================
echo   STRUCT.ai News Impact Service - Test Suite
echo   (No internet connection required - all calls are mocked)
echo  =========================================================
echo.

:: ── Pre-flight check ──────────────────────────────────────────────────────────
if not exist venv\Scripts\python.exe (
    echo  [ERROR] Virtual environment not found.
    echo.
    echo  Fix: run install.bat first, then try run_tests.bat again.
    goto :done
)

echo  Running 265 tests...
echo.

venv\Scripts\python -m pytest tests/ -v --tb=short

echo.
echo  =========================================================
if errorlevel 1 (
    echo   Some tests FAILED.  See details above.
    color 0C
) else (
    echo   All tests PASSED.
    color 0A
)
echo  =========================================================

:done
echo.
echo  This window closes in 60 seconds -- press any key to close it now.
timeout /t 60
exit /b
