@echo off
:: Runs repo-sync from anywhere, with no activation or generated batch files.
:: Finds the project from this file's own location, makes sure the virtual
:: environment exists (setup_venv.bat), then runs sync.py.
::
:: Arguments come from the command line if there are any:
::   "C:\repo-sync\repo-sync.bat" check main DEVICE_B --no-popup --no-pause
::
:: With no arguments (for callers like the VENUS Shell step, which can only
:: run a file), they're read from the first line of repo-sync-args.txt in the
:: project folder, which is then deleted so a stale file never runs again:
::   check main DEVICE_B --repo Method-A --output "C:\results.csv" --no-popup --no-pause
::
:: The venv is .venv in the project folder, or REPO_SYNC_VENV if that is set.
:: Exit code is sync.py's, 2 if there were no arguments and no args file, or
:: setup_venv.bat's (10-12) if setup failed.

setlocal EnableExtensions

set "REPO_DIR=%~dp0"
set "ARGS_FILE=%REPO_DIR%repo-sync-args.txt"
if defined REPO_SYNC_VENV (
    set "VENV_DIR=%REPO_SYNC_VENV%"
) else (
    set "VENV_DIR=%REPO_DIR%.venv"
)

call "%REPO_DIR%setup_venv.bat" "%VENV_DIR%" >nul
if errorlevel 1 exit /b %ERRORLEVEL%

if not "%~1"=="" goto :run_with_command_line

if not exist "%ARGS_FILE%" (
    echo ERROR: No arguments given and "%ARGS_FILE%" not found. 1>&2
    exit /b 2
)
set "ARGS="
set /p ARGS=<"%ARGS_FILE%"
del "%ARGS_FILE%" >nul 2>&1
if not defined ARGS (
    echo ERROR: "%ARGS_FILE%" is empty. 1>&2
    exit /b 2
)
"%VENV_DIR%\Scripts\python.exe" "%REPO_DIR%sync.py" %ARGS%
exit /b %ERRORLEVEL%

:run_with_command_line
"%VENV_DIR%\Scripts\python.exe" "%REPO_DIR%sync.py" %*
exit /b %ERRORLEVEL%
