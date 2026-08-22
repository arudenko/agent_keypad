#!/usr/bin/env bash
# Back up the KM16's stock firmware before any flash (macOS).
#
# Verifies the device really is the expected STM32duino bootloader (1eaf:0003), reads the
# stock firmware, and refuses to call it a backup unless the result is exactly 122880
# bytes. Writes backup.bin plus backup.sha256 next to it.
#
# The backup lands OUTSIDE the repo by default: it is device-specific, and the repo
# .gitignore blocks *.bin deliberately.
#
# Put the KM16 into bootloader mode first: hold the TOP-LEFT key while plugging in USB.
# No Zadig/WinUSB step exists on macOS; dfu-util talks to the bootloader directly.
#
# Usage: scripts/backup-km16-firmware.sh [output.bin]

set -euo pipefail

EXPECTED_BYTES=122880
OUTPUT="${1:-$HOME/km16-firmware-backup/backup.bin}"

if ! command -v dfu-util >/dev/null 2>&1; then
    echo "dfu-util not found on PATH. Install it: brew install dfu-util" >&2
    exit 1
fi

echo "== Checking for the STM32duino bootloader =="
echo "If nothing is listed, unplug the KM16, hold the TOP-LEFT key, and plug it back in."
echo

# dfu-util writes ordinary progress to stderr and its exit code is unreliable; the
# verified artifacts are the success signal throughout, never the exit code.
list="$(dfu-util -l 2>&1 || true)"
echo "$list"

if ! grep -q '1eaf:0003' <<<"$list"; then
    echo "STOP." >&2
    echo "Expected bootloader 1eaf:0003 (STM32duino) was not found." >&2
    echo "This unit may be a different hardware revision than the one RawMacroPad was" >&2
    echo "reverse engineered from. Do NOT flash. Investigate first." >&2
    exit 1
fi
echo "Found the expected 1eaf:0003 bootloader."
echo

mkdir -p "$(dirname "$OUTPUT")"

if [[ -e "$OUTPUT" ]]; then
    stamp="$(date +%Y%m%d-%H%M%S)"
    echo "Existing backup found; preserving it as $OUTPUT.$stamp.bak"
    mv "$OUTPUT" "$OUTPUT.$stamp.bak"
    [[ -e "$OUTPUT.sha256" ]] && mv "$OUTPUT.sha256" "$OUTPUT.sha256.$stamp.bak"
fi

echo "== Reading stock firmware to $OUTPUT =="
echo "'Error during upload (LIBUSB_ERROR_PIPE)' at the end is EXPECTED."
echo

dfu-util -d 1eaf:0003 -a 2 -U "$OUTPUT" 2>&1 || true
echo

if [[ ! -e "$OUTPUT" ]]; then
    echo "No backup file was produced. Do not flash." >&2
    exit 1
fi

size="$(stat -f%z "$OUTPUT" 2>/dev/null || stat -c%s "$OUTPUT")"
if [[ "$size" -ne "$EXPECTED_BYTES" ]]; then
    echo "STOP." >&2
    echo "Backup is $size bytes; expected exactly $EXPECTED_BYTES (120 KB)." >&2
    echo "The read was incomplete. Do NOT flash until you have a full backup." >&2
    exit 1
fi

(cd "$(dirname "$OUTPUT")" && shasum -a 256 "$(basename "$OUTPUT")" > "$(basename "$OUTPUT").sha256")

echo "Backup verified: $size bytes at $OUTPUT"
echo "SHA256: $(cut -d' ' -f1 "$OUTPUT.sha256")"
echo "Copy both files somewhere safe (they are intentionally not version controlled)."
echo "Restore with: scripts/restore-km16-firmware.sh \"$OUTPUT\""
