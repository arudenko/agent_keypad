"""RAW HID transport for a KM16 running RawMacroPad firmware.

A reviewed local implementation rather than the upstream client, because upstream's
``set_led()`` sends command 0x04 ("set whole chain to one colour") with an extra index byte
instead of 0x06 ("set individual LED"). Confirmed present at upstream HEAD ead652e.
See CLAUDE.md and tests/test_led_protocol.py.

Packet builders are pure functions so the protocol can be tested without hardware.
"""

from __future__ import annotations

import asyncio
import logging
import queue
import threading
import time
from typing import Callable, Iterable, Sequence

log = logging.getLogger("herdr_km16.km16")

# hidapi device handles are not thread-safe. Reading on one thread while writing on
# another makes the Windows backend fail with OSError('read error') within seconds, so a
# single thread owns the handle and does both. Reads use a short timeout to keep queued
# writes responsive.
READ_TIMEOUT_MS = 20


def watchdog_gap_tripped(gap_ms: float, interval_ms: int) -> bool:
    """Did enough time pass between pings that the firmware watchdog will have fired?

    Uses the full timeout as the threshold. The ping rate is twice that, so a normal gap is
    ~half the timeout and this cannot fire on ordinary jitter.
    """
    return interval_ms > 0 and gap_ms >= interval_ms

VID = 0x1209
PID = 0x88BF
STOCK_VID = 0x5343  # unflashed KM16, useful for diagnostics
STOCK_PID = 0x0080

