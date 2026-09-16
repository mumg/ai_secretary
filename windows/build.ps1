$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root
python windows/build.py
if ($LASTEXITCODE -ne 0) { throw 'Windows payload build failed' }
$Version = (Get-Content "$Root/version" -Raw).Trim()
if ($Version -notmatch '^\d+\.\d+\.\d+$') { throw 'Invalid version file' }
$Compiler = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
if (-not (Test-Path $Compiler)) { throw 'Install Inno Setup 6 before building the EXE' }
& $Compiler "/DAppVersion=$Version" "/DPayloadDir=$Root\dist\windows\payload" "$Root\windows\installer.iss"
if ($LASTEXITCODE -ne 0) { throw 'Installer compilation failed' }
$Installer = "$Root\dist\windows\AI-Secretary-Setup-$Version-windows-x64.exe"
$Hash = (Get-FileHash -Algorithm SHA256 $Installer).Hash.ToLowerInvariant()
[IO.File]::WriteAllText("$Root\dist\windows\SHA256SUMS", "$Hash  $([IO.Path]::GetFileName($Installer))`n")
Write-Host "Installer ready: $Installer"
