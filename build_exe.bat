@echo off
setlocal
cd /d "%~dp0"

for /f "usebackq delims=" %%V in (`python -c "from app_version import APP_VERSION; print(APP_VERSION)"`) do set "APP_VERSION=%%V"
if not defined APP_VERSION (
  echo Failed to read application version from app_version.py.
  exit /b 1
)

set "MODE=%~1"
if "%MODE%"=="" set "MODE=licensed"

if /i "%MODE%"=="licensed" (
  set "ENTRY=GenerateSaveGUI.py"
  set "APP_NAME=GenerateSave-%APP_VERSION%"
  set "DIST_DIR=dist"
) else if /i "%MODE%"=="free" (
  set "ENTRY=GenerateSaveGUIFree.py"
  set "APP_NAME=GenerateSave-Free-%APP_VERSION%"
  set "DIST_DIR=dist"
) else (
  echo Usage: build_exe.bat [licensed^|free]
  exit /b 2
)

python -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name "%APP_NAME%" ^
  --add-data "_template;_template" ^
  --add-data "database;database" ^
  --distpath "%DIST_DIR%" ^
  --workpath "build\%MODE%" ^
  --specpath "." ^
  "%ENTRY%"

if errorlevel 1 (
  echo.
  echo %MODE% EXE build failed.
  exit /b 1
)

echo.
echo %MODE% EXE created: %~dp0%DIST_DIR%\%APP_NAME%.exe
endlocal
