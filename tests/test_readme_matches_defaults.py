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
    grid = re.search(r"\|  0  \|.*?\| 15  \|", README, re.S)
    assert grid, "4x4 key grid missing"
    numbers = [int(n) for n in re.findall(r"\b(\d+)\b", grid.group(0))]
    assert sorted(numbers) == list(range(KEY_COUNT))


def test_readme_matches_shipped_config():
    cfg = load_config(Path(__file__).resolve().parent.parent / "config.yaml")
    assert f"default `{cfg.brightness}`" in README
    assert f"{cfg.long_press_ms} ms long press" in README
    assert f"default {cfg.poll_seconds} s" in README


def test_readme_documents_debounce():
    assert f"debounced at {round(DEBOUNCE_SECONDS * 1000)} ms" in README
