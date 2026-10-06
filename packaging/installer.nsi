Unicode true
!define APP_VERSION "33.3"
Name "PoE2 Data Logger"
OutFile "${__FILEDIR__}\..\PoE2-Data-Logger-Setup-v${APP_VERSION}.exe"
VIProductVersion "${APP_VERSION}.0.0"
VIAddVersionKey /LANG=1033 "ProductName" "PoE2 Data Logger"
VIAddVersionKey /LANG=1033 "ProductVersion" "${APP_VERSION}"
VIAddVersionKey /LANG=1033 "FileVersion" "${APP_VERSION}"
VIAddVersionKey /LANG=1033 "FileDescription" "PoE2 Data Logger Installer"
VIAddVersionKey /LANG=1033 "LegalCopyright" "PoE2 Data Logger"
InstallDir "$PROGRAMFILES32\PoE2 Data Logger"
RequestExecutionLevel admin
SetCompressor /SOLID lzma
Icon "${__FILEDIR__}\..\PoE2_Data_Logger\power_rune.ico"
UninstallIcon "${__FILEDIR__}\..\PoE2_Data_Logger\power_rune.ico"
ShowInstDetails show
ShowUninstDetails show

Function .onInit
  System::Call 'user32::FindWindowW(p 0, w "PoE2 Data Logger") p.r0'
  StrCmp $0 "0" ready
    MessageBox MB_ICONEXCLAMATION|MB_OK "Close PoE2 Data Logger before installing this update."
    Abort
  ready:
FunctionEnd

Function un.onInit
  System::Call 'user32::FindWindowW(p 0, w "PoE2 Data Logger") p.r0'
  StrCmp $0 "0" ready
    MessageBox MB_ICONEXCLAMATION|MB_OK "Close PoE2 Data Logger before uninstalling."
    Abort
  ready:
FunctionEnd

Section "PoE2 Data Logger" Main
  SetShellVarContext all
  Delete "$DESKTOP\Runeshape Logger.lnk"
  RMDir /r "$INSTDIR\_internal"
  Delete "$INSTDIR\PoE2-Data-Logger.exe"
  SetOutPath "$INSTDIR"
  File /r "${__FILEDIR__}\..\dist\PoE2-Data-Logger\*"
  File "${__FILEDIR__}\..\PoE2_Data_Logger\CRAFTYXII_ASSETS_LICENSE.txt"
  CreateDirectory "$INSTDIR\Databases"
  nsExec::ExecToLog '"$SYSDIR\icacls.exe" "$INSTDIR\Databases" /grant *S-1-5-32-545:(OI)(CI)M'
  Pop $0
  StrCmp $0 "0" +2
    Abort "Could not set up the writable Databases folder."
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  CreateDirectory "$SMPROGRAMS\PoE2 Data Logger"
  CreateShortcut "$SMPROGRAMS\PoE2 Data Logger\PoE2 Data Logger.lnk" "$INSTDIR\PoE2-Data-Logger.exe" "" "$INSTDIR\PoE2-Data-Logger.exe" 0
  CreateShortcut "$DESKTOP\PoE2 Data Logger.lnk" "$INSTDIR\PoE2-Data-Logger.exe" "" "$INSTDIR\PoE2-Data-Logger.exe" 0
  CreateShortcut "$SMPROGRAMS\PoE2 Data Logger\Uninstall.lnk" "$INSTDIR\Uninstall.exe"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\PoE2DataLogger" "DisplayName" "PoE2 Data Logger"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\PoE2DataLogger" "DisplayVersion" "${APP_VERSION}"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\PoE2DataLogger" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\PoE2DataLogger" "DisplayIcon" "$INSTDIR\PoE2-Data-Logger.exe"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\PoE2DataLogger" "Publisher" "PoE2 Data Logger"
  WriteRegDWORD HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\PoE2DataLogger" "NoModify" 1
  WriteRegDWORD HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\PoE2DataLogger" "NoRepair" 1
SectionEnd

Section "Uninstall"
  SetShellVarContext all
  Delete "$DESKTOP\PoE2 Data Logger.lnk"
  Delete "$SMPROGRAMS\PoE2 Data Logger\PoE2 Data Logger.lnk"
  Delete "$SMPROGRAMS\PoE2 Data Logger\Uninstall.lnk"
  RMDir "$SMPROGRAMS\PoE2 Data Logger"
  RMDir /r "$INSTDIR\_internal"
  Delete "$INSTDIR\PoE2-Data-Logger.exe"
  Delete "$INSTDIR\CRAFTYXII_ASSETS_LICENSE.txt"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
  DeleteRegKey HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\PoE2DataLogger"
SectionEnd
