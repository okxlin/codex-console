@echo off
setlocal
cd /d "%~dp0"

set "BOOTSTRAP_PYTHON="
where py >nul 2>nul
if not errorlevel 1 set "BOOTSTRAP_PYTHON=py -3"
if not defined BOOTSTRAP_PYTHON (
    where python >nul 2>nul
    if not errorlevel 1 set "BOOTSTRAP_PYTHON=python"
)
if not defined BOOTSTRAP_PYTHON (
    echo.
    echo [ERROR] Python was not found in PATH.
    echo         Please install Python 3 first.
    exit /b 1
)

set "VENV_PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%VENV_PYTHON%" (
    echo.
    echo [INFO] Python virtual environment not found. Creating .venv ...
    call %BOOTSTRAP_PYTHON% -m venv ".venv"
    if errorlevel 1 (
        echo [ERROR] Failed to create .venv
        exit /b 1
    )
)

if not exist "%VENV_PYTHON%" (
    echo.
    echo [ERROR] Virtual environment python still missing after creation:
    echo         %VENV_PYTHON%
    exit /b 1
)

set "TEST_ROOT=%~dp0runtime-tests\webui-test"
set "APP_DATA_DIR=%TEST_ROOT%\data"
set "APP_LOGS_DIR=%TEST_ROOT%\logs"

if not exist "%TEST_ROOT%" mkdir "%TEST_ROOT%"
if not exist "%APP_DATA_DIR%" mkdir "%APP_DATA_DIR%"
if not exist "%APP_LOGS_DIR%" mkdir "%APP_LOGS_DIR%"

if not defined WEBUI_HOST set "WEBUI_HOST=127.0.0.1"
if not defined WEBUI_PORT set "WEBUI_PORT=18000"
if not defined LOG_LEVEL set "LOG_LEVEL=INFO"

echo.
echo ================================================
echo   codex-console WebUI test mode
echo ================================================
echo   APP_DATA_DIR=%APP_DATA_DIR%
echo   APP_LOGS_DIR=%APP_LOGS_DIR%
echo   WEBUI_HOST=%WEBUI_HOST%
echo   WEBUI_PORT=%WEBUI_PORT%
echo   LOG_LEVEL=%LOG_LEVEL%
echo ================================================
echo.

call "%~dp0.venv\Scripts\activate.bat"
if errorlevel 1 (
    echo [ERROR] Failed to activate virtual environment.
    exit /b 1
)

set "REQUIREMENTS_FILE=%~dp0requirements.txt"
if exist "%REQUIREMENTS_FILE%" (
    python -c "import fastapi, uvicorn" >nul 2>nul
    if errorlevel 1 (
        echo [INFO] Installing project dependencies from requirements.txt ...
        python -m pip install --upgrade pip
        if errorlevel 1 (
            echo [ERROR] Failed to upgrade pip.
            exit /b 1
        )
        python -m pip install -r "%REQUIREMENTS_FILE%"
        if errorlevel 1 (
            echo [ERROR] Failed to install project dependencies.
            exit /b 1
        )
    )
)

python webui.py --host "%WEBUI_HOST%" --port %WEBUI_PORT% --log-level "%LOG_LEVEL%"
set "EXIT_CODE=%ERRORLEVEL%"

echo.
echo WebUI test mode exited with code %EXIT_CODE%.
exit /b %EXIT_CODE%
