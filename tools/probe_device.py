"""Print every HID interface a KM16 enumerates, and which one the daemon would open.

    .venv/bin/python tools/probe_device.py

Covers both identities: stock VIA firmware (5343:0080) and RawMacroPad (1209:88BF).
On macOS a single device enumerates one HID interface per usage page; only the
RawMacroPad interface at vendor usage page 0xFF60 speaks the LED/key protocol, which is
why KM16.open() filters on it instead of opening by bare VID/PID.

If the right interface is listed but reads return nothing, grant the process running the
daemon Input Monitoring permission: System Settings > Privacy & Security >
Input Monitoring. hidapi opens the device fine without it and fails silently on read.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from herdr_km16.km16 import (  # noqa: E402
    PID,
    RAW_USAGE_PAGE,
    STOCK_PID,
    STOCK_VID,
    VID,
    KM16,
)

IDENTITIES = [
    (STOCK_VID, STOCK_PID, "stock VIA firmware"),
    (VID, PID, "RawMacroPad firmware"),
]


def fmt_path(path) -> str:
    return path.decode(errors="replace") if isinstance(path, (bytes, bytearray)) else str(path)


def main() -> int:
    import hid

    would_open = KM16.raw_interface_path()
    found_any = False

    for vid, pid, label in IDENTITIES:
        interfaces = hid.enumerate(vid, pid)
        print(f"\n{vid:04x}:{pid:04x} ({label}): {len(interfaces)} interface(s)")
        if not interfaces:
            continue
        found_any = True
        print(f"  {'usage_page':>10}  {'usage':>6}  {'iface':>5}  {'product':<16} path")
        for info in interfaces:
            page = info.get("usage_page", 0)
            marker = "  <-- daemon opens this" if info.get("path") == would_open else ""
            print(
                f"  {page:>#10x}  {info.get('usage', 0):>#6x}  "
                f"{info.get('interface_number', -1):>5}  "
                f"{info.get('product_string', '')[:16]:<16} "
                f"{fmt_path(info.get('path', b''))}{marker}"
            )

    print()
    if would_open is not None:
        print(f"daemon would open: {fmt_path(would_open)} (usage page {RAW_USAGE_PAGE:#06x})")
    elif hid.enumerate(STOCK_VID, STOCK_PID):
        print("daemon would open: nothing -- the pad is on STOCK firmware; flash RawMacroPad first")
    elif found_any:
        print(f"daemon would open: nothing -- no interface reports usage page {RAW_USAGE_PAGE:#06x}")
    else:
        print("daemon would open: nothing -- no KM16 attached")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
