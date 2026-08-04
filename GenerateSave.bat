@echo off
setlocal
python "%~dp0GenerateSave.py" %*
if errorlevel 1 pause
endlocal
