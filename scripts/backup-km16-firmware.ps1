<#
.SYNOPSIS
    Phase 1: back up the KM16's stock firmware before any flash.

.DESCRIPTION
    Verifies the device really is the expected STM32duino bootloader (1eaf:0003), reads the
    stock firmware, and checks the result is exactly 122880 bytes.

    The backup is written OUTSIDE the repo by default: it is device-specific, and the repo
    .gitignore blocks *.bin deliberately.

    Put the KM16 into bootloader mode first: hold the TOP-LEFT key while plugging in USB.

.EXAMPLE
    .\scripts\backup-km16-firmware.ps1
#>
[CmdletBinding()]
param(
    [string]$OutputPath = "$env:USERPROFILE\km16-firmware-backup\original_firmware.bin"
)

$ErrorActionPreference = 'Stop'
$EXPECTED_BYTES = 122880

# PowerShell 5.1 wraps a native command's redirected stderr in ErrorRecords, which
# $ErrorActionPreference='Stop' then treats as fatal. dfu-util writes its normal progress
# and its harmless "cannot open <other device>" notes to stderr, so capture output with
# the preference relaxed and flatten the records back to plain text.
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
    Write-Error "dfu-util not found on PATH. Install it: winget install dfu-util   (or https://dfu-util.sourceforge.net/releases/)"
}

Write-Host "== Checking for the STM32duino bootloader ==" -ForegroundColor Cyan
Write-Host "If nothing is listed, unplug the KM16, hold the TOP-LEFT key, and plug it back in.`n"

$list = Invoke-Native dfu-util @('-l')
Write-Host $list

if ($list -notmatch '1eaf:0003') {
    Write-Host "STOP." -ForegroundColor Red
    Write-Host "Expected bootloader 1eaf:0003 (STM32duino) was not found."
    Write-Host "This unit may be a different hardware revision than the one RawMacroPad was"
    Write-Host "reverse engineered from. Do NOT flash. Investigate first."
    exit 1
}
Write-Host "Found the expected 1eaf:0003 bootloader.`n" -ForegroundColor Green

$dir = Split-Path -Parent $OutputPath
if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }

if (Test-Path $OutputPath) {
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $archived = "$OutputPath.$stamp.bak"
    Write-Host "Existing backup found; preserving it as $archived" -ForegroundColor Yellow
    Move-Item $OutputPath $archived
}

Write-Host "== Reading stock firmware to $OutputPath ==" -ForegroundColor Cyan
Write-Host "'Error during upload (LIBUSB_ERROR_PIPE)' at the end is EXPECTED.`n"

Write-Host (Invoke-Native dfu-util @('-d', '1eaf:0003', '-a', '2', '-U', $OutputPath))
Write-Host ""

if (-not (Test-Path $OutputPath)) {
    Write-Error "No backup file was produced. Do not flash."
}

$size = (Get-Item $OutputPath).Length
if ($size -ne $EXPECTED_BYTES) {
    Write-Host "STOP." -ForegroundColor Red
    Write-Host "Backup is $size bytes; expected exactly $EXPECTED_BYTES (120 KB)."
    Write-Host "The read was incomplete. Do NOT flash until you have a full backup."
    exit 1
}

Write-Host "Backup verified: $size bytes at $OutputPath" -ForegroundColor Green
Write-Host "SHA256: $((Get-FileHash $OutputPath -Algorithm SHA256).Hash)"
Write-Host "Copy this file somewhere safe (it is intentionally not version controlled)."
Write-Host "Restore with: .\scripts\restore-km16-firmware.ps1 -InputPath `"$OutputPath`""

# dfu-util exits non-zero after the expected LIBUSB_ERROR_PIPE; the verified byte count is
# the real success signal, so don't leak its exit code to the caller.
exit 0
