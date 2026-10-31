@echo off
setlocal
title TEMU Workbench Backend Restart
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0restart-backend.ps1"
set "TASK_EXIT_CODE=%ERRORLEVEL%"
if not "%TASK_EXIT_CODE%"=="0" echo.
if not "%TASK_EXIT_CODE%"=="0" echo Restart did not finish. Review the message above, then run this file again.
pause
exit /b %TASK_EXIT_CODE%
