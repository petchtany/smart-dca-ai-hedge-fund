@echo off
chcp 65001 >nul
cd /d "C:\Users\P\ai-hedge-fund"
echo ========================================================
echo [1/2] Updating all AI Hedge Funds...
echo ========================================================
for /d %%D in ("%USERPROFILE%\.hedge-fund\paper\*") do (
    echo Ticking fund: %%~nxD
    "C:\Users\P\AppData\Local\pypoetry\Cache\virtualenvs\aihf-9cAGD6x--py3.14\Scripts\aihf.exe" paper tick "%%~nxD"
)

echo.
echo ========================================================
echo [2/2] Generating Smart DCA Dashboard and starting Add-Stock Server...
echo ========================================================
"C:\Users\P\AppData\Local\pypoetry\Cache\virtualenvs\aihf-9cAGD6x--py3.14\Scripts\python.exe" trade_plan.py --serve
pause
