@echo off
setlocal
rem ========================================================
rem  Drag files/folders onto this .bat to extract them.
rem  Requires Python 3.10+ (uses "py" launcher if present).
rem  NOTE: keep this file ASCII-only, cmd.exe parses it as OEM codepage.
rem ========================================================

set "SCRIPT=%~dp0complex-unzip-tool-mod.py"

if not exist "%SCRIPT%" (
    echo [ERROR] complex-unzip-tool-mod.py not found next to this .bat
    pause
    exit /b 1
)

set "PYCMD="
where py >nul 2>nul
if not errorlevel 1 set "PYCMD=py -3"

if not defined PYCMD (
    where python >nul 2>nul
    if not errorlevel 1 set "PYCMD=python"
)

if not defined PYCMD (
    echo [ERROR] Python not found. Install Python 3.10+ and check "Add to PATH".
    pause
    exit /b 1
)

%PYCMD% "%SCRIPT%" %*
if errorlevel 1 (
    echo.
    echo [ERROR] exited with code %errorlevel%
    pause
)
