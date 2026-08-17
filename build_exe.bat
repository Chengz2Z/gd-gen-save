@echo off
setlocal
cd /d "%~dp0"

set "MODE=%~1"
if "%MODE%"=="" set "MODE=licensed"

if /i "%MODE%"=="licensed" (
  set "ENTRY=GenerateSaveGUI.py"
  set "APP_NAME=GenerateSave"
  set "DIST_DIR=dist"
) else if /i "%MODE%"=="free" (
  set "ENTRY=GenerateSaveGUIFree.py"
  set "APP_NAME=GenerateSave-Free"
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
