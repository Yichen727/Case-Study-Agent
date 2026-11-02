@echo off
setlocal
title Case Study Agent
cd /d "%~dp0"
if errorlevel 1 goto failed

echo Case Study Agent - preparing your local app...
echo.

if not exist ".venv\Scripts\python.exe" goto try_launcher
".venv\Scripts\python.exe" -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) and sys.maxsize > 2**32 else 1)" >nul 2>&1
if errorlevel 1 goto try_launcher
set "BOOTSTRAP_PYTHON=%~dp0.venv\Scripts\python.exe"
goto launch_python

:try_launcher
where.exe py.exe >nul 2>&1
if errorlevel 1 goto try_python
py.exe -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) and sys.maxsize > 2**32 else 1)" >nul 2>&1
if errorlevel 1 goto try_python
py.exe -3 "%~dp0start_app.py"
if errorlevel 1 goto failed
goto finished

:try_python
for /f "delims=" %%P in ('where.exe python.exe 2^>nul') do (
    if /i not "%%~dpP"=="%LOCALAPPDATA%\Microsoft\WindowsApps\" (
        "%%P" -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) and sys.maxsize > 2**32 else 1)" >nul 2>&1
        if not errorlevel 1 (
            set "BOOTSTRAP_PYTHON=%%P"
            goto launch_python
        )
    )
)

echo No compatible 64-bit Python was found.
where.exe winget.exe >nul 2>&1
if errorlevel 1 goto manual_python
echo Installing Python 3.13 for your Windows account. Internet access is required.
winget.exe install --id Python.Python.3.13 --exact --source winget --scope user --architecture x64 --silent --accept-package-agreements --accept-source-agreements --disable-interactivity
if errorlevel 1 goto manual_python
set "BOOTSTRAP_PYTHON=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not exist "%BOOTSTRAP_PYTHON%" goto manual_python

:launch_python
"%BOOTSTRAP_PYTHON%" "%~dp0start_app.py"
if errorlevel 1 goto failed
goto finished

:manual_python
echo.
echo Python could not be installed automatically on this computer.
echo Install 64-bit Python 3.10 or later from https://www.python.org/downloads/windows/
echo or ask IT to approve the installation, then double-click start.bat again.
goto failed

:failed
echo.
echo The app did not start successfully. Check the message above.
pause
exit /b 1

:finished
endlocal
exit /b 0
