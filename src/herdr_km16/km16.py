"""RAW HID transport for a KM16 running RawMacroPad firmware.

A reviewed local implementation rather than the upstream client, because upstream's
``set_led()`` sends command 0x04 ("set whole chain to one colour") with an extra index byte
instead of 0x06 ("set individual LED"). Confirmed present at upstream HEAD ead652e.
See CLAUDE.md and tests/test_led_protocol.py.

Packet builders are pure functions so the protocol can be tested without hardware.
"""

from __future__ import annotations

import asyncio
from typing import Callable, Iterable, Sequence

VID = 0x1209
PID = 0x88BF
STOCK_VID = 0x5343  # unflashed KM16, useful for diagnostics
STOCK_PID = 0x0080

REPORT_SIZE = 64
PACKET_SIZE = REPORT_SIZE + 1  # leading 0x00 report ID

# Host -> device commands
CMD_WATCHDOG = 0x01
CMD_ENABLE_ALL_LEDS = 0x02
CMD_ENABLE_CHAIN = 0x03
CMD_CHAIN_SOLID = 0x04
CMD_CHAIN_ARRAY = 0x05
CMD_SET_LED = 0x06
CMD_RESET = 0xFF

# Device -> host events
EVT_KEY = 0x01
EVT_ENCODER = 0x02

# LED chains
CHAIN_KEYS = 0
CHAIN_UNDERGLOW = 1
CHAIN_LAYER = 2

CHAIN_SIZES = {CHAIN_KEYS: 16, CHAIN_UNDERGLOW: 6, CHAIN_LAYER: 1}

# Key indices
KEY_COUNT = 16
KEY_MAIN_ENCODER = 16
KEY_LEFT_ENCODER = 17
KEY_RIGHT_ENCODER = 18


def _packet() -> bytearray:
    """A fresh zeroed packet. Never reuse a buffer: stale bytes leak between commands."""
    return bytearray(PACKET_SIZE)


def _rgb(color: int) -> tuple[int, int, int]:
    return (color >> 16) & 0xFF, (color >> 8) & 0xFF, color & 0xFF


def build_watchdog(interval_ms: int) -> bytes:
    p = _packet()
    p[1] = CMD_WATCHDOG
    p[2] = (interval_ms >> 8) & 0xFF
    p[3] = interval_ms & 0xFF
    return bytes(p)


def build_enable_all_leds(enable: bool) -> bytes:
    p = _packet()
    p[1] = CMD_ENABLE_ALL_LEDS
    p[2] = 1 if enable else 0
    return bytes(p)


def build_enable_chain(chain: int, enable: bool) -> bytes:
    p = _packet()
    p[1] = CMD_ENABLE_CHAIN
    p[2] = chain
    p[3] = 1 if enable else 0
    return bytes(p)


def build_chain_solid(chain: int, color: int) -> bytes:
    p = _packet()
    p[1] = CMD_CHAIN_SOLID
    p[2] = chain
    p[3], p[4], p[5] = _rgb(color)
    return bytes(p)


def build_chain_array(chain: int, colors: Sequence[int]) -> bytes:
    """Whole-chain frame. Preferred for state updates: one write, always consistent."""
    expected = CHAIN_SIZES.get(chain)
    if expected is not None and len(colors) != expected:
        raise ValueError(f"chain {chain} expects {expected} colors, got {len(colors)}")
    if 3 * len(colors) + 3 > PACKET_SIZE:
        raise ValueError(f"{len(colors)} colors do not fit in one {PACKET_SIZE}-byte packet")
    p = _packet()
    p[1] = CMD_CHAIN_ARRAY
    p[2] = chain
    for i, color in enumerate(colors):
        p[i * 3 + 3], p[i * 3 + 4], p[i * 3 + 5] = _rgb(color)
    return bytes(p)


def build_set_led(chain: int, index: int, color: int) -> bytes:
    """Single LED. Command 0x06 — upstream's 0x04 is the bug this module exists to avoid."""
    size = CHAIN_SIZES.get(chain)
    if size is not None and not 0 <= index < size:
        raise ValueError(f"led index {index} out of range for chain {chain} (size {size})")
    p = _packet()
    p[1] = CMD_SET_LED
    p[2] = chain
    p[3] = index
    p[4], p[5], p[6] = _rgb(color)
    return bytes(p)


def build_reset() -> bytes:
    p = _packet()
    p[1] = CMD_RESET
    return bytes(p)


