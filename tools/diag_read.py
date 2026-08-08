r"""Isolate what makes the KM16 read path fail.

    .\.venv\Scripts\python.exe tools\diag_read.py

Runs several variants against a freshly opened device and reports where each one dies.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import hid  # noqa: E402

from herdr_km16.km16 import (  # noqa: E402
    CHAIN_KEYS,
    PID,
    VID,
    build_chain_array,
    build_watchdog,
)

DURATION = 12.0


def open_device():
    d = hid.device()
    d.open(VID, PID)
    return d


def trial(name: str, *, watchdog_ms: int | None, write_every: float | None, read_timeout: int):
    print(f"\n=== {name} ===")
    print(f"    watchdog={watchdog_ms} write_every={write_every} read_timeout={read_timeout}ms")
    d = open_device()
    reads = reports = writes = 0
    last_write = last_ping = time.monotonic()
    start = time.monotonic()
    try:
        if watchdog_ms:
            d.write(build_watchdog(watchdog_ms))
        while time.monotonic() - start < DURATION:
            now = time.monotonic()
            if watchdog_ms and now - last_ping >= watchdog_ms / 2000:
                d.write(build_watchdog(watchdog_ms))
                last_ping = now
            if write_every and now - last_write >= write_every:
                shade = (writes % 2) * 0x101010
                d.write(build_chain_array(CHAIN_KEYS, [shade] * 16))
                writes += 1
                last_write = now
            data = d.read(64, timeout_ms=read_timeout)
            reads += 1
            if data:
                reports += 1
                print(f"    REPORT after {now - start:5.1f}s: {list(data[:4])}")
        print(f"    OK  survived {DURATION:.0f}s  reads={reads} reports={reports} writes={writes}")
        return True
    except Exception as exc:
        print(f"    FAILED after {time.monotonic() - start:5.1f}s  reads={reads} writes={writes}")
        print(f"    {type(exc).__name__}: {exc}")
        return False
    finally:
        try:
            d.close()
        except Exception:
            pass


def main() -> int:
    present = [x for x in hid.enumerate() if x["vendor_id"] == VID and x["product_id"] == PID]
    if not present:
        print("No RawMacroPad found.")
        return 1
    print(f"device present, {len(present)} interface(s)")
    print("Do NOT press any keys during these trials.")

    results = {
        "A read only, no writes at all": trial(
            "A read only", watchdog_ms=None, write_every=None, read_timeout=20),
        "B read + watchdog pings": trial(
            "B watchdog only", watchdog_ms=2000, write_every=None, read_timeout=20),
        "C read + LED writes, no watchdog": trial(
            "C LED writes only", watchdog_ms=None, write_every=0.25, read_timeout=20),
        "D read + watchdog + LED writes": trial(
            "D watchdog + LED", watchdog_ms=2000, write_every=0.25, read_timeout=20),
    }

    print("\n=== summary ===")
    for name, ok in results.items():
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