# RawMacroPad's raw HID vendor usage page, read off the flashed device and confirmed in
# its report descriptor (usbd_customhid_if.c: "Usage Page (Vendor Defined 0xFF00)").
# NOT QMK/VIA's 0xFF60 -- the STOCK firmware exposes a 0xFF60 interface for the VIA
# configurator, which is exactly the kind of near-miss this filter exists to reject.
# macOS exposes each usage page as its own HID interface, and opening by bare VID/PID
# grabs whichever enumerates first, whose reads then silently return nothing.
RAW_USAGE_PAGE = 0xFF00

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
    """Single LED. Command 0x06 -- upstream's 0x04 is the bug this module exists to avoid.

    Requires patched firmware: stock km16.ino falls through from case 0x06 into the 0xFF
    reset, so this packet reboots an unpatched device. See CLAUDE.md and
    firmware/patches/. Prefer set_frame() anyway -- one packet, always consistent.
    """
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
        self._watchdog_task: asyncio.Task | None = None
        self._handlers: list[Callable[[dict], None]] = []
        self._last_frame: dict[int, tuple[int, ...]] = {}
        # The owning thread and its outbound queue.
        self._writes: queue.Queue[bytes] = queue.Queue()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        # Diagnostics: a silent read loop is indistinguishable from an idle keypad.
        self.read_error: Exception | None = None
        self.reads_attempted = 0
        self.reports_received = 0

    @staticmethod
    def raw_interface_path() -> bytes | None:
        """The hid path of the RAW HID interface, or None if it is not enumerated."""
        import hid

        for info in hid.enumerate(VID, PID):
            if info.get("usage_page") == RAW_USAGE_PAGE:
                return info["path"]
        return None

    @classmethod
    def open(cls) -> "KM16":
        import hid

        # Open by path, never by bare VID/PID: the device enumerates one HID interface
        # per usage page and only the RAW_USAGE_PAGE one speaks this protocol. If reads
        # then return nothing at all, the process is missing the macOS Input Monitoring
        # permission (System Settings > Privacy & Security > Input Monitoring) -- hidapi
        # opens the device fine and fails silently on every read.
        path = cls.raw_interface_path()
        if path is None:
            raise OSError(
                f"no RAW HID interface (usage page 0x{RAW_USAGE_PAGE:04X}) "
                f"enumerated for {VID:04x}:{PID:04x}"
            )
        if hasattr(hid, "Device"):  # apmorton/hid
            device = hid.Device(path=path)
        else:  # cython-hidapi
            device = hid.device()
            device.open_path(path)
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
        """Queue a packet for the owning thread. Fire and forget."""
        if self._thread is None:
            # No I/O thread yet (LED setup before start_reading): safe to write inline.
            self._hid.write(packet)
        else:
            self._writes.put(packet)

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
        """Master power only (firmware drives PB14). Chains still need enable_chain()."""
        self._write(build_enable_all_leds(enable))

    def enable_chain(self, chain: int, enable: bool) -> None:
        self._write(build_enable_chain(chain, enable))

    def power_on_leds(self) -> None:
        """Bring the LEDs fully up.

        The firmware has three independent switches and *all three default to off*:
        master power (0x02), the key chain and the underglow chain (0x03 per chain).
        setKeyLed() only marks the frame dirty when its chain is enabled, so with only
        the master on, colours are stored and silently never shifted out. Order matters:
        power first, then chains, because enabling a chain pushes its pixels immediately.
        """
        self.enable_all_leds(True)
        self.enable_chain(CHAIN_KEYS, True)
        self.enable_chain(CHAIN_UNDERGLOW, True)

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
        last = time.monotonic()
        try:
            while True:
                await asyncio.sleep(interval_ms / 2000)  # ping at twice the timeout rate
                now = time.monotonic()
                gap_ms = (now - last) * 1000
                last = now
                if watchdog_gap_tripped(gap_ms, interval_ms):
                    # A tripped firmware watchdog does not just flash the indicator: it runs
                    # setEnableKeyLeds(false) / setEnableUnderglow(false). Resuming pings
                    # clears the trip but never re-enables the chains, so without this the
                    # pad stays dark forever while input keeps working.
                    log.warning(
                        "watchdog gap %.0fms exceeded %dms timeout; re-enabling LED chains",
                        gap_ms, interval_ms,
                    )
                    self.power_on_leds()
                    self._last_frame.clear()  # force a full repaint, not a deduped no-op
                self._write(build_watchdog(interval_ms))
        except asyncio.CancelledError:
            pass

    # --- input ------------------------------------------------------------

    def start_reading(self) -> None:
        """Hand the device over to its owning thread."""
        self._loop = asyncio.get_running_loop()
        self._thread = threading.Thread(target=self._io_loop, name="km16-io", daemon=True)
        self._thread.start()

    def _io_loop(self) -> None:
        """Sole owner of the hidapi handle: drains writes, then polls for input."""
        try:
            while not self._stop.is_set():
                while True:
                    try:
                        self._hid.write(self._writes.get_nowait())
                    except queue.Empty:
                        break
                data = self._hid.read(REPORT_SIZE, timeout_ms=READ_TIMEOUT_MS)
                self.reads_attempted += 1
                if not data:
                    continue
                self.reports_received += 1
                event = parse_event(data)
                if event and self._loop is not None:
                    self._loop.call_soon_threadsafe(self._dispatch, event)

            # Flush anything queued during shutdown, e.g. the final blank LED frame.
            while True:
                try:
                    self._hid.write(self._writes.get_nowait())
                except queue.Empty:
                    break
        except Exception as exc:
            # Without this the thread dies silently and the pad just looks dead to input.
            self.read_error = exc
            log.exception("KM16 I/O loop failed")

    def _dispatch(self, event: dict) -> None:
        for handler in self._handlers:
            try:
                handler(event)
            except Exception:
                log.exception("error in KM16 event handler")

    def close(self) -> None:
        if self._watchdog_task:
            self._watchdog_task.cancel()
            self._watchdog_task = None
        self._stop.set()
        if self._thread is not None:
            # Let the owning thread finish its current read and drain pending writes.
            self._thread.join(timeout=1.0)
            self._thread = None
        if self._hid is not None:
            self._hid.close()
            self._hid = None
