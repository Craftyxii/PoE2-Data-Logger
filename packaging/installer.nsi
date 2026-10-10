Unicode true
; Install the frozen runtime and shortcuts while leaving user database files
; outside the replaceable _internal directory.
!define APP_VERSION "1.3.2.7"
!define APP_CHANNEL "beta"
!define APP_DISPLAY_VERSION "${APP_VERSION} Beta"
!define APP_RELEASE_VERSION "${APP_VERSION}-${APP_CHANNEL}"
!define APP_NAME "PoE2 Data Logger ${APP_DISPLAY_VERSION}"
Name "${APP_NAME}"
OutFile "${__FILEDIR__}\..\PoE2-Data-Logger-Setup-v${APP_RELEASE_VERSION}.exe"
VIProductVersion "1.3.2.7"
VIAddVersionKey /LANG=1033 "ProductName" "${APP_NAME}"
VIAddVersionKey /LANG=1033 "ProductVersion" "${APP_DISPLAY_VERSION}"
VIAddVersionKey /LANG=1033 "FileVersion" "${APP_DISPLAY_VERSION}"
VIAddVersionKey /LANG=1033 "FileDescription" "${APP_NAME} Installer"
VIAddVersionKey /LANG=1033 "LegalCopyright" "PoE2 Data Logger"
InstallDir "$PROGRAMFILES32\PoE2 Data Logger"
RequestExecutionLevel admin
SetCompressor /SOLID lzma
Icon "${__FILEDIR__}\..\PoE2_Data_Logger\power_rune.ico"
UninstallIcon "${__FILEDIR__}\..\PoE2_Data_Logger\power_rune.ico"
ShowInstDetails show
ShowUninstDetails show

Function .onInit
  ; Refuse installation while a recognized logger window is open, preventing
  ; replacement of files still loaded by the app.
  System::Call 'user32::FindWindowW(p 0, w "PoE2 Data Logger") p.r0'
  StrCmp $0 "0" check_previous_beta running
  check_previous_beta:
    System::Call 'user32::FindWindowW(p 0, w "PoE2 Data Logger 33.34 Beta") p.r0'
    StrCmp $0 "0" check_patch_beta running
  check_patch_beta:
    System::Call 'user32::FindWindowW(p 0, w "PoE2 Data Logger 33.34.1 Beta") p.r0'
    StrCmp $0 "0" check_atlas_beta running
  check_atlas_beta:
    System::Call 'user32::FindWindowW(p 0, w "PoE2 Data Logger 1.1 Beta") p.r0'
    StrCmp $0 "0" check_activity_beta running
  check_activity_beta:
    System::Call 'user32::FindWindowW(p 0, w "PoE2 Data Logger 1.2 Beta") p.r0'
    StrCmp $0 "0" check_ritual_beta running
  check_ritual_beta:
    System::Call 'user32::FindWindowW(p 0, w "PoE2 Data Logger 1.2.1 Beta") p.r0'
    StrCmp $0 "0" check_currency_beta running
  check_currency_beta:
    System::Call 'user32::FindWindowW(p 0, w "PoE2 Data Logger 1.2.2 Beta") p.r0'
    StrCmp $0 "0" check_session_beta running
  check_session_beta:
    System::Call 'user32::FindWindowW(p 0, w "PoE2 Data Logger 1.3 Beta") p.r0'
    StrCmp $0 "0" check_chain_beta running
  check_chain_beta:
    System::Call 'user32::FindWindowW(p 0, w "PoE2 Data Logger 1.3.1 Beta") p.r0'
    StrCmp $0 "0" check_integrity_beta running
  check_integrity_beta:
    System::Call 'user32::FindWindowW(p 0, w "PoE2 Data Logger 1.3.1.1 Beta") p.r0'
    StrCmp $0 "0" check_chain_completion_beta running
  check_chain_completion_beta:
    ; Retain earlier running clients when the beta replaces their runtime.
    System::Call 'user32::FindWindowW(p 0, w "PoE2 Data Logger 1.3.1.2 Beta") p.r0'
    StrCmp $0 "0" check_review_beta running
  check_review_beta:
    System::Call 'user32::FindWindowW(p 0, w "PoE2 Data Logger 1.3.1.3 Beta") p.r0'
    StrCmp $0 "0" check_previous_stable running
  check_previous_stable:
    ; The beta must not replace files still loaded by the stable client.
    System::Call 'user32::FindWindowW(p 0, w "PoE2 Data Logger 1.3.2") p.r0'
    StrCmp $0 "0" check_previous_patch_beta running
  check_previous_patch_beta:
    System::Call 'user32::FindWindowW(p 0, w "PoE2 Data Logger 1.3.2.4 Beta") p.r0'
    StrCmp $0 "0" check_previous_layout_beta running
  check_previous_layout_beta:
    ; Keep the previous beta's loaded runtime intact while updating.
    System::Call 'user32::FindWindowW(p 0, w "PoE2 Data Logger 1.3.2.5 Beta") p.r0'
    StrCmp $0 "0" check_previous_audit_beta running
  check_previous_audit_beta:
    ; Do not replace or delete runtime files while the preceding beta uses them.
    System::Call 'user32::FindWindowW(p 0, w "PoE2 Data Logger 1.3.2.6 Beta") p.r0'
    StrCmp $0 "0" check_current running
  check_current:
    System::Call 'user32::FindWindowW(p 0, w "${APP_NAME}") p.r0'
    StrCmp $0 "0" ready
  running:
    MessageBox MB_ICONEXCLAMATION|MB_OK "Close PoE2 Data Logger before installing this update." /SD IDOK
    SetErrorLevel 2
    Abort
  ready:
