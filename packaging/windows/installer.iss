#ifndef AppVersion
  #define AppVersion "0.5.5"
#endif
#ifndef BundleDir
  #error BundleDir is required
#endif
#ifndef OutputRoot
  #error OutputRoot is required
#endif
#ifdef TestBuild
  #define ProductId "{A80A57C9-4244-4FD3-A166-CA9468D60A4F}"
  #define ProductName "MiniCodex Verification"
  #define SetupName "MiniCodex-Verification-Setup"
#else
  #define ProductId "{5D603A3D-4FDB-43B9-844D-3226D055E648}"
  #define ProductName "MiniCodex"
  #define SetupName "MiniCodex-" + AppVersion + "-Setup"
#endif

[Setup]
AppId={{#ProductId}
AppName={#ProductName}
AppVersion={#AppVersion}
AppPublisher=MiniCodex Project
DefaultDirName={localappdata}\Programs\{#ProductName}
DefaultGroupName={#ProductName}
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.22000
OutputDir={#OutputRoot}
OutputBaseFilename={#SetupName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\MiniCodex.exe
AppMutex=Local\MiniCodexDesktop-v1
CloseApplications=no
RestartApplications=no
SetupLogging=yes
DisableProgramGroupPage=yes
UsePreviousAppDir=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Files]
Source: "{#BundleDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userprograms}\{#ProductName}\MiniCodex"; Filename: "{app}\MiniCodex.exe"; WorkingDir: "{app}"
Name: "{userprograms}\{#ProductName}\Uninstall"; Filename: "{uninstallexe}"
Name: "{userdesktop}\{#ProductName}"; Filename: "{app}\MiniCodex.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\MiniCodex.exe"; Description: "Launch MiniCodex"; Flags: nowait postinstall skipifsilent

[Code]
function InitializeUninstall(): Boolean;
var
  Choice, ExitCode: Integer;
begin
  Result := True;
  { Silent uninstall always retains user data. No command-line switch silently purges it. }
  if not UninstallSilent then begin
    Choice := MsgBox('Also remove MiniCodex default user data and saved API credentials?' + #13#10 +
      'Yes: move local sessions/settings to the Recycle Bin and delete saved keys.' + #13#10 +
      'No (default): retain all user data. Custom --data-dir folders are always retained.',
      mbConfirmation, MB_YESNOCANCEL or MB_DEFBUTTON2);
    if Choice = IDCANCEL then begin
      Result := False;
      exit;
    end;
    if Choice = IDYES then begin
      if (not Exec(ExpandConstant('{app}\MiniCodex.exe'),
        '--purge-user-data --confirm-purge', '', SW_HIDE, ewWaitUntilTerminated, ExitCode))
        or (ExitCode <> 0) then begin
        MsgBox('User data cleanup could not be confirmed. Close MiniCodex and check the data folder. ' +
          'Uninstall has been cancelled; program files were retained.', mbError, MB_OK);
        Result := False;
      end;
    end;
  end;
end;
