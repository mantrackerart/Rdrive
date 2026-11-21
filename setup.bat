@echo off
rem Rdrive - Cai dat tu dong (may thieu gi tu tai: Python runtime, PySide6, rclone).
rem Chay lai bat cu luc nao de sua loi hoac cap nhat: buoc nao xong roi se tu bo qua.
cd /d "%~dp0"
title Rdrive - Cai dat tu dong
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\setup.ps1"