FunctionEnd

Function un.onInit
  ; Apply the same running-window guard before removing the installed runtime.
  System::Call 'user32::FindWindowW(p 0, w "PoE2 Data Logger") p.r0'
  StrCmp $0 "0" check_previous_beta running
  check_previous_beta:
    System::Call 'user32::FindWindowW(p 0, w "PoE2 Data Logger 33.34 Beta") p.r0'
    StrCmp $0 "0" check_patch_beta running
  check_patch_beta:
    System::Call 'user32::FindWindowW(p 0, w "PoE2 Data Logger 33.34.1 Beta") p.r0'
    StrCmp $0 "0" check_atlas_beta running
  check_atlas_beta:
    System::Call 'user32::FindWindowW(p 0, w "PoE2 Data Logger 1.1 Beta") p.r0'
    StrCmp $0 "0" check_activity_beta running
  check_activity_beta:
    System::Call 'user32::FindWindowW(p 0, w "PoE2 Data Logger 1.2 Beta") p.r0'
    StrCmp $0 "0" check_ritual_beta running
  check_ritual_beta:
    System::Call 'user32::FindWindowW(p 0, w "PoE2 Data Logger 1.2.1 Beta") p.r0'
    StrCmp $0 "0" check_currency_beta running
  check_currency_beta:
    System::Call 'user32::FindWindowW(p 0, w "PoE2 Data Logger 1.2.2 Beta") p.r0'
    StrCmp $0 "0" check_session_beta running
  check_session_beta:
    System::Call 'user32::FindWindowW(p 0, w "PoE2 Data Logger 1.3 Beta") p.r0'
    StrCmp $0 "0" check_chain_beta running
  check_chain_beta:
    System::Call 'user32::FindWindowW(p 0, w "PoE2 Data Logger 1.3.1 Beta") p.r0'
    StrCmp $0 "0" check_integrity_beta running
  check_integrity_beta:
    System::Call 'user32::FindWindowW(p 0, w "PoE2 Data Logger 1.3.1.1 Beta") p.r0'
    StrCmp $0 "0" check_chain_completion_beta running
  check_chain_completion_beta:
    ; Block removal while the previous release still has its runtime loaded.
    System::Call 'user32::FindWindowW(p 0, w "PoE2 Data Logger 1.3.1.2 Beta") p.r0'
    StrCmp $0 "0" check_review_beta running
  check_review_beta:
    System::Call 'user32::FindWindowW(p 0, w "PoE2 Data Logger 1.3.1.3 Beta") p.r0'
    StrCmp $0 "0" check_previous_stable running
  check_previous_stable:
    ; Keep the stable client's loaded runtime intact during uninstallation.
    System::Call 'user32::FindWindowW(p 0, w "PoE2 Data Logger 1.3.2") p.r0'
    StrCmp $0 "0" check_previous_patch_beta running
  check_previous_patch_beta:
    System::Call 'user32::FindWindowW(p 0, w "PoE2 Data Logger 1.3.2.4 Beta") p.r0'
    StrCmp $0 "0" check_previous_layout_beta running
  check_previous_layout_beta:
    ; Uninstallation must also preserve files loaded by the previous beta.
    System::Call 'user32::FindWindowW(p 0, w "PoE2 Data Logger 1.3.2.5 Beta") p.r0'
    StrCmp $0 "0" check_previous_audit_beta running
  check_previous_audit_beta:
    ; Do not replace or delete runtime files while the preceding beta uses them.
    System::Call 'user32::FindWindowW(p 0, w "PoE2 Data Logger 1.3.2.6 Beta") p.r0'
    StrCmp $0 "0" check_current running
  check_current:
    System::Call 'user32::FindWindowW(p 0, w "${APP_NAME}") p.r0'
    StrCmp $0 "0" ready
  running:
    MessageBox MB_ICONEXCLAMATION|MB_OK "Close PoE2 Data Logger before uninstalling." /SD IDOK
    SetErrorLevel 2
    Abort
  ready:
FunctionEnd

Section "PoE2 Data Logger" Main
  ; Replace bundled runtime files, grant users access to Databases, and register
  ; shortcuts/uninstall metadata. Existing Databases contents are retained.
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
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\PoE2DataLogger" "DisplayName" "${APP_NAME}"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\PoE2DataLogger" "DisplayVersion" "${APP_DISPLAY_VERSION}"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\PoE2DataLogger" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\PoE2DataLogger" "DisplayIcon" "$INSTDIR\PoE2-Data-Logger.exe"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\PoE2DataLogger" "Publisher" "PoE2 Data Logger"
  WriteRegDWORD HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\PoE2DataLogger" "NoModify" 1
  WriteRegDWORD HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\PoE2DataLogger" "NoRepair" 1
SectionEnd

Section "Uninstall"
  ; Remove only installed runtime files and shortcuts. RMDir on $INSTDIR is
  ; nonrecursive, so remaining user databases prevent the directory's removal.
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
