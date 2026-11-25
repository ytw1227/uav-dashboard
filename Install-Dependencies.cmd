@echo off
setlocal
pushd "%~dp0" || exit /b 1
if not exist ".venv\Scripts\python.exe" (
    py -3.12 -m venv .venv
    if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -B prepare_sample.py
if errorlevel 1 goto failed
echo Ready. Open Open-Shared-Dashboard.cmd.
pause
popd
exit /b 0
:failed
echo Setup failed. Install Python 3.12 and check the message above.
pause
popd
exit /b 1
