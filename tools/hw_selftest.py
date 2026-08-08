r"""Phase 3: prove the hardware before wiring in Herdr.

Run after flashing RawMacroPad:

    .\.venv\Scripts\python.exe tools\hw_selftest.py

Exercises every LED chain and prints every key/encoder event. Nothing here touches Herdr.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from herdr_km16.km16 import (  # noqa: E402
    CHAIN_KEYS,
    CHAIN_LAYER,
    CHAIN_UNDERGLOW,
    KEY_LEFT_ENCODER,
    KEY_MAIN_ENCODER,
    KEY_RIGHT_ENCODER,
    KM16,
)

KEY_LABELS = {
    KEY_MAIN_ENCODER: "MAIN ENCODER push",
    KEY_LEFT_ENCODER: "LEFT ENCODER push",
    KEY_RIGHT_ENCODER: "RIGHT ENCODER push",
}
TEST_COLORS = [
    0xFF0000, 0x00FF00, 0x0000FF, 0xFFFF00,
    0xFF00FF, 0x00FFFF, 0xFF8000, 0x8000FF,
    0x00FF80, 0x808080, 0xFF0080, 0x80FF00,
    0x0080FF, 0xFFFFFF, 0x400000, 0x004040,
]


def _preflight() -> bool:
    if KM16.is_present():
        return True
    if KM16.stock_firmware_present():
        print("A KM16 is attached but still on STOCK firmware (VID 0x5343 / PID 0x0080).")
        print("Flash RawMacroPad first - see README.md Phase 1/2.")
    else:
        print("No KM16 found. Expected RAW HID VID 0x1209 / PID 0x88BF.")
    return False


async def main() -> int:
    if not _preflight():
        return 1

    device = KM16.open()
    seen_keys: set[int] = set()
    seen_encoders: set[int] = set()

    def on_event(event: dict) -> None:
        if event["kind"] == "key":
            label = KEY_LABELS.get(event["key"], f"key {event['key']}")
            print(f"  {label:22} {'PRESS' if event['pressed'] else 'release'}")
            if event["pressed"]:
                seen_keys.add(event["key"])
        else:
            print(f"  encoder {event['encoder']:<14} delta {event['delta']:+d}")
            seen_encoders.add(event["encoder"])

    device.on_event(on_event)
    device.start_reading()
    device.set_watchdog(2000)
    device.enable_all_leds(True)

    try:
        print("\nWatch the keypad - LED sequence starts in 3s...")
        await asyncio.sleep(3)

        print("\n[1] all LEDs off")
        device.set_frame(CHAIN_KEYS, [0] * 16, force=True)
        device.set_frame(CHAIN_UNDERGLOW, [0] * 6, force=True)
        await asyncio.sleep(0.6)

        print("[2] walking each key LED one at a time (watch for a single moving dot)")
        for i in range(16):
            frame = [0] * 16
            frame[i] = 0x00FF00
            device.set_frame(CHAIN_KEYS, frame, force=True)
            await asyncio.sleep(0.12)

        print("[3] all 16 key LEDs, distinct colours")
        device.set_frame(CHAIN_KEYS, TEST_COLORS, force=True)
        await asyncio.sleep(1.5)

        print("[4] single-LED command 0x06 - key 0 white, others unchanged")
        print("    (if the WHOLE pad turns white, the 0x04/0x06 bug is back)")
        device.set_led(CHAIN_KEYS, 0, 0xFFFFFF)
        await asyncio.sleep(1.5)

        print("[5] underglow: 6 LEDs walking red")
        for i in range(6):
            frame = [0] * 6
            frame[i] = 0xFF0000
            device.set_frame(CHAIN_UNDERGLOW, frame, force=True)
            await asyncio.sleep(0.25)
        device.set_frame(CHAIN_UNDERGLOW, [0x202020] * 6, force=True)

        print("[6] layer indicator: red, green, blue")
        for color in (0xFF0000, 0x00FF00, 0x0000FF):
            device.set_frame(CHAIN_LAYER, [color], force=True)
            await asyncio.sleep(0.5)

        print("\n[7] input test - press all 16 keys, click all 3 encoders, turn each one.")
        print("    Each key lights white as it registers. Ctrl+C to stop early.\n")
        reason = "interrupted"
        while True:
            await asyncio.sleep(0.25)

            if device.read_error is not None:
                reason = f"read loop failed: {device.read_error!r}"
                break

            # Light every key that has registered, so progress is visible on the pad itself.
            device.set_frame(CHAIN_KEYS, [0xFFFFFF if i in seen_keys else 0x000000 for i in range(16)])

            # Only redraw in place on a real terminal; piped output would repeat the line.
            if sys.stdout.isatty():
                print(
                    f"\r    keys {len(seen_keys):2}/19   encoders {len(seen_encoders)}/3   "
                    f"(HID reports: {device.reports_received})   ",
                    end="",
                    flush=True,
                )
            if len(seen_keys) >= 19 and len(seen_encoders) >= 3:
                reason = "complete"
                break
    except KeyboardInterrupt:
        reason = "interrupted"
    finally:
        missing_keys = sorted(set(range(19)) - seen_keys)
        missing_encoders = sorted({16, 17, 18} - seen_encoders)
        print(f"\n\nexit reason: {reason}")
        print(f"HID reports received: {device.reports_received} (read calls: {device.reads_attempted})")
        if device.read_error is not None:
            print(f"READ ERROR: {device.read_error!r}")
        elif device.reports_received == 0 and device.reads_attempted > 0:
            print("No HID reports at all. The read path is alive but the device sent nothing --")
            print("if you were pressing keys, that points at the firmware, not this script.")
        print(f"keys seen: {len(seen_keys)}/19" + (f"  missing: {missing_keys}" if missing_keys else ""))
        print(f"encoders turned: {len(seen_encoders)}/3" + (f"  missing: {missing_encoders}" if missing_encoders else ""))
        if reason == "complete":
            print("\nAll 19 keys and 3 encoders seen. Hardware good.")
        device.set_frame(CHAIN_KEYS, [0] * 16, force=True)
        device.set_frame(CHAIN_UNDERGLOW, [0] * 6, force=True)
        device.set_watchdog(0)
        device.close()
    return 0 if reason == "complete" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        pass
