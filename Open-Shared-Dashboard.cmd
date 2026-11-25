@echo off
setlocal
pushd "%~dp0" || exit /b 1
if not exist ".venv\Scripts\python.exe" (
    echo Run Install-Dependencies.cmd first.
    pause
    popd
    exit /b 1
)
".venv\Scripts\python.exe" -B prepare_sample.py
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -B -m nicegui_dashboard.desktop
if errorlevel 1 goto failed
popd
exit /b 0
:failed
echo Dashboard startup failed. Check the message above and data\logs.
pause
popd
exit /b 1
