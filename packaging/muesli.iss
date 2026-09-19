; Inno Setup script -> Muesli-Setup-x.y.z.exe
;
; Per-user install (no admin prompt): a dictation app has no business asking for
; elevation, and a non-elevated install keeps it out of the UIPI-protected
; process class, which is what we want for SendInput anyway.

#define AppName      "Muesli"
#define AppVersion   "1.0.0"
#define AppPublisher "Muesli for Windows"
#define AppExe       "Muesli.exe"

[Setup]
AppId={{B3F2A6E4-7C31-4B4E-9A2E-6D5F0C2A9E11}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\dist
OutputBaseFilename=Muesli-Setup-{#AppVersion}
SetupIconFile=muesli.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "startup"; Description: "Start {#AppName} when I sign in"; GroupDescription: "Startup"
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked
Name: "addtopath"; Description: "Add muesli-cli to PATH (for scripts and agents)"; Flags: unchecked

[Files]
Source: "..\dist\Muesli\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon
Name: "{userstartup}\{#AppName}"; Filename: "{app}\{#AppExe}"; Parameters: "--tray"; Tasks: startup

[Registry]
Root: HKCU; Subkey: "Environment"; ValueType: expandsz; ValueName: "Path"; \
  ValueData: "{olddata};{app}"; Check: NeedsAddPath('{app}'); Tasks: addtopath

[Run]
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName}"; \
  Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Models are large and live outside the app folder; leave the user's data alone
; and only remove what the installer itself created.
Type: filesandordirs; Name: "{app}"

[Code]
function NeedsAddPath(Param: string): boolean;
var
  OrigPath: string;
begin
  if not RegQueryStringValue(HKEY_CURRENT_USER, 'Environment', 'Path', OrigPath) then
  begin
    Result := True;
    exit;
  end;
  Result := Pos(';' + ExpandConstant(Param) + ';', ';' + OrigPath + ';') = 0;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Answer: Integer;
  DataDir, CacheDir: string;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{userappdata}\Muesli');
    CacheDir := ExpandConstant('{localappdata}\Muesli');
    if DirExists(DataDir) or DirExists(CacheDir) then
    begin
      Answer := MsgBox('Also delete your settings, dictation history and downloaded '
        + 'speech models?' + #13#10 + #13#10
        + 'Choose No to keep them for a future reinstall.',
        mbConfirmation, MB_YESNO or MB_DEFBUTTON2);
      if Answer = IDYES then
      begin
        DelTree(DataDir, True, True, True);
        DelTree(CacheDir, True, True, True);
      end;
    end;
  end;
end;
