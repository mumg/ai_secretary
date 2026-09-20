#ifndef AppVersion
  #define AppVersion "0.1.37"
#endif
#ifndef PayloadDir
  #define PayloadDir "..\dist\windows\payload"
#endif

[Setup]
AppId={{D7C76C6A-1829-4BE8-B54E-026615D011EE}
AppName=AI Секретарь
AppVersion={#AppVersion}
AppPublisher=mumg
AppPublisherURL=https://github.com/mumg/ai_secretary
DefaultDirName={autopf}\AI Secretary
DefaultGroupName=AI Секретарь
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
UninstallDisplayName=AI Секретарь
UninstallDisplayIcon={app}\secretary.ico
CloseApplications=yes
RestartApplications=no
; Always create a fresh log after the previous installation has been removed.
UninstallLogMode=new
LicenseFile=THIRD_PARTY.md

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "{#PayloadDir}\setup\secretary-setup.exe"; Flags: dontcopy
Source: "{#PayloadDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{commondesktop}\AI Секретарь"; Filename: "{app}\desktop\AI Secretary.exe"; IconFilename: "{app}\secretary.ico"
Name: "{group}\AI Секретарь"; Filename: "{app}\desktop\AI Secretary.exe"; IconFilename: "{app}\secretary.ico"
Name: "{group}\Настройки AI Секретаря"; Filename: "{app}\desktop\AI Secretary.exe"; Parameters: "--settings"; IconFilename: "{app}\secretary.ico"
Name: "{group}\Новые версии"; Filename: "https://github.com/mumg/ai_secretary/releases"
Name: "{group}\Удалить AI Секретарь"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\desktop\AI Secretary.exe"; Parameters: "--settings"; Description: "Открыть настройку источников и модели"; Flags: postinstall skipifsilent runasoriginaluser

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
      HelperError := 'Не удалось запустить помощник: ' + SysErrorMessage(Code);
      Exit;
    end;
    Result := Code = 0;
    if not Result and (HelperError = '') then
      HelperError := 'Помощник завершился с кодом ' + IntToStr(Code);
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
    Result := 'Не найдена команда удаления прежней версии. Восстановите деинсталлятор и повторите установку.';
    Exit;
  end;
  Uninstaller := RemoveQuotes(Trim(Command));
  Root := ExtractFileDir(Uninstaller);
  if (Root = '') or not FileExists(Uninstaller) or
     (CompareText(ExtractFileExt(Uninstaller), '.exe') <> 0) or
     (CompareText(Copy(ExtractFileName(Uninstaller), 1, 5), 'unins') <> 0) then begin
    Result := 'Деинсталлятор прежней версии отсутствует или повреждён. Установка поверх неё запрещена.';
    Exit;
  end;
  if not SafeProgramDirectory(Root) then begin
    Result := 'Каталоги программы и сохраняемых данных пересекаются. Удаление остановлено.';
    Exit;
  end;
  if InsideDirectory(ExpandConstant('{srcexe}'), Root) then
    Result := 'Переместите новый установщик из каталога программы, например в Загрузки, и запустите снова.';
end;

function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo,
  MemoTypeInfo, MemoComponentsInfo, MemoGroupInfo, MemoTasksInfo: String): String;
begin
  Result := MemoDirInfo;
  if RegKeyExists(HKLM64, UninstallKey) then
    Result := Result + NewLine + NewLine +
      'Прежняя версия будет удалена, затем программа будет установлена заново.' + NewLine +
      'База, вложения, настройки и ключи сохраняются в ' + DataRoot + '.';
end;

procedure InitializeWizard;
begin
  ConnectionPage := CreateInputQueryPage(wpSelectDir, 'Сервер AI Секретаря',
    'Локальная база PostgreSQL и фоновые службы',
    'Выберите свободные порты. Подключение к AI-провайдеру и модель настраиваются в админке после установки.');
  ConnectionPage.Add('Порт интерфейса:', False);
  ConnectionPage.Add('Порт парсера документов:', False);
  ConnectionPage.Add('Порт PostgreSQL:', False);
  ConnectionPage.Values[0] := ExpandConstant('{param:APIPORT|18000}');
  ConnectionPage.Values[1] := ExpandConstant('{param:PARSERPORT|18080}');
  ConnectionPage.Values[2] := ExpandConstant('{param:DBPORT|15432}');
  AccessPage := CreateInputOptionPage(ConnectionPage.ID, 'Доступ к приложению',
    'На этом компьютере или по HTTPS',
    'Публичный доступ требует домена и перенаправления портов 80 и 443 на этот компьютер. Для доступа с телефона будет создан клиентский сертификат.', True, False);
  AccessPage.Add('Только на этом компьютере');
  AccessPage.Add('HTTPS для браузера и мобильного приложения');
  AccessPage.SelectedValueIndex := 0;
  HostPage := CreateInputQueryPage(AccessPage.ID, 'Публичный HTTPS-адрес',
    'Домен вашей установки',
    'Укажите домен без https://. Поддерживается DynDNS. Let''s Encrypt подтвердит домен через HTTP и будет продлевать серверный сертификат автоматически.');
  HostPage.Add('Домен:', False);
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
    if not Result then MsgBox('Каталоги программы и сохраняемых данных не должны пересекаться.', mbError, MB_OK);
  end;
  if CurPageID = ConnectionPage.ID then begin
    for I := 0 to 2 do begin
      P := StrToIntDef(ConnectionPage.Values[I], 0);
      if (P < 1024) or (P > 65535) then Result := False;
    end;
    if (ConnectionPage.Values[0] = ConnectionPage.Values[1]) or
       (ConnectionPage.Values[0] = ConnectionPage.Values[2]) or
       (ConnectionPage.Values[1] = ConnectionPage.Values[2]) then Result := False;
    if not Result then MsgBox('Укажите три разных порта 1024–65535.', mbError, MB_OK);
  end;
  if CurPageID = HostPage.ID then begin
    Result := (Trim(HostPage.Values[0]) <> '') and (Pos('"', HostPage.Values[0]) = 0) and (Pos('\', HostPage.Values[0]) = 0);
    if not Result then MsgBox('Введите публичный DNS-домен.', mbError, MB_OK);
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Root, Helper, Params, Uninstaller: String;
  Code, Attempts: Integer;
  SavedData: Boolean;
begin
  Result := '';
  if not SafeProgramDirectory(ExpandConstant('{app}')) then begin
    Result := 'Каталоги программы и сохраняемых данных не должны пересекаться.';
    Exit;
  end;
  Result := PreviousUninstaller(Root, Uninstaller);
  if Result <> '' then Exit;
  if ((Uninstaller = '') or (CompareText(Root, ExpandConstant('{app}')) <> 0)) and
     HasUninstallLog(ExpandConstant('{app}')) then begin
    Result := 'В каталоге программы остались данные прежнего деинсталлятора. Завершите удаление прежней версии и повторите установку.';
    Exit;
  end;
  if PendingProgramRemoval(Root) or PendingProgramRemoval(ExpandConstant('{app}')) then begin
    NeedsRestart := True;
    Result := 'Удаление прежних файлов ожидает перезагрузки. Перезагрузите Windows и запустите установку снова.';
    Exit;
  end;
  SavedData := ExistingInstallation;
  if SavedData then begin
    { Back up the OLD root using the NEW helper before running its uninstaller. }
    ExtractTemporaryFile('secretary-setup.exe');
    Helper := ExpandConstant('{tmp}\secretary-setup.exe');
    Params := 'prepare --root ' + Q(Root) + ' --data ' + Q(DataRoot) + ' --target-version {#AppVersion}';
    if not RunHelper(Helper, Params, ExpandConstant('{tmp}')) then begin
      Result := 'Резервное копирование не завершено. Установка остановлена до замены файлов.' + #13#10
        + HelperError + #13#10 + 'Журнал: ' + DataRoot + '\logs\installer.log';
      Exit;
    end;
  end;
  if Uninstaller <> '' then begin
    Log('Removing previous installation before installing new files; preserving ' + DataRoot);
    Params := '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG=' + Q(ExpandConstant('{tmp}\ai-secretary-uninstall.log'));
    if not Exec(Uninstaller, Params, ExpandConstant('{tmp}'), SW_HIDE, ewWaitUntilTerminated, Code) then begin
      Result := 'Не удалось запустить удаление прежней версии: ' + SysErrorMessage(Code);
      Exit;
    end;
    if Code <> 0 then begin
      Result := 'Удаление прежней версии завершилось с ошибкой ' + IntToStr(Code) +
        '. Новая версия не установлена. Данные и резервная копия сохранены.';
      Exit;
    end;
    { The uninstaller clone terminates its original process before deleting it. }
    for Attempts := 1 to 300 do begin
      if not FileExists(Uninstaller) and not RegKeyExists(HKLM64, UninstallKey) then Break;
      Sleep(100);
    end;
    if PendingProgramRemoval(Root) then begin
      NeedsRestart := True;
      Result := 'Для завершения удаления нужна перезагрузка. Данные сохранены; после перезагрузки запустите установщик снова.';
      Exit;
    end;
    if FileExists(Uninstaller) or RegKeyExists(HKLM64, UninstallKey) then begin
      Result := 'Удаление прежней версии не завершено. Установка новых файлов остановлена; повторите запуск после завершения удаления.';
      Exit;
    end;
    if SavedData and not ExistingInstallation then begin
      Result := 'После удаления не найдены сохранённые настройки. Восстановите ProgramData из резервной копии перед установкой.';
      Exit;
    end;
    Log('Previous installation removed; persistent data preserved.');
  end;
  { Never append to or replace an orphaned uninstall log. }
  if HasUninstallLog(ExpandConstant('{app}')) then begin
    Result := 'В каталоге программы остались данные прежнего деинсталлятора. Завершите удаление прежней версии и повторите установку.';
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
      RaiseException('Не удалось запустить установку Microsoft Visual C++ Runtime.');
    if (Code <> 0) and (Code <> 3010) and (Code <> 1638) then
      RaiseException('Не удалось установить Microsoft Visual C++ Runtime: ' + IntToStr(Code));
    RuntimeNeedsRestart := Code = 3010;
    Host := '';
    if AccessPage.SelectedValueIndex = 1 then Host := HostPage.Values[0];
    Params := 'configure --root ' + Q(Root) + ' --data ' + Q(DataRoot)
      + ' --api-port ' + Q(ConnectionPage.Values[0]) + ' --parser-port ' + Q(ConnectionPage.Values[1])
      + ' --database-port ' + Q(ConnectionPage.Values[2]) + ' --public-host ' + Q(Host);
    if not RunHelper(Root + '\setup\secretary-setup.exe', Params, Root) then
      RaiseException('Не удалось настроить службы. Данные сохранены.' + #13#10
        + HelperError + #13#10 + 'Журнал: ' + DataRoot + '\logs\installer.log'
        + #13#10 + 'После исправления ошибки запустите установщик повторно.');
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
    Params := 'remove --root ' + Q(Root) + ' --data ' + Q(DataRoot);
    if not RunHelper(Root + '\setup\secretary-setup.exe', Params, Root) then
      RaiseException('Не удалось остановить и удалить службы. Файлы программы сохраняются.' + #13#10
        + HelperError + #13#10 + 'Журнал: ' + DataRoot + '\logs\installer.log');
  end;
end;
