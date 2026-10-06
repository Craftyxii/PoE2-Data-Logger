@echo off
cd /d "%~dp0"
py -3.12 -m pip install -r requirements-build.txt
if errorlevel 1 exit /b 1
py -3.12 -m PyInstaller --clean --noconfirm packaging\PoE2-Data-Logger.spec
if errorlevel 1 exit /b 1
py -3.12 tools\verify_build.py dist\PoE2-Data-Logger\PoE2-Data-Logger.exe
if errorlevel 1 exit /b 1
py -3.12 -c "import sys; sys.path.insert(0, 'PoE2_Data_Logger'); from opened_scan import verify_models; verify_models('dist/PoE2-Data-Logger/_internal/rapidocr/models')"
if errorlevel 1 exit /b 1
makensis packaging\installer.nsi
if errorlevel 1 exit /b 1
