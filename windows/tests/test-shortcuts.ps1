# Read actual Shell links with non-ANSI names without installing an application.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path (Split-Path $PSScriptRoot -Parent) 'shortcut-utils.ps1')
$Temporary = Join-Path ([IO.Path]::GetTempPath()) ('secretary-shortcut-test-' + [guid]::NewGuid().ToString('N'))
New-Item $Temporary -ItemType Directory | Out-Null
try {
    $Target = Join-Path $env:SystemRoot 'System32\notepad.exe'
    $Shell = New-Object -ComObject WScript.Shell
    # Code points keep this test usable in both Windows PowerShell 5.1 and pwsh.
    $Names = @('ascii', (-join ([char[]](0x0421,0x0435,0x043A,0x0440,0x0435,0x0442,0x0430,0x0440,0x044C))), (-join ([char[]](0x6D4B,0x8BD5))))
    foreach ($Arguments in @('', '--settings')) {
        $Source = Join-Path $Temporary 'source.lnk'
        $Link = $Shell.CreateShortcut($Source)
        $Link.TargetPath = $Target
        $Link.Arguments = $Arguments
        $Link.Save()
        foreach ($Name in $Names) {
            $Directory = Join-Path $Temporary $Name
            New-Item $Directory -ItemType Directory -Force | Out-Null
            $Path = Join-Path $Directory ($Name + '.lnk')
            Copy-Item -LiteralPath $Source -Destination $Path -Force
            $Before = (Get-FileHash -LiteralPath $Path).Hash
            $Info = Get-ShortcutInfo -LiteralPath $Path
            if ($Info.TargetPath -ne $Target -or $Info.Arguments -cne $Arguments) {
                throw "Shortcut mismatch for '$Name': $($Info | ConvertTo-Json -Compress)"
            }
            if ((Get-FileHash -LiteralPath $Path).Hash -ne $Before) { throw 'Reading modified the shortcut' }
        }
    }
    Write-Host 'PASS: Unicode shortcut names/directories, EXE targets and settings arguments (6 cases)'
} finally {
    Remove-Item -LiteralPath $Temporary -Recurse -Force
}
