@echo off
cd /d "%~dp0"
title STRUCT.ai News Impact Service - Install
color 0B

echo.
echo  =========================================================
echo   STRUCT.ai News Impact Service - Installing Dependencies
echo  =========================================================
echo.
echo  This creates a self-contained virtual environment in the
echo  current folder. No admin rights required.
echo.
echo  Folder: %CD%
echo.

:: ── Find Python ───────────────────────────────────────────────────────────────
:: Try the Windows Python Launcher first (py.exe) — it is not affected by the
:: Microsoft Store alias that can make "python" appear to exist but do nothing.
set PYTHON=
py --version >nul 2>&1
if not errorlevel 1 (
    set PYTHON=py
    goto :python_ok
)
python --version >nul 2>&1
if not errorlevel 1 (
    set PYTHON=python
    goto :python_ok
)

echo  [ERROR] Python was not found on this computer.
echo.
echo  Fix: Download Python 3.10 or newer from https://python.org
echo  On the installer screen, tick "Add Python to PATH" before clicking Install.
echo.
echo  After installing Python, run this file again.
goto :done

:python_ok
for /f "tokens=*" %%i in ('%PYTHON% --version 2^>^&1') do set PYVER=%%i
echo  [OK] Found %PYVER% (using command: %PYTHON%)
echo.

:: ── Create virtual environment ────────────────────────────────────────────────
if exist venv\Scripts\python.exe (
    echo  [OK] Virtual environment already exists - skipping creation.
) else (
    echo  [..] Creating virtual environment in venv\ ...
    %PYTHON% -m venv venv
    if errorlevel 1 (
        echo.
        echo  [ERROR] Could not create the virtual environment.
        echo.
        echo  Fix: run this command in a terminal and try again:
        echo    %PYTHON% -m ensurepip
        goto :done
    )
    echo  [OK] Virtual environment created.
)
echo.

:: ── Install packages ──────────────────────────────────────────────────────────
echo  [..] Installing packages into venv (flask, requests, python-dotenv)...
venv\Scripts\pip install -r requirements.txt --quiet --disable-pip-version-check
if errorlevel 1 (
    echo.
    echo  [ERROR] Package installation failed.
    echo.
    echo  Fix: delete the venv\ folder and run install.bat again.
    echo  If the problem continues, check your internet connection.
    goto :done
)
echo  [OK] All packages installed.
echo.

:: ── Create .env if missing ────────────────────────────────────────────────────
if not exist .env (
    echo  [SETUP] Creating .env from template...
    copy .env.example .env >nul
    echo.
    echo  [!!] Notepad will open now.
    echo  [!!] Replace  your_key_here  with your FinnHub API key and save.
    echo  [!!] Get a free key at: https://finnhub.io (takes 2 minutes)
    echo.
    notepad .env
) else (
    echo  [OK] .env already exists.
)

echo.
echo  =========================================================
echo   Installation complete!  Next step: run start.bat
echo  =========================================================

:done
echo.
echo  This window closes in 60 seconds -- press any key to close it now.
timeout /t 60
exit /b
