"""Guard the README's reference tables against drift.

The colour and mapping tables are the thing a user reads instead of the source, so a default
that changes without the docs changing is a real defect.
"""

import re
from pathlib import Path

import pytest

from herdr_km16.actions import DEBOUNCE_SECONDS
from herdr_km16.config import load_config
from herdr_km16.km16 import KEY_COUNT, KEY_LEFT_ENCODER, KEY_MAIN_ENCODER, KEY_RIGHT_ENCODER
from herdr_km16.leds import DEFAULT_COLORS, PULSE_DEPTH
from herdr_km16.mapping import ATTENTION_ORDER

README = (Path(__file__).resolve().parent.parent / "README.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("state,value", sorted(DEFAULT_COLORS.items()))
def test_readme_lists_each_default_colour(state, value):
    assert f"`{state}`" in README, f"README does not mention state {state}"
    assert f"#{value:06x}" in README.lower(), f"README missing default colour for {state}"


def test_readme_documents_pulse_depths():
    for state, depth in PULSE_DEPTH.items():
        assert f"{round(depth * 100)}%" in README, f"pulse depth for {state} not documented"


def test_readme_documents_attention_priority():
    order = re.search(r"Attention priority is (.+?), so", README, re.S)
    assert order, "attention priority sentence missing"
    found = re.findall(r"`(\w+)`", order.group(1))
    assert tuple(found) == ATTENTION_ORDER


def test_readme_documents_encoder_indices():
    for index in (KEY_MAIN_ENCODER, KEY_LEFT_ENCODER, KEY_RIGHT_ENCODER):
        assert f"| {index} |" in README, f"encoder index {index} not in the table"


def test_readme_key_grid_covers_every_slot():
    """The grid must name all 16 keys, whatever cell width it is drawn with."""
    grids = [b for b in re.findall(r"```(.*?)```", README, re.S) if "|" in b and "0" in b]
    matching = [
        g for g in grids
        if sorted({int(n) for n in re.findall(r"\b(\d{1,2})\b", g)}) == list(range(KEY_COUNT))
    ]
    assert matching, "no fenced block draws all 16 keys exactly once"


def test_readme_documents_every_action_key_binding():
    from herdr_km16.config import load_config

    cfg = load_config(Path(__file__).resolve().parent.parent / "config.yaml")
    for slot, action in cfg.action_keys.items():
        assert f"| {slot} |" in README, f"action key {slot} missing from the table"
        assert action.split("_")[0] in README.lower(), f"action {action} not described"


def _numbers(text: str) -> set[str]:
    """Both spellings of a float, so '5' and '5.0' compare equal in prose."""
    return {text, text.rstrip("0").rstrip(".")}


def test_readme_matches_shipped_config():
    cfg = load_config(Path(__file__).resolve().parent.parent / "config.yaml")
    assert f"default `{cfg.brightness}`" in README
    assert f"{cfg.long_press_ms} ms long press" in README
    assert any(f"default {n} s" in README for n in _numbers(str(cfg.poll_seconds))), (
        f"README does not state the {cfg.poll_seconds}s poll interval"
    )
    assert any(f"({n})" in README for n in _numbers(str(cfg.brightness_step))), (
        f"README does not state the {cfg.brightness_step} brightness step"
    )


def test_readme_documents_debounce():
    assert f"debounced at {round(DEBOUNCE_SECONDS * 1000)} ms" in README
