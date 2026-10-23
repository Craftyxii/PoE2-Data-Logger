@echo off
rem Build with Python 3.12, then verify the frozen app and OCR assets before NSIS.
rem Each failed command stops the pipeline rather than packaging partial output.
cd /d "%~dp0"
py -3.12 -m pip install --upgrade "pip>=26.2"
if errorlevel 1 exit /b 1
py -3.12 -m pip install -r requirements-build.txt
if errorlevel 1 exit /b 1
py -3.12 -m PyInstaller --clean --noconfirm packaging\PoE2-Data-Logger.spec
if errorlevel 1 exit /b 1
py -3.12 tools\verify_build.py dist\PoE2-Data-Logger\PoE2-Data-Logger.exe
if errorlevel 1 exit /b 1
py -3.12 -c "from PoE2_Data_Logger.ocr.opened_scan import verify_models; verify_models('dist/PoE2-Data-Logger/_internal/rapidocr/models')"
if errorlevel 1 exit /b 1
makensis "%~dp0packaging\installer.nsi"
if errorlevel 1 exit /b 1
