; BUCS per-user Windows installer (Inno Setup 6).
; iscc /DStageDir=<stage> /DAppVersion=7.9.0 /DIconFile=<bucs.ico> /DOutputDir=<dist> bucs.iss
;
; Installs into %LOCALAPPDATA%\Programs\BUCS (no admin rights; user-writable so signed in-app
; updates can replace app files). Layout: python\ (bundled CPython + deps), launcher\, app files.
; Uninstall removes the program but keeps the user's data (licence, settings, memory) unless
; the user ticks "also delete my data" (interactive uninstall only).

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6F1C2B7E-3B5A-4E52-9C1D-B0C5B1E5A7D3}
AppName=BUCS
AppVerName=BUCS {#AppVersion}
AppVersion={#AppVersion}
AppPublisher=Bobbiey
AppPublisherURL=https://saikiranthatikonda-coder.github.io/Avengers-Bobbiey-UCS/
VersionInfoVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\BUCS
DefaultGroupName=BUCS
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#OutputDir}
OutputBaseFilename=BUCS-Windows-x64-Setup
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\bucs.ico
UninstallDisplayName=BUCS
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=no
SetupLogging=yes
#ifdef SignIt
SignTool=bucssign
SignedUninstaller=yes
#endif

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "{#StageDir}\python\*"; DestDir: "{app}\python"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#StageDir}\launcher\*"; DestDir: "{app}\launcher"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#StageDir}\app\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#StageDir}\build.json"; DestDir: "{app}\launcher"; Flags: ignoreversion
Source: "{#IconFile}"; DestDir: "{app}"; DestName: "bucs.ico"; Flags: ignoreversion

[Icons]
Name: "{userprograms}\BUCS\BUCS"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\launcher\bucs_launcher.py"""; WorkingDir: "{app}"; IconFilename: "{app}\bucs.ico"; Comment: "Open BUCS"
Name: "{userprograms}\BUCS\Stop BUCS"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\launcher\bucs_launcher.py"" --stop"; WorkingDir: "{app}"; IconFilename: "{app}\bucs.ico"; Comment: "Shut BUCS down"
Name: "{userprograms}\BUCS\BUCS logs"; Filename: "{app}\logs"; Comment: "BUCS log files"
Name: "{userdesktop}\BUCS"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\launcher\bucs_launcher.py"""; WorkingDir: "{app}"; IconFilename: "{app}\bucs.ico"; Tasks: desktopicon; Comment: "Open BUCS"

[Run]
Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\launcher\bucs_launcher.py"""; WorkingDir: "{app}"; Description: "Start BUCS now"; Flags: postinstall nowait skipifsilent

[UninstallRun]
Filename: "{app}\python\python.exe"; Parameters: """{app}\launcher\bucs_launcher.py"" --stop"; WorkingDir: "{app}"; Flags: runhidden waituntilterminated; RunOnceId: "StopBUCS"

[UninstallDelete]
; program files the app or an in-app update created after install (user data is kept)
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\launcher"
Type: filesandordirs; Name: "{app}\static"
Type: filesandordirs; Name: "{app}\web3mod"
Type: filesandordirs; Name: "{app}\__pycache__"
Type: filesandordirs; Name: "{app}\.pycache"
Type: filesandordirs; Name: "{app}\.update"
Type: files; Name: "{app}\*.py"
Type: files; Name: "{app}\*.pyc"
Type: files; Name: "{app}\*.cmd"
Type: files; Name: "{app}\*.ps1"
Type: files; Name: "{app}\*.sh"
Type: files; Name: "{app}\*.command"
Type: files; Name: "{app}\VERSION"
Type: files; Name: "{app}\requirements.txt"

[Code]
function StopRunningBUCS(): Boolean;
var
  Py, Launcher: String;
  Code: Integer;
begin
  Result := True;
  Py := ExpandConstant('{app}\python\python.exe');
  Launcher := ExpandConstant('{app}\launcher\bucs_launcher.py');
  if FileExists(Py) and FileExists(Launcher) then
    Exec(Py, '"' + Launcher + '" --stop', ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, Code);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  { an upgrade over a running BUCS: shut it down cleanly first so its files can be replaced }
  StopRunningBUCS();
  Result := '';
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
  begin
    if (not UninstallSilent) and DirExists(ExpandConstant('{app}')) then
      if MsgBox('Also delete your BUCS data (licence, settings, memory and logs) from' + #13#10 +
                ExpandConstant('{app}') + '?' + #13#10#13#10 +
                'Choose No to keep it for a later reinstall.', mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(ExpandConstant('{app}'), True, True, True);
  end;
end;
