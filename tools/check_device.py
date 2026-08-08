r"""Report what the KM16 is currently enumerating as.

    .\.venv\Scripts\python.exe tools\check_device.py

Exit code 0 means RawMacroPad RAW HID is live and ready.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import hid  # noqa: E402

from herdr_km16.km16 import PID, STOCK_PID, STOCK_VID, VID  # noqa: E402


def main() -> int:
    devices = hid.enumerate()

    raw = [d for d in devices if d["vendor_id"] == VID and d["product_id"] == PID]
    stock = [d for d in devices if d["vendor_id"] == STOCK_VID and d["product_id"] == STOCK_PID]

    if raw:
        print(f"RawMacroPad RAW HID present: {VID:#06x}:{PID:#06x}  ({len(raw)} interface(s))")
        for d in raw:
            print(
                f"  manufacturer={d.get('manufacturer_string')!r} "
                f"product={d.get('product_string')!r} "
                f"usage_page={d.get('usage_page'):#06x} usage={d.get('usage'):#06x}"
            )
        print("\nReady. Next: .\\.venv\\Scripts\\python.exe tools\\hw_selftest.py")
        return 0

    if stock:
        print(f"KM16 on STOCK firmware: {STOCK_VID:#06x}:{STOCK_PID:#06x} ({len(stock)} interface(s))")
        print("Not yet flashed. See README.md Phase 1/2.")
        return 1

    print("No KM16 found on either the RawMacroPad or stock USB IDs.")
    print("If you just flashed, unplug and replug the device normally (no key held).")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
