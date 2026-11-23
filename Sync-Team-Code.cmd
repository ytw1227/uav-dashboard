@echo off
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
pushd "%~dp0" || exit /b 1
if not exist ".venv\Scripts\python.exe" (
    echo Install the dashboard Python environment first. See README.md.
    set "sync_exit=1"
    goto finish
)
".venv\Scripts\python.exe" -B -m nicegui_dashboard team-sync
set "sync_exit=%errorlevel%"
:finish
popd
if /i not "%~1"=="--no-pause" pause
exit /b %sync_exit%
