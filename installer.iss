; Script generated for Inno Setup 6
; Desktop LED Sync Windows Installer

#ifndef AppVersion
#define AppVersion "1.5.0"
#endif

#define AppName "Desktop LED Sync"
#define AppPublisher "Desktop LED Sync"
#define AppExeName "DesktopLEDSync.exe"
#define AppId "{{E8B54D40-6215-4D56-A893-9C149D2FA41E}"

[Setup]
AppId={#AppId}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
UninstallDisplayIcon={app}\{#AppExeName}
SetupIconFile=assets\app_icon.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
VersionInfoVersion={#AppVersion}.0
VersionInfoCompany={#AppPublisher}
VersionInfoDescription={#AppName} Setup
VersionInfoProductName={#AppName}
VersionInfoProductVersion={#AppVersion}

; Allow installation without administrator privileges (installs to %LocalAppData%\Programs)
; or allow user to elevate and install for all users (Program Files)
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

; Gracefully ask to close application if it is currently running
CloseApplications=yes
CloseApplicationsFilter={#AppExeName}

; Output configuration
OutputDir=dist
OutputBaseFilename=DesktopLEDSync-Setup

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "startupicon"; Description: "Start {#AppName} minimized when Windows starts"; GroupDescription: "Startup:"; Flags: unchecked

[Files]
Source: "dist\{#AppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "assets\app_icon.ico"; DestDir: "{app}\assets"; Flags: ignoreversion

[Icons]
; Start Menu
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExeName}"
; Desktop shortcut (optional)
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon
; Windows Startup folder shortcut (optional)
Name: "{userstartup}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Parameters: "--minimized"; Tasks: startupicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[Code]
// Cleanup and optional settings removal during uninstall
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  AppDataDir: String;
  StartupLnk: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    // Remove autostart shortcut in case it was toggled on from inside the app settings
    StartupLnk := ExpandConstant('{userstartup}\Desktop LED Sync.lnk');
    if FileExists(StartupLnk) then
    begin
      DeleteFile(StartupLnk);
    end;

    // Ask whether to delete user settings in %APPDATA%\DesktopLEDSync
    AppDataDir := ExpandConstant('{userappdata}\DesktopLEDSync');
    if DirExists(AppDataDir) then
    begin
      if MsgBox('Do you want to delete your saved settings and configuration?' + #13#10 + #13#10 +
                AppDataDir, mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
      begin
        DelTree(AppDataDir, True, True, True);
      end;
    end;
  end;
end;
