@echo off
cd /d "%~dp0"
py -3.12 -m PoE2_Data_Logger
if errorlevel 1 pause
