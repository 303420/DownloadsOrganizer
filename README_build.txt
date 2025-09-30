
Build to EXE (Windows)
----------------------
1) Double‑click build_exe.bat  (it will install PyInstaller for your Python user and build)
2) The EXE will appear in .\dist\DownloadsOrganizer.exe
3) Keep config.json next to the EXE (you can edit it anytime)
4) Run once to test:
   dist\DownloadsOrganizer.exe --once --dry
5) For background auto‑run at logon:
   double‑click install_autorun.bat

Notes
-----
- No virtualenv required. Uses your system Python.
- The script is freeze‑safe: it loads config.json from the EXE folder.
- Logs are written to .\logs\organizer.log (next to the EXE).
