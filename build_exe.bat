@echo off
setlocal
cd /d "%~dp0"

python -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name GenerateSave ^
  --add-data "_template;_template" ^
  --distpath "dist" ^
  --workpath "build" ^
  --specpath "." ^
  GenerateSaveGUI.py

if errorlevel 1 (
  echo.
  echo EXE build failed.
  exit /b 1
)

echo.
echo EXE created: %~dp0dist\GenerateSave.exe
endlocal
