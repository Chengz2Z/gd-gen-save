@echo off
setlocal
cd /d "%~dp0"

for /f "usebackq delims=" %%V in (`python -c "import sys; sys.path.insert(0, r'src'); from app_version import APP_VERSION; print(APP_VERSION)"`) do set "APP_VERSION=%%V"
if not defined APP_VERSION (
  echo Failed to read application version from src\app_version.py.
  exit /b 1
)

set "MODE=%~1"
if "%MODE%"=="" set "MODE=licensed"

if /i "%MODE%"=="licensed" (
  set "ENTRY=src\GenerateSaveGUI.py"
  set "APP_NAME=GenerateSave-%APP_VERSION%"
) else if /i "%MODE%"=="free" (
  set "ENTRY=src\GenerateSaveGUIFree.py"
  set "APP_NAME=GenerateSave-Free-%APP_VERSION%"
) else (
  echo Usage: build_exe.bat [licensed^|free]
  exit /b 2
)

set "RELEASE_DIR=artifacts\releases\GenerateSave-%APP_VERSION%\%MODE%"
set "SPEC_DIR=artifacts\spec"
if not exist "%RELEASE_DIR%" mkdir "%RELEASE_DIR%"
if not exist "%SPEC_DIR%" mkdir "%SPEC_DIR%"

python -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name "%APP_NAME%" ^
  --add-data "%~dp0resources\_template;_template" ^
  --add-data "%~dp0resources\database;database" ^
  --add-data "%~dp0resources\languages;languages" ^
  --distpath "%RELEASE_DIR%" ^
  --workpath "artifacts\build\%MODE%" ^
  --specpath "%SPEC_DIR%" ^
  "%ENTRY%"

if errorlevel 1 (
  echo.
  echo %MODE% EXE build failed.
  exit /b 1
)

xcopy /E /I /Y "resources\languages" "%RELEASE_DIR%\languages" >nul
if errorlevel 1 (
  echo Failed to copy language packs.
  exit /b 1
)

echo.
echo %MODE% release created: %~dp0%RELEASE_DIR%
endlocal
