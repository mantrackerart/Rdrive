@echo off
cd /d "%~dp0"

if exist "runtime\pythonw.exe" (
  start "" "runtime\pythonw.exe" "%~dp0Rdrive.pyw"
  exit /b 0
)
if exist ".venv\Scripts\pythonw.exe" (
  start "" ".venv\Scripts\pythonw.exe" "%~dp0Rdrive.pyw"
  exit /b 0
)
if exist "..\SyAu\runtime\pythonw.exe" (
  start "" "..\SyAu\runtime\pythonw.exe" "%~dp0Rdrive.pyw"
  exit /b 0
)

echo Rdrive chua duoc cai dat day du. Dang mo setup.bat de tu dong tai thanh phan con thieu...
call "%~dp0setup.bat"
