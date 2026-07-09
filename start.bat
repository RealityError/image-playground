@echo off
setlocal

cd /d "%~dp0"
title image-playground

set "ROOT=%~dp0"
set "BACKEND=%ROOT%backend"
set "FRONTEND=%ROOT%frontend"
set "PYTHON=%BACKEND%\.venv\Scripts\python.exe"
set "PIP=%BACKEND%\.venv\Scripts\pip.exe"
set "PORT=8010"

rem Avoid inheriting a broken local proxy such as http://127.0.0.1:9.
set "HTTP_PROXY="
set "HTTPS_PROXY="
set "ALL_PROXY="
set "http_proxy="
set "https_proxy="
set "all_proxy="
set "GIT_HTTP_PROXY="
set "GIT_HTTPS_PROXY="

if not exist "%ROOT%.env" (
  echo [ERROR] .env not found in project root.
  echo Please copy .env.example to .env and fill in server settings first.
  echo Upstream providers and API keys are configured in the admin console.
  pause
  exit /b 1
)

for /f "usebackq tokens=1,* delims==" %%A in ("%ROOT%.env") do (
  if /I "%%A"=="PORT" set "PORT=%%B"
)

echo.
echo === image-playground ===
echo Project: %ROOT%
echo Port:    %PORT%
echo.

where node >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Node.js was not found. Please install Node.js first.
  pause
  exit /b 1
)

where npm >nul 2>nul
if errorlevel 1 (
  echo [ERROR] npm was not found. Please install Node.js/npm first.
  pause
  exit /b 1
)

if not exist "%PYTHON%" (
  echo [INFO] Creating backend virtual environment...
  python -m venv "%BACKEND%\.venv"
  if errorlevel 1 (
    echo [ERROR] Failed to create backend virtual environment.
    pause
    exit /b 1
  )
)

echo [INFO] Checking backend dependencies...
"%PYTHON%" -c "import fastapi, openai, PIL, dotenv, uvicorn" >nul 2>nul
if errorlevel 1 (
  echo [INFO] Installing backend dependencies...
  if exist "%PIP%" (
    "%PIP%" install -r "%BACKEND%\requirements.txt"
  ) else (
    python -m pip --python "%PYTHON%" install -r "%BACKEND%\requirements.txt"
  )
  if errorlevel 1 (
    echo [ERROR] Failed to install backend dependencies.
    pause
    exit /b 1
  )
)

if not exist "%FRONTEND%\node_modules" (
  echo [INFO] Installing frontend dependencies...
  pushd "%FRONTEND%"
  npm install --offline=false
  if errorlevel 1 (
    popd
    echo [ERROR] Failed to install frontend dependencies.
    pause
    exit /b 1
  )
  popd
)

if not exist "%FRONTEND%\dist\index.html" (
  echo [INFO] Building frontend...
  pushd "%FRONTEND%"
  npm run build
  if errorlevel 1 (
    popd
    echo [ERROR] Failed to build frontend.
    pause
    exit /b 1
  )
  popd
)

set "EXISTING_PID="
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":%PORT% .*LISTENING"') do set "EXISTING_PID=%%P"
if defined EXISTING_PID (
  echo [WARN] Port %PORT% is already listening. PID: %EXISTING_PID%
  choice /C YN /N /M "Stop the running process and restart here? [Y/N] "
  if errorlevel 2 (
    echo [INFO] Keeping the existing server.
    echo URL: http://127.0.0.1:%PORT%
    pause
    exit /b 0
  )

  echo [INFO] Stopping PID %EXISTING_PID%...
  taskkill /PID %EXISTING_PID% /F
  if errorlevel 1 (
    echo [ERROR] Failed to stop PID %EXISTING_PID%.
    pause
    exit /b 1
  )
  timeout /t 2 /nobreak >nul
)

echo [INFO] Starting backend server...
echo URL: http://127.0.0.1:%PORT%
echo Press Ctrl+C in this window to stop the server.
echo.

pushd "%BACKEND%"
"%PYTHON%" -m uvicorn app:app --host 0.0.0.0 --port %PORT%
set "EXIT_CODE=%ERRORLEVEL%"
popd

echo.
echo [INFO] Server stopped. Exit code: %EXIT_CODE%
pause
exit /b %EXIT_CODE%
