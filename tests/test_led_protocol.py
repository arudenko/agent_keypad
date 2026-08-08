"""Protocol regression tests.

The headline case is `test_set_led_uses_0x06`: upstream RawMacroPad's client sends 0x04 here,
which the firmware reads as "set the whole chain", silently painting all 16 keys.
"""

import pytest

from herdr_km16 import km16


def test_set_led_uses_0x06_not_0x04():
    packet = km16.build_set_led(km16.CHAIN_KEYS, 5, 0x112233)
    assert packet[1] == 0x06, "must be 'set individual LED'; 0x04 is the upstream bug"
    assert packet[2] == km16.CHAIN_KEYS
    assert packet[3] == 5
    assert (packet[4], packet[5], packet[6]) == (0x11, 0x22, 0x33)


def test_set_led_is_distinguishable_from_chain_solid():
    """The two commands must not collide: that collision *is* the upstream bug."""
    single = km16.build_set_led(km16.CHAIN_KEYS, 0, 0xAABBCC)
    solid = km16.build_chain_solid(km16.CHAIN_KEYS, 0xAABBCC)
    assert single[1] != solid[1]


def test_packet_size_and_report_id():
    for packet in (
        km16.build_watchdog(2000),
        km16.build_set_led(0, 0, 0),
        km16.build_chain_array(km16.CHAIN_KEYS, [0] * 16),
        km16.build_reset(),
    ):
        assert len(packet) == 65
        assert packet[0] == 0x00, "leading report ID byte"


def test_watchdog_encodes_big_endian_interval():
    packet = km16.build_watchdog(2000)  # 0x07D0
    assert packet[1] == 0x01
    assert (packet[2], packet[3]) == (0x07, 0xD0)


def test_chain_array_lays_out_rgb_triples():
    colors = [0x010203, 0x040506] + [0] * 14
    packet = km16.build_chain_array(km16.CHAIN_KEYS, colors)
    assert packet[1] == 0x05
    assert packet[2] == km16.CHAIN_KEYS
    assert tuple(packet[3:9]) == (0x01, 0x02, 0x03, 0x04, 0x05, 0x06)


def test_chain_array_full_frame_fits_one_packet():
    packet = km16.build_chain_array(km16.CHAIN_KEYS, [0xFFFFFF] * 16)
    assert len(packet) == 65
    assert tuple(packet[3:51]) == (0xFF,) * 48


def test_chain_array_rejects_wrong_length():
    with pytest.raises(ValueError):
        km16.build_chain_array(km16.CHAIN_KEYS, [0] * 15)
    with pytest.raises(ValueError):
        km16.build_chain_array(km16.CHAIN_UNDERGLOW, [0] * 16)


def test_set_led_rejects_out_of_range_index():
    with pytest.raises(ValueError):
        km16.build_set_led(km16.CHAIN_KEYS, 16, 0)
    with pytest.raises(ValueError):
        km16.build_set_led(km16.CHAIN_UNDERGLOW, 6, 0)


def test_packets_do_not_share_state():
    """Upstream reuses one buffer; stale bytes leak between commands."""
    km16.build_chain_array(km16.CHAIN_KEYS, [0xFFFFFF] * 16)
    packet = km16.build_watchdog(1000)
    assert set(packet[4:]) == {0}, "a later short packet must not carry earlier LED bytes"


@pytest.mark.parametrize(
    "data,expected",
    [
        ([0x01, 3, 1], {"kind": "key", "key": 3, "pressed": True}),
        ([0x01, 16, 0], {"kind": "key", "key": 16, "pressed": False}),
        ([0x02, 0, 1], {"kind": "encoder", "encoder": 0, "delta": 1}),
        ([0x02, 2, 0xFF], {"kind": "encoder", "encoder": 2, "delta": -1}),
    ],
)
def test_parse_event(data, expected):
    assert km16.parse_event(data) == expected


def test_parse_event_ignores_unknown_and_empty():
    assert km16.parse_event([]) is None
    assert km16.parse_event([0x7F, 0, 0]) is None
