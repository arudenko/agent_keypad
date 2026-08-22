#!/usr/bin/env bash
# Build and flash the patched RawMacroPad firmware onto the KM16 (macOS).
#
# Refuses to run unless a VERIFIED stock backup exists: exactly 122880 bytes AND matching
# its recorded backup.sha256. Clones/updates RawMacroPad outside the repo, pins the
# upstream revision, applies the local patches (the 0x06 fall-through reboots the MCU on
# every single-LED write -- never flash unpatched), compiles, and flashes over DFU.
#
# Put the KM16 into bootloader mode first: hold the TOP-LEFT key while plugging in USB.
# Deps: brew install dfu-util arduino-cli    (no Zadig/WinUSB step exists on macOS)
#
# Usage: scripts/flash-rawmacropad.sh [backup.bin] [workdir]

set -euo pipefail

EXPECTED_BYTES=122880
BACKUP="${1:-$HOME/km16-firmware-backup/backup.bin}"
WORKDIR="${2:-$HOME/km16-firmware-backup/rawMacroPad}"
FQBN='STMicroelectronics:stm32:GenF1:pnum=BLACKPILL_F103CB,upload_method=dfu2Method,xserial=generic,usb=none'
# Pinned rather than tracking HEAD, because we apply local patches on top and an
# upstream change would silently break them.
UPSTREAM_REV='ead652e9597a0ccada7d5fa720c26cff0e8b416e'
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# --- gate on a verified backup ------------------------------------------------
if [[ ! -e "$BACKUP" ]]; then
    echo "STOP: no stock firmware backup at $BACKUP" >&2
    echo "Run scripts/backup-km16-firmware.sh first." >&2
    exit 1
fi
size="$(stat -f%z "$BACKUP" 2>/dev/null || stat -c%s "$BACKUP")"
if [[ "$size" -ne "$EXPECTED_BYTES" ]]; then
    echo "STOP: backup is $size bytes, expected $EXPECTED_BYTES." >&2
    echo "Re-run the backup; do not flash without a good one." >&2
    exit 1
fi
if [[ ! -e "$BACKUP.sha256" ]]; then
    echo "STOP: $BACKUP.sha256 is missing, so the backup cannot be verified." >&2
    echo "Re-run scripts/backup-km16-firmware.sh." >&2
    exit 1
fi
if ! (cd "$(dirname "$BACKUP")" && shasum -a 256 -c "$(basename "$BACKUP").sha256" >/dev/null 2>&1); then
    echo "STOP: backup does not match its recorded SHA256. It may be corrupt." >&2
    echo "Re-run scripts/backup-km16-firmware.sh." >&2
    exit 1
fi
echo "Stock backup verified ($size bytes, SHA256 ok)."

for tool in dfu-util arduino-cli git; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        echo "$tool not found on PATH. Install it: brew install $tool" >&2
        exit 1
    fi
done

# --- explicit consent ---------------------------------------------------------
echo
echo "This REPLACES the stock VIA firmware with RawMacroPad (restore is scripted but"
echo "at your own risk). Type 'flash' to proceed:"
read -r answer
if [[ "$answer" != "flash" ]]; then
    echo "Aborted; nothing was written."
    exit 1
fi

# --- source -------------------------------------------------------------------
if [[ -d "$WORKDIR/.git" ]]; then
    echo "== Updating RawMacroPad checkout =="
    git -C "$WORKDIR" fetch --quiet origin
else
    echo "== Cloning RawMacroPad =="
    git clone --quiet https://github.com/toptensoftware/rawMacroPad.git "$WORKDIR"
fi

# Discard any previously applied patches so this is repeatable.
git -C "$WORKDIR" checkout --quiet --force "$UPSTREAM_REV"
git -C "$WORKDIR" clean --quiet -fd firmware
echo "upstream revision: $(git -C "$WORKDIR" rev-parse --short HEAD)"

