#ifndef AppVersion
  #define AppVersion "0.7.36"
#endif
#ifndef PayloadDir
  #define PayloadDir "..\dist\windows\payload"
#endif

[Setup]
AppId={{D7C76C6A-1829-4BE8-B54E-026615D011EE}
VersionInfoDescription=AI Secretary Setup
VersionInfoProductName=AI Secretary
AppName=AI {cm:Texte97f94f593}
AppVersion={#AppVersion}
AppPublisher=mumg
AppPublisherURL=https://github.com/mumg/ai_secretary
DefaultDirName={autopf}\AI Secretary
DefaultGroupName=AI {cm:Texte97f94f593}
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.14393
OutputDir=..\dist\windows
OutputBaseFilename=AI-Secretary-Setup-{#AppVersion}-windows-x64
Compression=lzma2/normal
SolidCompression=yes
WizardStyle=modern
SetupIconFile=assets\secretary.ico
SetupLogging=yes
SetupMutex=AISecretarySetup
AllowCancelDuringInstall=no
UninstallDisplayName=AI {cm:Texte97f94f593}
UninstallDisplayIcon={app}\secretary.ico
CloseApplications=yes
RestartApplications=no
; Always create a fresh log after the previous installation has been removed.
UninstallLogMode=new
LicenseFile=THIRD_PARTY.md

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "chinese"; MessagesFile: "languages\ChineseSimplified.isl"

[Files]
Source: "{#PayloadDir}\setup\secretary-setup.exe"; Flags: dontcopy
Source: "{#PayloadDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

; Policy-delivered configuration survives upgrades and uninstall/reinstall.
Source: "{param:CONFIGFILE|}"; DestDir: "{app}"; DestName: "secretary-config.json"; Flags: external ignoreversion uninsneveruninstall; Check: HasPreconfiguration

[Icons]
Name: "{commondesktop}\AI {cm:Texte97f94f593}"; Filename: "{app}\desktop\AI Secretary.exe"; IconFilename: "{app}\secretary.ico"
Name: "{group}\AI {cm:Texte97f94f593}"; Filename: "{app}\desktop\AI Secretary.exe"; IconFilename: "{app}\secretary.ico"
Name: "{group}\{cm:Text0f43fab963}"; Filename: "{app}\desktop\AI Secretary.exe"; Parameters: "--settings"; IconFilename: "{app}\secretary.ico"
Name: "{group}\{cm:Text445ed9ac5d}"; Filename: "https://github.com/mumg/ai_secretary/releases"
Name: "{group}\{cm:Text860db1a699}"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\desktop\AI Secretary.exe"; Parameters: "--settings"; Description: "{cm:Text7c627bb9c1}"; Flags: postinstall skipifsilent runasoriginaluser

[InstallDelete]
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\setup\__pycache__"
Type: files; Name: "{app}\setup\manage.py"
Type: files; Name: "{app}\setup\layout.py"
Type: files; Name: "{app}\setup\check_runtime.py"
Type: filesandordirs; Name: "{app}\backend\src"
Type: filesandordirs; Name: "{app}\backend\migrations"
Type: files; Name: "{app}\backend\alembic.ini"
Type: files; Name: "{app}\backend\requirements.txt"
Type: files; Name: "{app}\backend\pyproject.toml"
Type: files; Name: "{app}\parser\main.py"
Type: filesandordirs; Name: "{app}\parser\__pycache__"
Type: files; Name: "{app}\parser\pyproject.toml"
Type: files; Name: "{app}\python-packages.txt"

[UninstallDelete]
Type: filesandordirs; Name: "{app}\services"
Type: files; Name: "{app}\Open.url"
Type: files; Name: "{app}\Settings.url"

#include "languages/custom-messages.iss"

[Code]
const
  UninstallKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{D7C76C6A-1829-4BE8-B54E-026615D011EE}_is1';

var
  ConnectionPage: TInputQueryWizardPage;
  AccessPage: TInputOptionWizardPage;
  HostPage: TInputQueryWizardPage;
  RuntimeNeedsRestart: Boolean;
  HelperError: String;

function DataRoot: String;
begin
  Result := ExpandConstant('{commonappdata}\AI Secretary');
end;

function Q(Value: String): String;
begin
  StringChangeEx(Value, '"', '\"', True);
  Result := '"' + Value + '"';
end;

procedure CaptureHelperOutput(const S: String; const Error, FirstLine: Boolean);
begin
  Log(S);
  if Trim(S) <> '' then
    HelperError := Copy(S, 1, 2000);
end;

function RunHelper(const Helper, Params, WorkingDir: String): Boolean;
var
  Code: Integer;
begin
  HelperError := '';
  Result := False;
  try
    if not ExecAndLogOutput(Helper, Params, WorkingDir, SW_HIDE,
      ewWaitUntilTerminated, Code, @CaptureHelperOutput) then begin
      HelperError := CustomMessage('Text05b0276282') + SysErrorMessage(Code);
      Exit;
    end;
    Result := Code = 0;
    if not Result and (HelperError = '') then
      HelperError := CustomMessage('Texte44966fb86') + IntToStr(Code);
  except
    HelperError := GetExceptionMessage;
  end;
end;

function ExistingInstallation: Boolean;
begin
  Result := FileExists(DataRoot + '\connection.json');
end;

function InsideDirectory(const Path, Directory: String): Boolean;
var
  Prefix: String;
begin
  Prefix := AddBackslash(ExpandFileName(Directory));
  Result := CompareText(Copy(AddBackslash(ExpandFileName(Path)), 1, Length(Prefix)), Prefix) = 0;
end;

function SafeProgramDirectory(const Root: String): Boolean;
begin
  Result := not InsideDirectory(Root, DataRoot) and not InsideDirectory(DataRoot, Root);
end;

function HasUninstallLog(const Root: String): Boolean;
var
  Entry: TFindRec;
begin
  Result := FindFirst(AddBackslash(Root) + 'unins*.dat', Entry);
  if Result then FindClose(Entry);
end;

function PendingProgramRemoval(const Root: String): Boolean;
var
  Operations: String;
begin
  Operations := '';
  RegQueryMultiStringValue(HKLM64, 'SYSTEM\CurrentControlSet\Control\Session Manager',
    'PendingFileRenameOperations', Operations);
  Result := Pos(Lowercase(AddBackslash(ExpandFileName(Root))), Lowercase(Operations)) > 0;
end;

function PreviousUninstaller(var Root, Uninstaller: String): String;
var
  Command: String;
begin
  Result := '';
  Root := ExpandConstant('{app}');
  Uninstaller := '';
  if not RegKeyExists(HKLM64, UninstallKey) then Exit;
  if not RegQueryStringValue(HKLM64, UninstallKey, 'UninstallString', Command) then begin
    Result := CustomMessage('Text0ec141610b');
    Exit;
  end;
  Uninstaller := RemoveQuotes(Trim(Command));
  Root := ExtractFileDir(Uninstaller);
  if (Root = '') or not FileExists(Uninstaller) or
     (CompareText(ExtractFileExt(Uninstaller), '.exe') <> 0) or
     (CompareText(Copy(ExtractFileName(Uninstaller), 1, 5), 'unins') <> 0) then begin
    Result := CustomMessage('Textd1404d0083');
    Exit;
  end;
  if not SafeProgramDirectory(Root) then begin
    Result := CustomMessage('Textfde8e15c86');
    Exit;
  end;
  if InsideDirectory(ExpandConstant('{srcexe}'), Root) then
    Result := CustomMessage('Textf4f21c999c');
end;

function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo,
  MemoTypeInfo, MemoComponentsInfo, MemoGroupInfo, MemoTasksInfo: String): String;
begin
  Result := MemoDirInfo;
  if RegKeyExists(HKLM64, UninstallKey) then
    Result := Result + NewLine + NewLine +
      CustomMessage('Text048dee7fe9') + NewLine +
      CustomMessage('Text261a9933d5') + DataRoot + '.';
end;

procedure InitializeWizard;
begin
  ConnectionPage := CreateInputQueryPage(wpSelectDir, CustomMessage('Text0dc21f2a08'),
    CustomMessage('Text5a7290e5be'),
    CustomMessage('Text73d110ce0e'));
  ConnectionPage.Add(CustomMessage('Text22b0cf5280'), False);
  ConnectionPage.Add(CustomMessage('Text22859268e3'), False);
  ConnectionPage.Add(CustomMessage('Text4183b077b6'), False);
  ConnectionPage.Values[0] := ExpandConstant('{param:APIPORT|18000}');
  ConnectionPage.Values[1] := ExpandConstant('{param:PARSERPORT|18080}');
  ConnectionPage.Values[2] := ExpandConstant('{param:DBPORT|15432}');
  AccessPage := CreateInputOptionPage(ConnectionPage.ID, CustomMessage('Text966ba0147f'),
    CustomMessage('Text82b4e76f13'),
    CustomMessage('Text460548bcc1'), True, False);
  AccessPage.Add(CustomMessage('Text0f03030bff'));
  AccessPage.Add(CustomMessage('Text6b8d686204'));
  AccessPage.SelectedValueIndex := 0;
  HostPage := CreateInputQueryPage(AccessPage.ID, CustomMessage('Texta9ede6ca03'),
    CustomMessage('Text54d4f15f33'),
    CustomMessage('Text0e24d63576'));
  HostPage.Add(CustomMessage('Text4f0df1f9ba'), False);
  HostPage.Values[0] := ExpandConstant('{param:PUBLICHOST|}');
  if HostPage.Values[0] <> '' then AccessPage.SelectedValueIndex := 1;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := (ExistingInstallation and ((PageID = ConnectionPage.ID) or (PageID = AccessPage.ID) or (PageID = HostPage.ID)))
    or ((PageID = HostPage.ID) and (AccessPage.SelectedValueIndex = 0));
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  I, P: Integer;
begin
  Result := True;
  if CurPageID = wpSelectDir then begin
    Result := SafeProgramDirectory(ExpandConstant('{app}'));
    if not Result then MsgBox(CustomMessage('Texte21d44021c'), mbError, MB_OK);
  end;
  if CurPageID = ConnectionPage.ID then begin
    for I := 0 to 2 do begin
      P := StrToIntDef(ConnectionPage.Values[I], 0);
      if (P < 1024) or (P > 65535) then Result := False;
    end;
    if (ConnectionPage.Values[0] = ConnectionPage.Values[1]) or
       (ConnectionPage.Values[0] = ConnectionPage.Values[2]) or
       (ConnectionPage.Values[1] = ConnectionPage.Values[2]) then Result := False;
    if not Result then MsgBox(CustomMessage('Text1099bb4343'), mbError, MB_OK);
  end;
  if CurPageID = HostPage.ID then begin
    Result := (Trim(HostPage.Values[0]) <> '') and (Pos('"', HostPage.Values[0]) = 0) and (Pos('\', HostPage.Values[0]) = 0);
    if not Result then MsgBox(CustomMessage('Text4ce0ec385c'), mbError, MB_OK);
  end;
end;

function HasPreconfiguration: Boolean;
begin
  Result := ExpandConstant('{param:CONFIGFILE|}') <> '';
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Root, Helper, Params, Uninstaller: String;
  Code, Attempts: Integer;
  SavedData: Boolean;
begin
  Result := '';
  if HasPreconfiguration and not FileExists(ExpandConstant('{param:CONFIGFILE|}')) then begin
    Result := 'The preconfiguration JSON file does not exist.';
    Exit;
  end;
  if not SafeProgramDirectory(ExpandConstant('{app}')) then begin
    Result := CustomMessage('Texte21d44021c');
    Exit;
  end;
  Result := PreviousUninstaller(Root, Uninstaller);
  if Result <> '' then Exit;
  if ((Uninstaller = '') or (CompareText(Root, ExpandConstant('{app}')) <> 0)) and
     HasUninstallLog(ExpandConstant('{app}')) then begin
    Result := CustomMessage('Text269ec19cc1');
    Exit;
  end;
  if PendingProgramRemoval(Root) or PendingProgramRemoval(ExpandConstant('{app}')) then begin
    NeedsRestart := True;
    Result := CustomMessage('Text42b1f3d51c');
    Exit;
  end;
  SavedData := ExistingInstallation;
  if SavedData then begin
    { Back up the OLD root using the NEW helper before running its uninstaller. }
    ExtractTemporaryFile('secretary-setup.exe');
    Helper := ExpandConstant('{tmp}\secretary-setup.exe');
    Params := 'prepare --language ' + ActiveLanguage + ' --root ' + Q(Root) + ' --data ' + Q(DataRoot) + ' --target-version {#AppVersion}';
    if not RunHelper(Helper, Params, ExpandConstant('{tmp}')) then begin
      Result := CustomMessage('Textde38c51af2') + #13#10
        + HelperError + #13#10 + CustomMessage('Textf6a716fdc0') + DataRoot + '\logs\installer.log';
      Exit;
    end;
  end;
  if Uninstaller <> '' then begin
    Log('Removing previous installation before installing new files; preserving ' + DataRoot);
    Params := '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG=' + Q(ExpandConstant('{tmp}\ai-secretary-uninstall.log'));
    if not Exec(Uninstaller, Params, ExpandConstant('{tmp}'), SW_HIDE, ewWaitUntilTerminated, Code) then begin
      Result := CustomMessage('Text92e383e53e') + SysErrorMessage(Code);
      Exit;
    end;
    if Code <> 0 then begin
      Result := CustomMessage('Text9c3147222d') + IntToStr(Code) +
        CustomMessage('Text94b99989f8');
      Exit;
    end;
    { The uninstaller clone terminates its original process before deleting it. }
    for Attempts := 1 to 300 do begin
      if not FileExists(Uninstaller) and not RegKeyExists(HKLM64, UninstallKey) then Break;
      Sleep(100);
    end;
    if PendingProgramRemoval(Root) then begin
      NeedsRestart := True;
      Result := CustomMessage('Text38f517609b');
      Exit;
    end;
    if FileExists(Uninstaller) or RegKeyExists(HKLM64, UninstallKey) then begin
      Result := CustomMessage('Texte9a7285244');
      Exit;
    end;
    if SavedData and not ExistingInstallation then begin
      Result := CustomMessage('Text0f57f77790');
      Exit;
    end;
    Log('Previous installation removed; persistent data preserved.');
  end;
  { Never append to or replace an orphaned uninstall log. }
  if HasUninstallLog(ExpandConstant('{app}')) then begin
    Result := CustomMessage('Text269ec19cc1');
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Root, Params, Host: String;
  Code: Integer;
begin
  if CurStep = ssPostInstall then begin
    Root := ExpandConstant('{app}');
    if not Exec(Root + '\vendor\vc_redist.x64.exe', '/install /quiet /norestart', Root, SW_HIDE, ewWaitUntilTerminated, Code) then
      RaiseException(CustomMessage('Text19ed971018'));
    if (Code <> 0) and (Code <> 3010) and (Code <> 1638) then
      RaiseException(CustomMessage('Text9f461460c6') + IntToStr(Code));
    RuntimeNeedsRestart := Code = 3010;
    Host := '';
    if AccessPage.SelectedValueIndex = 1 then Host := HostPage.Values[0];
    Params := 'configure --language ' + ActiveLanguage + ' --root ' + Q(Root) + ' --data ' + Q(DataRoot)
      + ' --api-port ' + Q(ConnectionPage.Values[0]) + ' --parser-port ' + Q(ConnectionPage.Values[1])
      + ' --database-port ' + Q(ConnectionPage.Values[2]) + ' --public-host ' + Q(Host);
    if not RunHelper(Root + '\setup\secretary-setup.exe', Params, Root) then
      RaiseException(CustomMessage('Text1f3cd0171c') + #13#10
        + HelperError + #13#10 + CustomMessage('Textf6a716fdc0') + DataRoot + '\logs\installer.log'
        + #13#10 + CustomMessage('Texta61b1449f7'));
  end;
end;

function NeedRestart: Boolean;
begin
  Result := RuntimeNeedsRestart;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Root, Params: String;
begin
  if CurUninstallStep = usUninstall then begin
    Root := ExpandConstant('{app}');
    Params := 'remove --language ' + ActiveLanguage + ' --root ' + Q(Root) + ' --data ' + Q(DataRoot);
    if not RunHelper(Root + '\setup\secretary-setup.exe', Params, Root) then
      RaiseException(CustomMessage('Textec9c424d09') + #13#10
        + HelperError + #13#10 + CustomMessage('Textf6a716fdc0') + DataRoot + '\logs\installer.log');
  end;
end;
