@echo off
rem Launch from the checkout root so package and resource paths resolve together.
cd /d "%~dp0"
py -3.12 -m PoE2_Data_Logger
if errorlevel 1 pause
