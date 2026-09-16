# Run only on an isolated Windows CI runner. Never run on a real installation.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$Root = Split-Path $PSScriptRoot -Parent
$Version = (Get-Content "$Root/version" -Raw).Trim()
$Installer = "$Root\dist\windows\AI-Secretary-Setup-$Version-windows-x64.exe"
$InstallRoot = "$env:ProgramFiles\AI Secretary CI"
$DataRoot = "$env:ProgramData\AI Secretary"
if (Test-Path "$DataRoot\connection.json") { throw 'Smoke test requires a clean disposable machine' }
function Run-Installer {
    $Process = Start-Process -FilePath $Installer -ArgumentList @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', "/DIR=`"$InstallRoot`"") -Wait -PassThru
    if ($Process.ExitCode -notin @(0, 3010)) { throw "Installer exit code $($Process.ExitCode)" }
    $Ready = Invoke-RestMethod 'http://127.0.0.1:18000/health/ready' -TimeoutSec 15
    if ($Ready.status -ne 'ready') { throw 'API not ready' }
    $Parser = Invoke-RestMethod 'http://127.0.0.1:18080/health' -TimeoutSec 15
    foreach ($Name in @('AISecretaryDatabase', 'AISecretaryParser', 'AISecretaryApi', 'AISecretaryWorker')) {
        $Service = Get-CimInstance Win32_Service -Filter "Name='$Name'"
        if ($Service.State -ne 'Running' -or $Service.StartMode -ne 'Auto') { throw "Service not running automatically: $Name" }
        if ($Service.StartName -notmatch 'LocalService') { throw "Service runs with unexpected privileges: $Name" }
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
try {
    Run-Installer
    if ((Query-Database "SELECT rolsuper FROM pg_roles WHERE rolname=current_user;") -ne 'f') { throw 'Application DB role is a superuser' }
    Query-Database "CREATE TABLE windows_install_smoke(id integer PRIMARY KEY); INSERT INTO windows_install_smoke VALUES (17);"
    Set-Content "$DataRoot\data\upgrade-marker.txt" 'preserved'
    $MasterKeyHash = (Get-FileHash "$DataRoot\secrets\master-key").Hash
    Run-Installer
    if ((Query-Database 'SELECT id FROM windows_install_smoke;') -ne '17') { throw 'Upgrade lost database data' }
    if ((Get-Content "$DataRoot\data\upgrade-marker.txt") -ne 'preserved') { throw 'Upgrade lost attachments' }
    if ((Get-FileHash "$DataRoot\secrets\master-key").Hash -ne $MasterKeyHash) { throw 'Upgrade replaced encryption key' }
    $Backups = @(Get-ChildItem "$DataRoot\backups" -Directory)
    if ($Backups.Count -ne 1 -or -not (Test-Path "$($Backups[0].FullName)\database.dump")) { throw 'Database backup missing' }
    $Process = Start-Process "$InstallRoot\unins000.exe" -ArgumentList @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART') -Wait -PassThru
    if ($Process.ExitCode -ne 0) { throw 'Uninstall failed' }
    if (Get-Service 'AISecretary*' -ErrorAction SilentlyContinue) { throw 'Uninstall left services registered' }
    if (-not (Test-Path "$DataRoot\postgres\PG_VERSION") -or -not (Test-Path "$DataRoot\secrets\master-key")) { throw 'Uninstall deleted user data' }
    Write-Host 'Install, upgrade, database preservation and uninstall checks passed'
} finally {
    # Do not upload logs: they can contain private configuration or runtime credentials.
    if (Test-Path "$DataRoot\logs\installer.log") {
        Get-Content "$DataRoot\logs\installer.log" | Select-String 'Error|Ошибка|RuntimeError|завершился|не запустился' | ForEach-Object { Write-Host $_.Line }
    }
}
