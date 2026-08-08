<#
.SYNOPSIS
    Roll the KM16 back to its stock firmware.

.DESCRIPTION
    Put the KM16 into bootloader mode first: hold the TOP-LEFT key while plugging in USB.

.EXAMPLE
    .\scripts\restore-km16-firmware.ps1
#>
[CmdletBinding()]
param(
    [string]$InputPath = "$env:USERPROFILE\km16-firmware-backup\original_firmware.bin"
)

$ErrorActionPreference = 'Stop'
$EXPECTED_BYTES = 122880

# See backup-km16-firmware.ps1 for why native stderr needs this wrapper on PowerShell 5.1.
function Invoke-Native {
    param([Parameter(Mandatory)][string]$Exe, [string[]]$Arguments = @())
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $output = & $Exe @Arguments 2>&1 | ForEach-Object { $_.ToString() }
        return ($output -join [Environment]::NewLine)
    } finally {
        $ErrorActionPreference = $prev
    }
}

if (-not (Get-Command dfu-util -ErrorAction SilentlyContinue)) {
    Write-Error "dfu-util not found on PATH."
}
if (-not (Test-Path $InputPath)) {
    Write-Error "No backup at $InputPath"
}

$size = (Get-Item $InputPath).Length
if ($size -ne $EXPECTED_BYTES) {
    Write-Host "WARNING: backup is $size bytes, expected $EXPECTED_BYTES." -ForegroundColor Yellow
    $answer = Read-Host "Restore anyway? (yes/no)"
    if ($answer -ne 'yes') { exit 1 }
}

$list = Invoke-Native dfu-util @('-l')
if ($list -notmatch '1eaf:0003') {
    Write-Host "Bootloader 1eaf:0003 not found." -ForegroundColor Red
    Write-Host "Unplug the KM16, hold the TOP-LEFT key, plug it back in, then re-run."
    exit 1
}

Write-Host "== Restoring $InputPath ==" -ForegroundColor Cyan
Write-Host (Invoke-Native dfu-util @('-d', '1eaf:0003', '-a', '2', '-D', $InputPath))

Write-Host "`nRestored. Replug the device normally; it should enumerate as the stock KM16" -ForegroundColor Green
Write-Host "(VID 0x5343 / PID 0x0080) and work with VIA again."
exit 0
