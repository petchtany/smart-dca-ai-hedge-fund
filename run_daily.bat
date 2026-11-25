@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "PY_EXE=python"
if exist "%~dp0.venv\Scripts\python.exe" set "PY_EXE=%~dp0.venv\Scripts\python.exe"

set "AIHF_EXE=aihf"
if exist "%~dp0.venv\Scripts\aihf.exe" set "AIHF_EXE=%~dp0.venv\Scripts\aihf.exe"

echo ========================================================
echo [1/2] Updating AI Hedge Funds (if configured)...
echo ========================================================
if exist "%USERPROFILE%\.hedge-fund\paper" (
    for /d %%D in ("%USERPROFILE%\.hedge-fund\paper\*") do (
        echo Ticking fund: %%~nxD
        "%AIHF_EXE%" paper tick "%%~nxD"
    )
) else (
    echo No local paper funds found in %USERPROFILE%\.hedge-fund\paper - skipping.
)

echo.
echo ========================================================
echo [2/2] Generating Smart DCA Dashboard v4.0 and starting API Server...
echo ========================================================
"%PY_EXE%" trade_plan.py --refresh --serve
pause
