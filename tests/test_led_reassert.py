"""Unconditional re-assert of the LED chain enables.

Covers the failure that `test_watchdog_recovery` structurally cannot: the firmware watchdog
trips on pings not *arriving*, while km16._watchdog_loop measures whether we stopped
*sending*. Windows Modern Standby suspends USB with the process still running, so the gap
check sees nothing, no warning is logged, and the pad stays dark with working keys.

Observed 2026-08-12: standby 23:16-23:34, daemon logging throughout, clean log, dark pad.
Sending nothing but the three enable commands brought it straight back.
"""

import asyncio

import pytest

from herdr_km16 import km16
from herdr_km16.config import Config, load_config
from herdr_km16.km16 import KM16
from herdr_km16.main import Controller


class FakeHid:
    def __init__(self):
        self.writes = []

    def write(self, packet):
        self.writes.append(bytes(packet))

    def close(self):
        pass


def commands(hid):
    return [p[1] for p in hid.writes]


def run_loop_briefly(interval, seconds, device_present=True):
    """Run led_reassert_loop for a while and return the FakeHid it wrote to."""
    hid = FakeHid()

    async def go():
        config = Config()
        config.led_reassert_seconds = interval
        controller = Controller(config)
        controller.device = KM16(hid) if device_present else None
        task = asyncio.create_task(controller.led_reassert_loop())
        await asyncio.sleep(seconds)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(go())
    return hid


def test_reassert_repeats_while_nothing_is_wrong():
    """It must fire on a healthy pad too -- there is no signal to condition it on."""
    hid = run_loop_briefly(interval=0.01, seconds=0.06)
    enables = [c for c in commands(hid) if c == km16.CMD_ENABLE_ALL_LEDS]
    assert len(enables) >= 2, "must keep re-asserting, not just run once at startup"


def test_reassert_sends_master_power_and_both_chains():
    hid = run_loop_briefly(interval=0.01, seconds=0.03)
    assert commands(hid)[:3] == [
        km16.CMD_ENABLE_ALL_LEDS,
        km16.CMD_ENABLE_CHAIN,
        km16.CMD_ENABLE_CHAIN,
    ]
    chains = [p[2] for p in hid.writes if p[1] == km16.CMD_ENABLE_CHAIN][:2]
    assert chains == [km16.CHAIN_KEYS, km16.CHAIN_UNDERGLOW]
    assert all(p[3] == 1 for p in hid.writes if p[1] == km16.CMD_ENABLE_CHAIN)


def test_reassert_paints_nothing():
    """Enabling a chain re-pushes the pixels the firmware already holds, so a repaint would
    only add HID traffic -- and risk disturbing a pad that is working fine."""
    hid = run_loop_briefly(interval=0.01, seconds=0.06)
    assert km16.CMD_CHAIN_ARRAY not in commands(hid)
    assert km16.CMD_CHAIN_SOLID not in commands(hid)


def test_reassert_tolerates_no_device():
    """The daemon runs with the keypad absent; this loop must not be what crashes it."""
    hid = run_loop_briefly(interval=0.01, seconds=0.03, device_present=False)
    assert hid.writes == []


def test_zero_disables_the_loop():
    hid = FakeHid()

    async def go():
        config = Config()
        config.led_reassert_seconds = 0
        controller = Controller(config)
        controller.device = KM16(hid)
        await asyncio.wait_for(controller.led_reassert_loop(), timeout=1.0)

    asyncio.run(go())  # returns immediately rather than hanging
    assert hid.writes == []


def test_default_is_on():
    """Shipping this off by default would leave the documented failure unfixed."""
    assert Config().led_reassert_seconds > 0


def test_config_reads_the_interval(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("km16:\n  led_reassert_seconds: 12\n", encoding="utf-8")
    assert load_config(path).led_reassert_seconds == 12


def test_negative_reassert_is_rejected(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("km16:\n  led_reassert_seconds: -1\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_config(path)