# --- local firmware patches ---------------------------------------------------
patch_dir="$REPO_ROOT/firmware/patches"
found_patch=0
for patch in "$patch_dir"/*.patch; do
    [[ -e "$patch" ]] || continue
    found_patch=1
    echo "applying patch: $(basename "$patch")"
    if ! git -C "$WORKDIR" apply --verbose "$patch"; then
        echo "failed to apply $(basename "$patch") -- refusing to flash unpatched firmware" >&2
        exit 1
    fi
done
if [[ "$found_patch" -eq 0 ]]; then
    echo "WARNING: no firmware patches found in $patch_dir" >&2
fi

# The 0x06 fall-through reboots the MCU on every single-LED write; never flash without
# the fix. (perl for the multi-line match; macOS grep has no -Pz.)
if ! perl -0777 -ne 'exit(!(m/case 0x06:.*?\n\s+break;\s*\n\s*\n?\s*case 0xFF:/s))' \
        "$WORKDIR/firmware/km16/km16.ino"; then
    echo "sanity check failed: case 0x06 still falls through to the 0xFF reset" >&2
    exit 1
fi
echo "verified: case 0x06 breaks before the 0xFF reset"
echo

# --- toolchain ----------------------------------------------------------------
echo "== Ensuring STM32 board support =="
arduino-cli config add board_manager.additional_urls \
    https://github.com/stm32duino/BoardManagerFiles/raw/main/package_stmicroelectronics_index.json \
    >/dev/null 2>&1 || true
arduino-cli core update-index
arduino-cli core install STMicroelectronics:stm32

# --- build --------------------------------------------------------------------
sketch="$WORKDIR/firmware/km16"
echo
echo "== Compiling =="
(cd "$sketch" && arduino-cli compile --fqbn "$FQBN" --export-binaries .)

# arduino-cli names the output after the sketch (km16.ino.bin) -- NOT firmware.ino.bin as
# upstream's readme claims. Discover it rather than hardcoding either name.
build_dir="$sketch/build/STMicroelectronics.stm32.GenF1"
candidates=("$build_dir"/*.ino.bin)
if [[ ! -e "${candidates[0]}" ]]; then
    echo "no *.ino.bin found in $build_dir" >&2
    exit 1
fi
if [[ "${#candidates[@]}" -gt 1 ]]; then
    echo "ambiguous build output in $build_dir: ${candidates[*]}" >&2
    exit 1
fi
binary="${candidates[0]}"
echo "built: $binary ($(stat -f%z "$binary" 2>/dev/null || stat -c%s "$binary") bytes)"

# --- flash --------------------------------------------------------------------
echo
echo "== Bootloader check =="
list="$(dfu-util -l 2>&1 || true)"
if ! grep -q '1eaf:0003' <<<"$list"; then
    echo "STOP: bootloader 1eaf:0003 not found." >&2
    echo "Unplug the KM16, hold the TOP-LEFT key, plug it back in, then re-run." >&2
    exit 1
fi

echo
echo "== Flashing =="
# dfu-util's exit code is unreliable here (it often reports the detach as an error), so
# treat the transfer summary as the success signal.
flash_output="$(dfu-util -d 1eaf:0003 -a 2 -D "$binary" 2>&1 || true)"
echo "$flash_output"

if ! grep -qE 'File downloaded successfully|Download done' <<<"$flash_output"; then
    echo >&2
    echo "Flash did not report success. If the device is unresponsive, restore with:" >&2
    echo "  scripts/restore-km16-firmware.sh" >&2
    exit 1
fi

echo
echo "Flashed. The device should reboot with the layer indicator FLASHING RED -"
echo "that means the watchdog is armed and no host client is connected yet. Expected."
echo
echo "Verify RAW HID enumeration, then run the hardware self-test:"
echo "  .venv/bin/python tools/check_device.py"
echo "  .venv/bin/python tools/hw_selftest.py"
