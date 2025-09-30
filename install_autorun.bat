@echo off
REM Creates a Task Scheduler job to run the app on logon (no admin needed).
set "APP=%~dp0dist\DownloadsOrganizer.exe"
if not exist "%APP%" (
  echo Build the EXE first (run build_exe.bat).
  pause
  exit /b 1
)
schtasks /Create /TN "DownloadsOrganizer" /TR "\"%APP%\" --watch" /SC ONLOGON /RL LIMITED /F
echo Task created. It will run the app on logon.
pause
