@echo off
:: Creates the repo-sync virtual environment if it doesn't exist, and installs
:: requirements.txt into it. Safe to call before every run: when the venv
:: already exists and requirements.txt hasn't changed, it does nothing.
::
:: Usage:  setup_venv.bat [VENV_DIR]
::   VENV_DIR defaults to .venv next to this file. Relative paths are resolved
::   against the current directory.
::
:: Afterwards, run the tool with the venv's Python directly (no activation):
::   "VENV_DIR\Scripts\python.exe" "<repo>\sync.py" sync main DEVICE_B --no-popup --no-pause
::
:: Also sets the REPO_SYNC_HOME user environment variable to this folder.
::
:: Exit codes:
::   0   venv ready
::   10  no Python 3.10+ found (tried "py -3" and "python")
::   11  creating the venv failed
::   12  installing requirements failed

setlocal EnableExtensions

set "REPO_DIR=%~dp0"
set "REQUIREMENTS=%REPO_DIR%requirements.txt"

if "%~1"=="" (
    set "VENV_DIR=%REPO_DIR%.venv"
) else (
    set "VENV_DIR=%~f1"
)
:: Drop a trailing backslash so "%VENV_DIR%\Scripts" stays well formed.
if "%VENV_DIR:~-1%"=="\" set "VENV_DIR=%VENV_DIR:~0,-1%"

set "VENV_PY=%VENV_DIR%\Scripts\python.exe"
:: Copy of the requirements last installed successfully. Missing or different
:: means install again; it's only written after pip succeeds.
set "STAMP=%VENV_DIR%\repo-sync-requirements.txt"

if exist "%VENV_PY%" goto :check_requirements

echo Creating virtual environment in "%VENV_DIR%"

call :find_python
if errorlevel 1 (
    echo ERROR: Python 3.10 or later was not found. Install it from https://www.python.org/ 1>&2
    exit /b 10
)

%BASE_PY% -m venv "%VENV_DIR%"
if errorlevel 1 goto :venv_failed
if not exist "%VENV_PY%" goto :venv_failed
goto :install

:check_requirements
if not exist "%STAMP%" goto :install
fc /b "%REQUIREMENTS%" "%STAMP%" >nul 2>&1
if errorlevel 1 goto :install
echo Virtual environment is up to date: "%VENV_DIR%"
goto :register_home

:install
echo Installing requirements into "%VENV_DIR%"
"%VENV_PY%" -m pip install --disable-pip-version-check --quiet -r "%REQUIREMENTS%"
if errorlevel 1 (
    echo ERROR: pip install failed. Check the network connection and try again. 1>&2
    exit /b 12
)
copy /y "%REQUIREMENTS%" "%STAMP%" >nul
echo Virtual environment is ready: "%VENV_DIR%"
goto :register_home

:: Points the per-user REPO_SYNC_HOME environment variable at this folder, so
:: batch files outside the project can run "%REPO_SYNC_HOME%\repo-sync.bat".
:: Skipped when it's already right. Programs already open, like VENUS, only see
:: a new value after a restart. A failure here is a warning, not an error.
:register_home
set "HOME_DIR=%REPO_DIR:~0,-1%"
if /i "%REPO_SYNC_HOME%"=="%HOME_DIR%" exit /b 0
setx REPO_SYNC_HOME "%HOME_DIR%" >nul 2>&1
if errorlevel 1 goto :register_failed
echo Set REPO_SYNC_HOME to "%HOME_DIR%". Restart VENUS so it sees the change.
exit /b 0

:register_failed
echo WARNING: Could not set the REPO_SYNC_HOME environment variable. 1>&2
exit /b 0

:venv_failed
echo ERROR: Could not create the virtual environment in "%VENV_DIR%" 1>&2
exit /b 11

:: Sets BASE_PY to a command that runs Python 3.10+, or returns 1.
:find_python
set "BASE_PY="
py -3 -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>&1
if not errorlevel 1 (
    set "BASE_PY=py -3"
    exit /b 0
)
python -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>&1
if not errorlevel 1 (
    set "BASE_PY=python"
    exit /b 0
)
exit /b 1
