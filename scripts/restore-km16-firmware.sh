#!/usr/bin/env bash
# Roll the KM16 back to its stock firmware (macOS).
#
# Put the KM16 into bootloader mode first: hold the TOP-LEFT key while plugging in USB.
#
# Usage: scripts/restore-km16-firmware.sh [backup.bin]

set -euo pipefail

EXPECTED_BYTES=122880
INPUT="${1:-$HOME/km16-firmware-backup/backup.bin}"

if ! command -v dfu-util >/dev/null 2>&1; then
    echo "dfu-util not found on PATH. Install it: brew install dfu-util" >&2
    exit 1
fi
if [[ ! -e "$INPUT" ]]; then
    echo "No backup at $INPUT" >&2
    exit 1
fi

size="$(stat -f%z "$INPUT" 2>/dev/null || stat -c%s "$INPUT")"
if [[ "$size" -ne "$EXPECTED_BYTES" ]]; then
    echo "WARNING: backup is $size bytes, expected $EXPECTED_BYTES."
    printf "Restore anyway? (yes/no) "
    read -r answer
    [[ "$answer" == "yes" ]] || exit 1
fi
if [[ -e "$INPUT.sha256" ]] && \
        ! (cd "$(dirname "$INPUT")" && shasum -a 256 -c "$(basename "$INPUT").sha256" >/dev/null 2>&1); then
    echo "WARNING: $INPUT does not match its recorded SHA256."
    printf "Restore anyway? (yes/no) "
    read -r answer
    [[ "$answer" == "yes" ]] || exit 1
fi

list="$(dfu-util -l 2>&1 || true)"
if ! grep -q '1eaf:0003' <<<"$list"; then
    echo "Bootloader 1eaf:0003 not found." >&2
    echo "Unplug the KM16, hold the TOP-LEFT key, plug it back in, then re-run." >&2
    exit 1
fi

echo "== Restoring $INPUT =="
# dfu-util's exit code is unreliable; the transfer summary is the success signal.
restore_output="$(dfu-util -d 1eaf:0003 -a 2 -D "$INPUT" 2>&1 || true)"
echo "$restore_output"

if ! grep -qE 'File downloaded successfully|Download done' <<<"$restore_output"; then
    echo "Restore did not report success; re-run with the device in bootloader mode." >&2
    exit 1
fi

echo
echo "Restored. Replug the device normally; it should enumerate as the stock KM16"
echo "(VID 0x5343 / PID 0x0080) and work with VIA again."
