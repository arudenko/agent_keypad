"""KM16.open() must pick the RAW HID interface by usage page, never bare VID/PID.

macOS enumerates one HID interface per usage page for the same physical device. Opening
by VID/PID grabs whichever comes first -- often the keyboard interface, which opens fine
and then reads nothing. These tests fake the hid module; no hardware involved.
"""

import sys
import types

import pytest

from herdr_km16.km16 import PID, RAW_USAGE_PAGE, VID, KM16

KEYBOARD_IFACE = {
    "vendor_id": VID, "product_id": PID, "usage_page": 0x0001, "usage": 0x0006,
    "interface_number": 0, "path": b"kbd-path",
}
RAW_IFACE = {
    "vendor_id": VID, "product_id": PID, "usage_page": RAW_USAGE_PAGE, "usage": 0x0001,
    "interface_number": 1, "path": b"raw-path",
}


def test_raw_usage_page_matches_the_rawmacropad_descriptor():
    """0xFF00 per the firmware's report descriptor -- NOT QMK/VIA's 0xFF60, which the
    stock firmware exposes for the VIA configurator and which reads nothing useful."""
    assert RAW_USAGE_PAGE == 0xFF00


class FakeHandle:
    def __init__(self):
        self.opened_path = None

    def open_path(self, path):
        self.opened_path = path


def fake_hid(interfaces, opened):
    module = types.ModuleType("hid")
    module.enumerate = lambda vid=0, pid=0: [
        d for d in interfaces
        if (not vid or d["vendor_id"] == vid) and (not pid or d["product_id"] == pid)
    ]

    def device():
        handle = FakeHandle()
        opened.append(handle)
        return handle

    module.device = device
    return module


def test_open_picks_the_raw_interface_by_usage_page(monkeypatch):
    opened = []
    monkeypatch.setitem(
        sys.modules, "hid", fake_hid([KEYBOARD_IFACE, RAW_IFACE], opened)
    )
    device = KM16.open()
    assert opened[0].opened_path == b"raw-path", "the keyboard interface reads nothing"
    device._hid = None  # never let a test touch a real handle


def test_open_refuses_when_only_the_keyboard_interface_exists(monkeypatch):
    """Opening the wrong interface would fail silently; refusing loudly is the fix."""
    opened = []
    monkeypatch.setitem(sys.modules, "hid", fake_hid([KEYBOARD_IFACE], opened))
    with pytest.raises(OSError, match="RAW HID"):
        KM16.open()
    assert opened == [], "nothing may be opened when the raw interface is absent"
