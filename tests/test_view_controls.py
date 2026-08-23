"""View controls on the knobs: font size, scratch terminal, surface zoom.

These change what the user sees, never what an agent receives, so unlike enter or
interrupt they are deliberately NOT long-press gated.
"""

import asyncio
from pathlib import Path

from herdr_km16.actions import ActionRouter
from herdr_km16.config import Config, load_config
from herdr_km16.km16 import KEY_LEFT_ENCODER, KEY_MAIN_ENCODER
from herdr_km16.mapping import Agent, SlotMap


class FakeClient:
    def __init__(self):
        self.font_steps: list[tuple[str | None, int]] = []
        self.scratch_toggles: list[str] = []
        self.zoom_toggles: int = 0
        self.focused: list[str] = []

    async def font_step(self, target, delta):
        self.font_steps.append((target, delta))

    async def scratch_toggle(self, target):
        self.scratch_toggles.append(target)

    async def zoom_toggle(self):
        self.zoom_toggles += 1

    async def focus_agent(self, target):
        self.focused.append(target)

    async def activate_app(self):
        pass


def make(**kw):
    cfg = Config(**kw)
    slots = SlotMap()
    slots.sync([Agent("w1:p1", "working"), Agent("w2:p1", "idle")])
    client = FakeClient()
    return ActionRouter(config=cfg, client=client, slots=slots), client


def press(router, key, held_ms=50):
    router._last_press.pop(key, None)
    asyncio.run(router.handle_key(key, True))
    router._press_started[key] -= held_ms / 1000
    asyncio.run(router.handle_key(key, False))


# --- font size (left knob turn) ---------------------------------------------


def test_left_knob_steps_the_selected_sessions_font():
    router, client = make()
    router.selected = "w2:p1"
    asyncio.run(router.handle_encoder(KEY_LEFT_ENCODER, 1))
    asyncio.run(router.handle_encoder(KEY_LEFT_ENCODER, -1))
    assert client.font_steps == [("w2:p1", 1), ("w2:p1", -1)]


def test_font_knob_falls_back_to_the_active_session():
    """No selection must not mean a dead knob -- agterm resolves None to `active`."""
    router, client = make()
    asyncio.run(router.handle_encoder(KEY_LEFT_ENCODER, 1))
    assert client.font_steps == [(None, 1)]


def test_font_knob_does_not_move_the_selection():
    router, client = make()
    asyncio.run(router.handle_encoder(KEY_LEFT_ENCODER, 1))
    assert router.selected is None
    assert client.focused == []


# --- scratch (main knob press) ----------------------------------------------


def test_main_knob_press_toggles_the_selected_scratch():
    router, client = make()
    router.selected = "w1:p1"
    press(router, KEY_MAIN_ENCODER)
    assert client.scratch_toggles == ["w1:p1"]


def test_scratch_press_without_a_selection_does_nothing():
    router, client = make()
    press(router, KEY_MAIN_ENCODER)
    assert client.scratch_toggles == []


def test_scratch_is_not_long_press_gated():
    router, client = make()
    router.selected = "w1:p1"
    press(router, KEY_MAIN_ENCODER, held_ms=10)
    assert client.scratch_toggles == ["w1:p1"], "a view toggle needs no guard"


# --- zoom (left knob press) --------------------------------------------------


def test_left_knob_press_toggles_zoom():
    router, client = make()
    press(router, KEY_LEFT_ENCODER, held_ms=10)
    assert client.zoom_toggles == 1


def test_zoom_needs_no_selection():
    router, client = make()
    router.selected = None
    press(router, KEY_LEFT_ENCODER)
    assert client.zoom_toggles == 1, "zoom acts on the active surface, not the selection"


# --- resilience ---------------------------------------------------------------


def test_view_controls_survive_an_agterm_outage(caplog):
    import logging

    class DeadClient(FakeClient):
        async def font_step(self, target, delta):
            raise ConnectionRefusedError("agterm is down")

        async def scratch_toggle(self, target):
            raise ConnectionRefusedError("agterm is down")

        async def zoom_toggle(self):
            raise ConnectionRefusedError("agterm is down")

    router, _ = make()
    router.client = DeadClient()
    router.selected = "w1:p1"
    with caplog.at_level(logging.DEBUG):
        asyncio.run(router.handle_encoder(KEY_LEFT_ENCODER, 1))
        press(router, KEY_MAIN_ENCODER)
        press(router, KEY_LEFT_ENCODER)
    assert sum("failed" in r.message for r in caplog.records) == 3


# --- config -------------------------------------------------------------------


def test_defaults_bind_the_view_controls():
    cfg = Config()
    assert cfg.main_encoder.press == "scratch"
    assert cfg.left_encoder.rotate == "font_size"
    assert cfg.left_encoder.press == "zoom"


def test_shipped_config_matches_the_defaults():
    cfg = load_config(Path(__file__).resolve().parent.parent / "config.yaml")
    assert cfg.main_encoder.press == "scratch"
    assert cfg.left_encoder.rotate == "font_size"
    assert cfg.left_encoder.press == "zoom"
    for action in ("scratch", "zoom", "font_size"):
        assert action not in cfg.require_long_press_for
