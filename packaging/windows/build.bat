@echo off
setlocal enabledelayedexpansion

rem === Dam Monitoring Dashboard - Windows build script ===
rem Installs Python 3.12.10 into a dedicated per-user folder (if not
rem already there), installs the project's requirements + PyInstaller,
rem and builds the Windows .exe.
rem
rem Run this from the repo root (the folder containing gui.py), e.g.:
rem     packaging\windows\build.bat

set "PY_VERSION=3.12.10"
set "PY_INSTALLER_URL=https://www.python.org/ftp/python/%PY_VERSION%/python-%PY_VERSION%-amd64.exe"
set "PY_INSTALL_DIR=%LocalAppData%\Programs\DamMonitoringPython312"
set "PYTHON_EXE=%PY_INSTALL_DIR%\python.exe"

if not exist "requirements.txt" (
    echo ERROR: requirements.txt not found in the current directory.
    echo Run this script from the repo root, e.g.: packaging\windows\build.bat
    exit /b 1
)
if not exist "packaging\windows\app.spec" (
    echo ERROR: packaging\windows\app.spec not found in the current directory.
    echo Run this script from the repo root, e.g.: packaging\windows\build.bat
    exit /b 1
)

echo.
echo === Step 1/5: Checking for Python %PY_VERSION% ===
if exist "%PYTHON_EXE%" (
    echo Found existing install at "%PYTHON_EXE%".
) else (
    echo Not found. Downloading Python %PY_VERSION% installer...
    set "INSTALLER=%TEMP%\python-%PY_VERSION%-amd64.exe"
    powershell -NoProfile -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '%PY_INSTALLER_URL%' -OutFile '!INSTALLER!' -UseBasicParsing"
    if not exist "!INSTALLER!" (
        echo ERROR: download failed. Check your internet connection and try again.
        exit /b 1
    )

    echo Installing to "%PY_INSTALL_DIR%" ^(no admin rights required^)...
    rem /quiet: unattended. A dedicated per-user TargetDir means this won't
    rem touch or conflict with any other Python already on this machine.
    rem PrependPath=1 also adds it to PATH for future terminal sessions --
    rem this script still uses the full path below either way, so it
    rem doesn't need to wait for that to take effect.
    "!INSTALLER!" /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=0 TargetDir="%PY_INSTALL_DIR%"

    if not exist "%PYTHON_EXE%" (
        echo ERROR: Python install did not complete as expected.
        echo Try running this manually to see the installer UI and any error:
        echo   "!INSTALLER!"
        exit /b 1
    )
    echo Installed Python %PY_VERSION% to "%PY_INSTALL_DIR%".
)

echo.
echo === Step 2/5: Upgrading pip ===
"%PYTHON_EXE%" -m pip install --upgrade pip
if errorlevel 1 goto :pip_error

echo.
echo === Step 3/5: Installing project requirements ===
"%PYTHON_EXE%" -m pip install -r requirements.txt
if errorlevel 1 goto :pip_error

echo.
echo === Step 4/5: Installing PyInstaller ===
"%PYTHON_EXE%" -m pip install pyinstaller pyinstaller-hooks-contrib
if errorlevel 1 goto :pip_error

echo.
echo === Step 5/5: Building the executable ===
"%PYTHON_EXE%" -m PyInstaller packaging\windows\app.spec
if errorlevel 1 (
    echo.
    echo Build failed -- see the PyInstaller output above.
    exit /b 1
)

echo.
echo === Done ===
echo The built app is:  dist\DamMonitoringDashboard.exe
exit /b 0

:pip_error
echo.
echo A pip step failed -- see the output above.
exit /b 1
