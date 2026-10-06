@echo off
cd /d "%~dp0PoE2_Data_Logger"
py -3.12 -m native_desktop
if errorlevel 1 pause
