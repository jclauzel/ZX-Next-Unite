@echo off
REM Build syncdev.dot via build.ps1 (see that file for details).
REM Pass-through args, e.g.:  build.bat -Keep    or    build.bat -SdccBin "D:\sdcc\bin"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build.ps1" %*
exit /b %ERRORLEVEL%
