"""Recovery from a tripped firmware watchdog.

When the watchdog fires, km16.ino runs setEnableKeyLeds(false) and
setEnableUnderglow(false). Resuming pings clears the trip but never re-enables the chains,
so without explicit recovery the pad stays dark forever while input keeps working.
"""

import pytest

from herdr_km16 import km16
from herdr_km16.km16 import KM16, watchdog_gap_tripped


class FakeHid:
    def __init__(self):
        self.writes = []

    def write(self, packet):
        self.writes.append(bytes(packet))

    def close(self):
        pass


def commands(hid):
    return [p[1] for p in hid.writes]


@pytest.mark.parametrize(
    "gap_ms,interval_ms,expected",
    [
        (1000, 2000, False),   # normal: pings run at twice the timeout rate
        (1200, 2000, False),   # jitter
        (2000, 2000, True),    # exactly at the timeout
        (5000, 2000, True),    # a real stall
        (60000, 2000, True),   # machine suspended
        (1000, 0, False),      # watchdog disabled
    ],
)
def test_gap_detection(gap_ms, interval_ms, expected):
    assert watchdog_gap_tripped(gap_ms, interval_ms) is expected


def test_recovery_reenables_both_chains():
    hid = FakeHid()
    dev = KM16(hid)
    dev.power_on_leds()
    assert commands(hid) == [
        km16.CMD_ENABLE_ALL_LEDS,
        km16.CMD_ENABLE_CHAIN,
        km16.CMD_ENABLE_CHAIN,
    ]
    chains = [p[2] for p in hid.writes if p[1] == km16.CMD_ENABLE_CHAIN]
    assert chains == [km16.CHAIN_KEYS, km16.CHAIN_UNDERGLOW]
    assert all(p[3] == 1 for p in hid.writes if p[1] == km16.CMD_ENABLE_CHAIN)


def test_power_on_sends_master_before_chains():
    """Enabling a chain pushes its pixels immediately, so power must already be on."""
    hid = FakeHid()
    KM16(hid).power_on_leds()
    assert commands(hid)[0] == km16.CMD_ENABLE_ALL_LEDS


def test_recovery_forces_a_repaint():
    """A deduped no-op after recovery would leave the pad dark despite enabled chains."""
    hid = FakeHid()
    dev = KM16(hid)
    frame = [0x112233] * 16
    assert dev.set_frame(km16.CHAIN_KEYS, frame) is True
    assert dev.set_frame(km16.CHAIN_KEYS, frame) is False, "unchanged frames are skipped"

    dev._last_frame.clear()  # what the watchdog recovery does
    assert dev.set_frame(km16.CHAIN_KEYS, frame) is True, "must repaint after recovery"
