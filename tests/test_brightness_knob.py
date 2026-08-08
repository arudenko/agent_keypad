"""Right encoder = LED brightness."""

import asyncio

from herdr_km16.actions import BRIGHTNESS_MAX, BRIGHTNESS_MIN, ActionRouter
from herdr_km16.config import Config, load_config
from herdr_km16.km16 import KEY_RIGHT_ENCODER
from herdr_km16.leds import LedRenderer, scale
from herdr_km16.mapping import Agent, SlotMap
from pathlib import Path


class FakeClient:
    async def focus_agent(self, target):
        raise AssertionError("brightness must not touch Herdr")

    async def send_keys(self, target, keys):
        raise AssertionError("brightness must not touch Herdr")


def make(brightness=0.35):
    cfg = Config(brightness=brightness)
    slots = SlotMap()
    slots.sync([Agent("w1:p1", "idle")])
    renderer = LedRenderer(pulse=False, brightness=brightness)
    return ActionRouter(config=cfg, client=FakeClient(), slots=slots, renderer=renderer), renderer


def turn(router, delta, times=1):
    async def go():
        for _ in range(times):
            await router.handle_encoder(KEY_RIGHT_ENCODER, delta)
    asyncio.run(go())


def test_turning_right_brightens():
    router, renderer = make(0.35)
    turn(router, +1)
    assert renderer.brightness == 0.40


def test_turning_left_dims():
    router, renderer = make(0.35)
    turn(router, -1)
    assert renderer.brightness == 0.30


def test_brightness_never_reaches_zero():
    router, renderer = make(0.35)
    turn(router, -1, times=50)
    assert renderer.brightness == BRIGHTNESS_MIN
    assert renderer.brightness > 0, "a pad that can go fully dark looks broken"


def test_brightness_clamps_at_full():
    router, renderer = make(0.35)
    turn(router, +1, times=50)
    assert renderer.brightness == BRIGHTNESS_MAX


def test_brightness_actually_changes_the_frame():
    router, renderer = make(0.35)
    before = renderer.key_frame(router.slots)[0]
    turn(router, +1, times=4)
    after = renderer.key_frame(router.slots)[0]
    assert after != before
    assert after == scale(renderer.colors["idle"], renderer.brightness)


def test_brightness_knob_does_not_move_the_selection():
    router, _ = make()
    turn(router, +1, times=3)
    assert router.selected is None, "the brightness knob is not a navigation knob"


def test_shipped_config_puts_brightness_on_the_right_knob():
    cfg = load_config(Path(__file__).resolve().parent.parent / "config.yaml")
    assert cfg.right_encoder.rotate == "brightness"
    assert cfg.main_encoder.rotate == "cycle_attention_agents"
