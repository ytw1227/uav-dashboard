@echo off
setlocal
pushd "%~dp0" || exit /b 1
if not exist ".venv\Scripts\python.exe" (
    echo Install the Python environment first. See README.md.
    popd
    exit /b 1
)
".venv\Scripts\python.exe" -B -m nicegui_dashboard app %*
set "dashboard_exit=%errorlevel%"
popd
exit /b %dashboard_exit%
