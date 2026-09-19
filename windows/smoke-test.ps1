# Run only on an isolated Windows CI runner. Never run on a real installation.
#requires -Version 5.0
#requires -RunAsAdministrator
param(
    [string]$InstallerPath = '',
    [int]$ExpectedWindowsBuild = 0
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$Root = Split-Path $PSScriptRoot -Parent
. (Join-Path $PSScriptRoot 'shortcut-utils.ps1')
$WindowsBuild = [int](Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion').CurrentBuildNumber
if (-not [Environment]::Is64BitOperatingSystem -or $WindowsBuild -lt 14393) { throw 'Requires Windows 10 1607 x64 or newer' }
if ($ExpectedWindowsBuild -and $WindowsBuild -ne $ExpectedWindowsBuild) { throw "Expected Windows build $ExpectedWindowsBuild, got $WindowsBuild" }
Write-Host "Testing Windows build $WindowsBuild"
if ($InstallerPath) {
    $Installer = (Resolve-Path $InstallerPath).Path
} else {
    $Version = (Get-Content "$Root/version" -Raw).Trim()
    $Installer = "$Root\dist\windows\AI-Secretary-Setup-$Version-windows-x64.exe"
}
$InstallRoot = "$env:ProgramFiles\AI Secretary CI"
$DataRoot = "$env:ProgramData\AI Secretary"
if (Test-Path "$DataRoot\connection.json") { throw 'Smoke test requires a clean disposable machine' }
if (Get-Service 'AISecretary*' -ErrorAction SilentlyContinue) { throw 'Smoke test requires no existing AI Secretary services' }
function Run-Installer([switch]$ExpectRemoval) {
    $SetupLog = Join-Path $env:TEMP ("ai-secretary-smoke-" + [guid]::NewGuid().ToString('N') + '.log')
    $Process = Start-Process -FilePath $Installer -ArgumentList @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', "/DIR=`"$InstallRoot`"", "/LOG=`"$SetupLog`"") -Wait -PassThru
    if ($Process.ExitCode -notin @(0, 3010)) { throw "Installer exit code $($Process.ExitCode)" }
    if ($ExpectRemoval -and -not (Select-String -Path $SetupLog -SimpleMatch 'Previous installation removed; persistent data preserved.' -Quiet)) {
        throw 'Installer did not finish removing the previous version before reinstalling'
    }
    $Ready = Invoke-RestMethod 'http://127.0.0.1:18000/health/ready' -TimeoutSec 15
    if ($Ready.status -ne 'ready') { throw 'API not ready' }
    $Parser = Invoke-RestMethod 'http://127.0.0.1:18080/health' -TimeoutSec 15
    & "$InstallRoot\setup\secretary-setup.exe" check-runtime --root $InstallRoot
    if ($LASTEXITCODE -ne 0) { throw 'Bundled native runtime check failed' }
    if (Test-Path "$InstallRoot\python") { throw 'Installer still contains a Python runtime' }
    if (Get-ChildItem $InstallRoot -Recurse -Filter '*.py') { throw 'Installer still contains Python source' }
    if ((& "$InstallRoot\setup\secretary-setup.exe" version) -ne (Get-Content "$InstallRoot\version" -Raw).Trim()) { throw 'Setup helper version mismatch' }
    foreach ($Name in @('AISecretaryDatabase', 'AISecretaryParser', 'AISecretaryApi', 'AISecretaryWorker')) {
        $Service = Get-CimInstance Win32_Service -Filter "Name='$Name'"
        if ($Service.State -ne 'Running' -or $Service.StartMode -ne 'Auto') { throw "Service not running automatically: $Name" }
        if ($Service.StartName -notmatch 'LocalService') { throw "Service runs with unexpected privileges: $Name" }
    }
    $Desktop = "$InstallRoot\desktop\AI Secretary.exe"
    if (-not (Test-Path $Desktop)) { throw 'Electron desktop application missing' }
    foreach ($Folder in @('CommonDesktopDirectory', 'CommonPrograms')) {
        $FolderPath = [Environment]::GetFolderPath($Folder)
        $AllLinks = @(Get-ChildItem -LiteralPath $FolderPath -Filter '*.lnk' -Recurse |
            ForEach-Object { Get-ShortcutInfo -LiteralPath $_.FullName })
        $Links = @($AllLinks | Where-Object { $_.TargetPath -eq $Desktop })
        $Details = $AllLinks | Select-Object FullName, TargetPath, Arguments | ConvertTo-Json -Compress
        if (@($Links | Where-Object { $_.Arguments -eq '' }).Count -ne 1) {
            throw "Application shortcut must target '$Desktop' in '$FolderPath'. Found: $Details"
        }
        if ($Folder -eq 'CommonPrograms' -and @($Links | Where-Object { $_.Arguments -eq '--settings' }).Count -ne 1) {
            throw "Settings shortcut must target '$Desktop' with --settings. Found: $Details"
        }
    }
}
function Query-Database([string]$Sql) {
    $env:PGPASSWORD = (Get-Content "$DataRoot\secrets\database-password" -Raw).Trim()
    try {
        $Value = & "$InstallRoot\postgres\bin\psql.exe" -X -h 127.0.0.1 -p 15432 -U improver -d improver -At -v ON_ERROR_STOP=1 -c $Sql
        if ($LASTEXITCODE -ne 0) { throw 'Database query failed' }
        return $Value
    } finally { Remove-Item Env:PGPASSWORD }
}
function Run-Uninstaller {
    $Process = Start-Process "$InstallRoot\unins000.exe" -ArgumentList @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART') -Wait -PassThru
    if ($Process.ExitCode -ne 0) { throw 'Uninstall failed' }
    $Registration = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{D7C76C6A-1829-4BE8-B54E-026615D011EE}_is1'
    $Deadline = [DateTime]::UtcNow.AddSeconds(30)
    while ((Test-Path "$InstallRoot\unins000.exe") -or (Test-Path $Registration)) {
        if ([DateTime]::UtcNow -ge $Deadline) { throw 'Uninstaller cleanup did not finish' }
        Start-Sleep -Milliseconds 100
    }
    if (Get-Service 'AISecretary*' -ErrorAction SilentlyContinue) { throw 'Uninstall left services registered' }
    if (-not (Test-Path "$DataRoot\postgres\PG_VERSION") -or -not (Test-Path "$DataRoot\secrets\master-key")) { throw 'Uninstall deleted user data' }
}
function Assert-SavedData {
    if ((Query-Database 'SELECT id FROM windows_install_smoke;') -ne '17') { throw 'Reinstall lost database data' }
    if ((Get-Content "$DataRoot\data\upgrade-marker.txt") -ne 'preserved') { throw 'Reinstall lost attachments' }
    if ((Get-FileHash "$DataRoot\secrets\master-key").Hash -ne $MasterKeyHash) { throw 'Reinstall replaced encryption key' }
    if ((Get-FileHash "$DataRoot\connection.json").Hash -ne $ConnectionHash) { throw 'Reinstall replaced connection settings' }
    if ((Get-Content "$DataRoot\certificates\reinstall-marker.txt") -ne 'preserved') { throw 'Reinstall lost certificates directory' }
}
try {
    Run-Installer
    if ((Query-Database "SELECT rolsuper FROM pg_roles WHERE rolname=current_user;") -ne 'f') { throw 'Application DB role is a superuser' }
    Query-Database "CREATE TABLE windows_install_smoke(id integer PRIMARY KEY); INSERT INTO windows_install_smoke VALUES (17);"
    Set-Content "$DataRoot\data\upgrade-marker.txt" 'preserved'
    $MasterKeyHash = (Get-FileHash "$DataRoot\secrets\master-key").Hash
    $ConnectionHash = (Get-FileHash "$DataRoot\connection.json").Hash
    Set-Content "$DataRoot\certificates\reinstall-marker.txt" 'preserved'
    # A damaged uninstall registration must abort without stopping the old app.
    $UninstallKey = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{D7C76C6A-1829-4BE8-B54E-026615D011EE}_is1'
    $UninstallCommand = (Get-ItemProperty $UninstallKey).UninstallString
    try {
        Set-ItemProperty $UninstallKey -Name UninstallString -Value "`"$InstallRoot\unins-missing.exe`""
        $Failed = Start-Process -FilePath $Installer -ArgumentList @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', "/DIR=`"$InstallRoot`"") -Wait -PassThru
        if ($Failed.ExitCode -in @(0, 3010)) { throw 'Installer continued without a valid previous uninstaller' }
        if ((Invoke-RestMethod 'http://127.0.0.1:18000/health/ready' -TimeoutSec 15).status -ne 'ready') { throw 'Rejected reinstall stopped the old server' }
        Assert-SavedData
    } finally {
        Set-ItemProperty $UninstallKey -Name UninstallString -Value $UninstallCommand
    }
    # Legacy untracked files must be backed up before cleanup; keep the old
    # helper intact because the registered uninstaller must remain runnable.
    New-Item "$InstallRoot\python" -ItemType Directory | Out-Null
    Set-Content "$InstallRoot\python\retired-runtime.txt" 'legacy payload'
    Set-Content "$InstallRoot\setup\manage.py" 'raise RuntimeError("Legacy helper must not run")'
    Run-Installer -ExpectRemoval
    Assert-SavedData
    $Backups = @(Get-ChildItem "$DataRoot\backups" -Directory)
    if ($Backups.Count -ne 1 -or -not (Test-Path "$($Backups[0].FullName)\database.dump")) { throw 'Database backup missing' }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $Archive = [IO.Compression.ZipFile]::OpenRead("$($Backups[0].FullName)\program.zip")
    try {
        if (-not $Archive.GetEntry('python/retired-runtime.txt')) { throw 'Old runtime was deleted before backup' }
    } finally { $Archive.Dispose() }
    Run-Uninstaller
    Run-Installer
    Assert-SavedData
    # Configure completes the upgrade state; find the preserved cold backup itself.
    $ColdBackups = @(Get-ChildItem "$DataRoot\backups" -Directory | Where-Object { Test-Path "$($_.FullName)\postgres\PG_VERSION" })
    if ($ColdBackups.Count -ne 1) { throw 'Cold database backup missing after reinstall' }
    Run-Uninstaller
    Write-Host 'Install, failed-removal preflight, automatic uninstall/reinstall, data preservation and uninstall checks passed'
} finally {
    # Do not upload logs: they can contain private configuration or runtime credentials.
    if (Test-Path "$DataRoot\logs\installer.log") {
        Get-Content "$DataRoot\logs\installer.log" | Select-String 'Error|Ошибка|RuntimeError|завершился|не запустился' | ForEach-Object { Write-Host $_.Line }
    }
}
