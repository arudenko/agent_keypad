r"""Bisect which specific LED command destabilises the KM16.

    .\.venv\Scripts\python.exe tools\diag_chains.py

diag_read.py showed plain reads + chain-0 writes are stable, so the culprit is one of the
other commands the self-test issues. Each trial reopens the device and reads afterwards to
see whether the device survived.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import hid  # noqa: E402

from herdr_km16.km16 import (  # noqa: E402
    CHAIN_KEYS,
    CHAIN_LAYER,
    CHAIN_UNDERGLOW,
    PID,
    VID,
    build_chain_array,
    build_enable_all_leds,
    build_set_led,
)

SETTLE = 6.0


def open_device():
    d = hid.device()
    d.open(VID, PID)
    return d


def trial(name: str, packets: list[bytes]):
    """Send packets, then read for SETTLE seconds and see if the device stays healthy."""
    print(f"\n=== {name} ===")
    try:
        d = open_device()
    except Exception as exc:
        print(f"    CANNOT OPEN: {type(exc).__name__}: {exc}")
        return False
    reads = 0
    start = time.monotonic()
    try:
        for p in packets:
            d.write(p)
            time.sleep(0.05)
        while time.monotonic() - start < SETTLE:
            d.read(64, timeout_ms=20)
            reads += 1
        print(f"    OK   {len(packets)} packet(s), then {reads} clean reads")
        return True
    except Exception as exc:
        print(f"    FAILED after {time.monotonic() - start:.1f}s, {reads} reads")
        print(f"    {type(exc).__name__}: {exc}")
        return False
    finally:
        try:
            d.close()
        except Exception:
            pass


def main() -> int:
    if not [x for x in hid.enumerate() if x["vendor_id"] == VID and x["product_id"] == PID]:
        print("No RawMacroPad found.")
        return 1

    results = {}
    results["enable_all_leds"] = trial(
        "enable_all_leds(True)", [build_enable_all_leds(True)])
    results["chain 0 array"] = trial(
        "chain 0 (16 keys) array", [build_chain_array(CHAIN_KEYS, [0x101010] * 16)])
    results["chain 1 array"] = trial(
        "chain 1 (6 underglow) array", [build_chain_array(CHAIN_UNDERGLOW, [0x100000] * 6)])
    results["chain 2 array"] = trial(
        "chain 2 (layer indicator) array  <-- prime suspect",
        [build_chain_array(CHAIN_LAYER, [0xFF0000])])
    results["set_led 0x06"] = trial(
        "set_led 0x06 on chain 0", [build_set_led(CHAIN_KEYS, 0, 0xFFFFFF)])

    print("\n=== summary ===")
    for name, ok in results.items():
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    if not results["chain 2 array"]:
        print("\nchain 2 via command 0x05 destabilises the device; use 0x06 or 0x04 for it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
