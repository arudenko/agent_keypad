<#
.SYNOPSIS
    Phase 2: build and flash the RawMacroPad firmware onto the KM16.

.DESCRIPTION
    Refuses to run unless a verified 122880-byte stock backup exists.

    Clones/updates RawMacroPad into a working directory outside the repo, installs STM32
    board support if needed, compiles, and flashes over DFU.

    Put the KM16 into bootloader mode first: hold the TOP-LEFT key while plugging in USB.

.EXAMPLE
    .\scripts\flash-rawmacropad.ps1
#>
[CmdletBinding()]
param(
    [string]$BackupPath = "$env:USERPROFILE\km16-firmware-backup\original_firmware.bin",
    [string]$WorkDir    = "$env:USERPROFILE\km16-firmware-backup\rawMacroPad",
    [switch]$SkipBackupCheck
)

$ErrorActionPreference = 'Stop'
$EXPECTED_BYTES = 122880
$FQBN = 'STMicroelectronics:stm32:GenF1:pnum=BLACKPILL_F103CB,upload_method=dfu2Method,xserial=generic,usb=none'

# See backup-km16-firmware.ps1: PowerShell 5.1 turns a native command's redirected stderr
# into fatal ErrorRecords under $ErrorActionPreference='Stop'. dfu-util uses stderr for
# ordinary progress output.
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

# --- gate on a verified backup ------------------------------------------------
if (-not $SkipBackupCheck) {
    if (-not (Test-Path $BackupPath)) {
        Write-Host "STOP: no stock firmware backup at $BackupPath" -ForegroundColor Red
        Write-Host "Run .\scripts\backup-km16-firmware.ps1 first."
        exit 1
    }
    $size = (Get-Item $BackupPath).Length
    if ($size -ne $EXPECTED_BYTES) {
        Write-Host "STOP: backup is $size bytes, expected $EXPECTED_BYTES." -ForegroundColor Red
        Write-Host "Re-run the backup; do not flash without a good one."
        exit 1
    }
    Write-Host "Stock backup verified ($size bytes)." -ForegroundColor Green
}

foreach ($tool in 'dfu-util', 'arduino-cli') {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
        Write-Error "$tool not found on PATH. Install it (winget install $tool) and reopen the shell."
    }
}

# --- source -------------------------------------------------------------------
if (Test-Path (Join-Path $WorkDir '.git')) {
    Write-Host "== Updating RawMacroPad checkout ==" -ForegroundColor Cyan
    git -C $WorkDir pull --ff-only
} else {
    Write-Host "== Cloning RawMacroPad ==" -ForegroundColor Cyan
    git clone https://github.com/toptensoftware/rawMacroPad.git $WorkDir
}
Write-Host "firmware revision: $(git -C $WorkDir rev-parse --short HEAD)`n"

# --- toolchain ----------------------------------------------------------------
Write-Host "== Ensuring STM32 board support ==" -ForegroundColor Cyan
& arduino-cli config add board_manager.additional_urls `
    https://github.com/stm32duino/BoardManagerFiles/raw/main/package_stmicroelectronics_index.json 2>&1 | Out-Null
& arduino-cli core update-index
& arduino-cli core install STMicroelectronics:stm32

# --- build --------------------------------------------------------------------
$sketch = Join-Path $WorkDir 'firmware\km16'
Write-Host "`n== Compiling ==" -ForegroundColor Cyan
Push-Location $sketch
try {
    & arduino-cli compile --fqbn $FQBN --export-binaries .
    if ($LASTEXITCODE -ne 0) { Write-Error "compile failed" }
} finally {
    Pop-Location
}

# arduino-cli names the output after the sketch, so this is km16.ino.bin --
# NOT the firmware.ino.bin that the upstream readme and the handoff doc both claim.
# Discover it rather than hardcoding either name.
$buildDir = Join-Path $sketch 'build\STMicroelectronics.stm32.GenF1'
$candidates = @(Get-ChildItem -Path $buildDir -Filter '*.ino.bin' -ErrorAction SilentlyContinue)
if ($candidates.Count -eq 0) {
    Write-Error "no *.ino.bin found in $buildDir"
}
if ($candidates.Count -gt 1) {
    Write-Error "ambiguous build output in ${buildDir}: $($candidates.Name -join ', ')"
}
$binary = $candidates[0].FullName
Write-Host "built: $binary ($($candidates[0].Length) bytes)" -ForegroundColor Green

# --- flash --------------------------------------------------------------------
Write-Host "`n== Bootloader check ==" -ForegroundColor Cyan
$list = Invoke-Native dfu-util @('-l')
if ($list -notmatch '1eaf:0003') {
    Write-Host "STOP: bootloader 1eaf:0003 not found." -ForegroundColor Red
    Write-Host "Unplug the KM16, hold the TOP-LEFT key, plug it back in, then re-run."
    exit 1
}

Write-Host "`n== Flashing ==" -ForegroundColor Cyan
$flashOutput = Invoke-Native dfu-util @('-d', '1eaf:0003', '-a', '2', '-D', $binary)
Write-Host $flashOutput

# dfu-util's exit code is unreliable here (it often reports the detach as an error), so
# treat the transfer summary as the success signal.
if ($flashOutput -notmatch 'File downloaded successfully|Download done') {
    Write-Host "`nFlash did not report success. If the device is unresponsive, restore with:" -ForegroundColor Red
    Write-Host "  .\scripts\restore-km16-firmware.ps1"
    exit 1
}

Write-Host "`nFlashed. The device should reboot with the layer indicator FLASHING RED -" -ForegroundColor Green
Write-Host "that means the watchdog is armed and no host client is connected yet. Expected.`n"
Write-Host 'Verify RAW HID enumeration, then run the hardware self-test:'
Write-Host '  .\.venv\Scripts\python.exe tools\check_device.py'
Write-Host '  .\.venv\Scripts\python.exe tools\hw_selftest.py'
exit 0
