@echo off
cd /d "%~dp0"
set PY=
if exist "runtime\python.exe" set PY=runtime\python.exe
if not defined PY if exist ".venv\Scripts\python.exe" set PY=.venv\Scripts\python.exe
if not defined PY if exist "..\SyAu\runtime\python.exe" set PY=..\SyAu\runtime\python.exe
if not defined PY if exist "..\SyAu\.venv\Scripts\python.exe" set PY=..\SyAu\.venv\Scripts\python.exe
if not defined PY set PY=python

echo Running Rdrive with: %PY%
"%PY%" -m rdrive
pause
