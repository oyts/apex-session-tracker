@echo off
setlocal
cd /d "%~dp0"
if exist "dist\v14-beta4\ApexTracker\ApexTracker.exe" (
    start "" "dist\v14-beta4\ApexTracker\ApexTracker.exe"
    exit /b
)
if exist "dist\v14-beta3\ApexTracker\ApexTracker.exe" (
    start "" "dist\v14-beta3\ApexTracker\ApexTracker.exe"
    exit /b
)
if exist "dist\v14-beta2\ApexTracker\ApexTracker.exe" (
    start "" "dist\v14-beta2\ApexTracker\ApexTracker.exe"
    exit /b
)
if exist "dist\v14-beta1\ApexTracker\ApexTracker.exe" (
    start "" "dist\v14-beta1\ApexTracker\ApexTracker.exe"
    exit /b
)
if exist "dist\v14\ApexTracker\ApexTracker.exe" (
    start "" "dist\v14\ApexTracker\ApexTracker.exe"
    exit /b
)
if exist "dist\v13\ApexTracker\ApexTracker.exe" (
    start "" "dist\v13\ApexTracker\ApexTracker.exe"
    exit /b
)
if exist "dist\v12\ApexTracker\ApexTracker.exe" (
    start "" "dist\v12\ApexTracker\ApexTracker.exe"
    exit /b
)
if exist "dist\v11\ApexTracker\ApexTracker.exe" (
    start "" "dist\v11\ApexTracker\ApexTracker.exe"
    exit /b
)
if exist "dist\v10\ApexTracker\ApexTracker.exe" (
    start "" "dist\v10\ApexTracker\ApexTracker.exe"
    exit /b
)
if exist "dist\v9\ApexTracker\ApexTracker.exe" (
    start "" "dist\v9\ApexTracker\ApexTracker.exe"
    exit /b
)
if exist "dist\v8\ApexTracker\ApexTracker.exe" (
    start "" "dist\v8\ApexTracker\ApexTracker.exe"
    exit /b
)
if exist "dist\ApexTracker\ApexTracker.exe" (
    start "" "dist\ApexTracker\ApexTracker.exe"
    exit /b
)
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" "main.py"
    exit /b
)
echo Please extract the full ApexTracker folder and run ApexTracker.exe.
pause
