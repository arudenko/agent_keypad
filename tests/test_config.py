from pathlib import Path

import pytest
import yaml

from herdr_km16.config import load_config


def write(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    return p


def test_defaults_when_file_absent(tmp_path):
    cfg = load_config(tmp_path / "nope.yaml")
    assert cfg.poll_seconds == 1.5
    assert cfg.key_press == "focus_agent"


def test_poll_seconds_is_configurable(tmp_path):
    cfg = load_config(write(tmp_path, {"herdr": {"poll_seconds": 0.5}}))
    assert cfg.poll_seconds == 0.5


def test_poll_seconds_must_be_positive(tmp_path):
    with pytest.raises(ValueError, match="poll_seconds"):
        load_config(write(tmp_path, {"herdr": {"poll_seconds": 0}}))


def test_rejects_unknown_colour_state(tmp_path):
    with pytest.raises(ValueError, match="colors.purple"):
        load_config(write(tmp_path, {"colors": {"purple": "#ff00ff"}}))


def test_rejects_out_of_range_brightness(tmp_path):
    with pytest.raises(ValueError, match="brightness"):
        load_config(write(tmp_path, {"km16": {"brightness": 2.0}}))


def test_rejects_unknown_encoder_action(tmp_path):
    with pytest.raises(ValueError, match="main_encoder.press"):
        load_config(write(tmp_path, {"controls": {"main_encoder": {"press": "launch_missiles"}}}))


def test_rejects_static_slot_out_of_range(tmp_path):
    with pytest.raises(ValueError, match="slot 99"):
        load_config(write(tmp_path, {"mapping": {"static": {99: "reviewer"}}}))


def test_shipped_config_yaml_is_valid():
    """The committed config must always load; it is the documented starting point."""
    cfg = load_config(Path(__file__).resolve().parent.parent / "config.yaml")
    assert cfg.poll_seconds > 0
    assert "enter" in cfg.require_long_press_for
