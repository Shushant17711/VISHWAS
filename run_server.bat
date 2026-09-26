@echo off
set PORT=8000

echo Checking for existing server on port %PORT%...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":%PORT%" ^| findstr "LISTENING"') do (
    echo Found existing server with PID: %%a
    echo Stopping existing server...
    taskkill /F /PID %%a
)

echo Starting VISHWAS server on port %PORT%...
python -m uvicorn vishwas.api.app:app --host 127.0.0.1 --port %PORT% --reload
