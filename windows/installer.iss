#ifndef AppVersion
  #define AppVersion "0.1.21"
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
LicenseFile=THIRD_PARTY.md

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "{#PayloadDir}\setup\secretary-setup.exe"; Flags: dontcopy
Source: "{#PayloadDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\AI Секретарь"; Filename: "{app}\Open.url"; IconFilename: "{app}\secretary.ico"
Name: "{group}\Настройки AI Секретаря"; Filename: "{app}\Settings.url"; IconFilename: "{app}\secretary.ico"
Name: "{group}\Новые версии"; Filename: "https://github.com/mumg/ai_secretary/releases"
Name: "{group}\Удалить AI Секретарь"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\Settings.url"; Description: "Открыть настройку источников и модели"; Flags: shellexec postinstall skipifsilent runasoriginaluser

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
var
  ConnectionPage: TInputQueryWizardPage;
  AccessPage: TInputOptionWizardPage;
  HostPage: TInputQueryWizardPage;
  RuntimeNeedsRestart: Boolean;

function DataRoot: String;
begin
  Result := ExpandConstant('{commonappdata}\AI Secretary');
end;

function Q(Value: String): String;
begin
  StringChangeEx(Value, '"', '\"', True);
  Result := '"' + Value + '"';
end;

function ExistingInstallation: Boolean;
begin
  Result := FileExists(DataRoot + '\connection.json');
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
  Code: Integer;
  Root, Helper, Params: String;
begin
  Result := '';
  Root := ExpandConstant('{app}');
  if ExistingInstallation then begin
    { Use the new native helper before overwriting any old installation files. }
    ExtractTemporaryFile('secretary-setup.exe');
    Helper := ExpandConstant('{tmp}\secretary-setup.exe');
    Params := 'prepare --root ' + Q(Root) + ' --data ' + Q(DataRoot) + ' --target-version {#AppVersion}';
    if not Exec(Helper, Params, ExpandConstant('{tmp}'), SW_HIDE, ewWaitUntilTerminated, Code) or (Code <> 0) then
      Result := 'Резервное копирование не завершено. Установка остановлена до замены файлов. Проверьте ' + DataRoot + '\logs\installer.log';
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
    if not Exec(Root + '\setup\secretary-setup.exe', Params, Root, SW_HIDE, ewWaitUntilTerminated, Code) or (Code <> 0) then
      RaiseException('Не удалось настроить службы. Данные сохранены. Проверьте ' + DataRoot + '\logs\installer.log' + #13#10 + 'После исправления ошибки запустите установщик повторно.');
  end;
end;

function NeedRestart: Boolean;
begin
  Result := RuntimeNeedsRestart;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Root, Params: String;
  Code: Integer;
begin
  if CurUninstallStep = usUninstall then begin
    Root := ExpandConstant('{app}');
    Params := 'remove --root ' + Q(Root) + ' --data ' + Q(DataRoot);
    if not Exec(Root + '\setup\secretary-setup.exe', Params, Root, SW_HIDE, ewWaitUntilTerminated, Code) or (Code <> 0) then
      RaiseException('Не удалось остановить и удалить службы. Файлы программы сохраняются. Проверьте журнал installer.log.');
  end;
end;
