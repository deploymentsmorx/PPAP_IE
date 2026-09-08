; SmorX PPAP desktop client installer.
; Per-user install (no admin rights required) with Start Menu + Desktop shortcuts and an uninstaller.

!include "MUI2.nsh"

!define PRODUCT_NAME "SmorX PPAP"
!define PRODUCT_VERSION "1.0.0"
!define PRODUCT_PUBLISHER "SmorX.ai"
!define PRODUCT_EXE "SmorX-PPAP.exe"
!define UNINST_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\SmorXPPAP"

Name "${PRODUCT_NAME}"
OutFile "${OUT_FILE}"
InstallDir "$LOCALAPPDATA\Programs\SmorX PPAP"
InstallDirRegKey HKCU "Software\SmorXPPAP" "InstallDir"
RequestExecutionLevel user
SetCompressor /SOLID lzma

!define MUI_ABORTWARNING
!define MUI_ICON "${NSISDIR}\Contrib\Graphics\Icons\modern-install.ico"
!define MUI_UNICON "${NSISDIR}\Contrib\Graphics\Icons\modern-uninstall.ico"

!define MUI_FINISHPAGE_RUN "$INSTDIR\${PRODUCT_EXE}"
!define MUI_FINISHPAGE_RUN_TEXT "Launch SmorX PPAP"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "English"

Section "Install" SEC01
  SetOutPath "$INSTDIR"
  File "${SRC_DIR}\${PRODUCT_EXE}"
  File "${SRC_DIR}\api_url.txt"

  CreateDirectory "$SMPROGRAMS\SmorX PPAP"
  CreateShortcut "$SMPROGRAMS\SmorX PPAP\SmorX PPAP.lnk" "$INSTDIR\${PRODUCT_EXE}"
  CreateShortcut "$SMPROGRAMS\SmorX PPAP\Uninstall SmorX PPAP.lnk" "$INSTDIR\uninstall.exe"
  CreateShortcut "$DESKTOP\SmorX PPAP.lnk" "$INSTDIR\${PRODUCT_EXE}"

  WriteRegStr HKCU "Software\SmorXPPAP" "InstallDir" "$INSTDIR"
  WriteUninstaller "$INSTDIR\uninstall.exe"

  WriteRegStr HKCU "${UNINST_KEY}" "DisplayName" "${PRODUCT_NAME}"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayVersion" "${PRODUCT_VERSION}"
  WriteRegStr HKCU "${UNINST_KEY}" "Publisher" "${PRODUCT_PUBLISHER}"
  WriteRegStr HKCU "${UNINST_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINST_KEY}" "UninstallString" "$INSTDIR\uninstall.exe"
  WriteRegStr HKCU "${UNINST_KEY}" "QuietUninstallString" "$INSTDIR\uninstall.exe /S"
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoRepair" 1
SectionEnd

Section "Uninstall"
  Delete "$INSTDIR\${PRODUCT_EXE}"
  Delete "$INSTDIR\api_url.txt"
  Delete "$INSTDIR\uninstall.exe"
  RMDir "$INSTDIR"

  Delete "$SMPROGRAMS\SmorX PPAP\SmorX PPAP.lnk"
  Delete "$SMPROGRAMS\SmorX PPAP\Uninstall SmorX PPAP.lnk"
  RMDir "$SMPROGRAMS\SmorX PPAP"
  Delete "$DESKTOP\SmorX PPAP.lnk"

  DeleteRegKey HKCU "${UNINST_KEY}"
  DeleteRegKey HKCU "Software\SmorXPPAP"
SectionEnd