def parse_event(data: Sequence[int]) -> dict | None:
    """Decode a device->host report. Returns None for anything unrecognised."""
    if not data:
        return None
    if data[0] == EVT_KEY:
        return {"kind": "key", "key": data[1], "pressed": bool(data[2])}
    if data[0] == EVT_ENCODER:
        delta = data[2] if data[2] < 128 else data[2] - 256
        return {"kind": "encoder", "encoder": data[1], "delta": delta}
    return None


class KM16:
    """Async RAW HID connection to the keypad.

    Reads happen on a worker thread because hidapi is blocking; writes are fire-and-forget
    64-byte reports.
    """

    def __init__(self, device):
        self._hid = device
        self._read_task: asyncio.Task | None = None
        self._watchdog_task: asyncio.Task | None = None
        self._handlers: list[Callable[[dict], None]] = []
        self._last_frame: dict[int, tuple[int, ...]] = {}

    @classmethod
    def open(cls) -> "KM16":
        import hid

        if hasattr(hid, "Device"):  # apmorton/hid
            device = hid.Device(VID, PID)
        else:  # cython-hidapi
            device = hid.device()
            device.open(VID, PID)
        return cls(device)

    @staticmethod
    def is_present() -> bool:
        import hid

        return any(d["vendor_id"] == VID and d["product_id"] == PID for d in hid.enumerate())

    @staticmethod
    def stock_firmware_present() -> bool:
        """True if a KM16 is attached but still on stock (unflashed) firmware."""
        import hid

        return any(
            d["vendor_id"] == STOCK_VID and d["product_id"] == STOCK_PID for d in hid.enumerate()
        )

    def on_event(self, handler: Callable[[dict], None]) -> None:
        self._handlers.append(handler)

    def _write(self, packet: bytes) -> None:
        self._hid.write(packet)

    # --- LED output -------------------------------------------------------

    def set_frame(self, chain: int, colors: Iterable[int], force: bool = False) -> bool:
        """Push a whole chain. Skips the write when unchanged; returns True if it wrote."""
        colors = tuple(colors)
        if not force and self._last_frame.get(chain) == colors:
            return False
        self._write(build_chain_array(chain, colors))
        self._last_frame[chain] = colors
        return True

    def set_led(self, chain: int, index: int, color: int) -> None:
        self._write(build_set_led(chain, index, color))
        self._last_frame.pop(chain, None)  # per-LED write invalidates the cached frame

    def set_chain_solid(self, chain: int, color: int) -> None:
        self._write(build_chain_solid(chain, color))
        self._last_frame.pop(chain, None)

    def enable_all_leds(self, enable: bool) -> None:
        self._write(build_enable_all_leds(enable))

    def enable_chain(self, chain: int, enable: bool) -> None:
        self._write(build_enable_chain(chain, enable))

    def reset(self) -> None:
        self._write(build_reset())

    # --- watchdog ---------------------------------------------------------

    def set_watchdog(self, interval_ms: int) -> None:
        """Arm the firmware watchdog. It flashes the layer LED red if we stop pinging."""
        self._write(build_watchdog(interval_ms))
        if self._watchdog_task:
            self._watchdog_task.cancel()
            self._watchdog_task = None
        if interval_ms:
            self._watchdog_task = asyncio.get_running_loop().create_task(
                self._watchdog_loop(interval_ms)
            )

    async def _watchdog_loop(self, interval_ms: int) -> None:
        try:
            while True:
                await asyncio.sleep(interval_ms / 2000)  # ping at twice the timeout rate
                self._write(build_watchdog(interval_ms))
        except asyncio.CancelledError:
            pass

    # --- input ------------------------------------------------------------

    def start_reading(self) -> None:
        self._read_task = asyncio.get_running_loop().create_task(self._read_loop())

    async def _read_loop(self) -> None:
        loop = asyncio.get_running_loop()
        try:
            while True:
                data = await loop.run_in_executor(
                    None, lambda: self._hid.read(REPORT_SIZE, timeout_ms=100)
                )
                event = parse_event(data) if data else None
                if event:
                    for handler in self._handlers:
                        handler(event)
        except asyncio.CancelledError:
            pass

    def close(self) -> None:
        for task in (self._watchdog_task, self._read_task):
            if task:
                task.cancel()
        self._watchdog_task = self._read_task = None
        if self._hid is not None:
            self._hid.close()
            self._hid = None
