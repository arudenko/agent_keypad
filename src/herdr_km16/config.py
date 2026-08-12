"""Config loading and validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .action_types import DEFAULT_ACTION_COLORS, VALID_ACTION_KEYS
from .leds import DEFAULT_COLORS, parse_color

VALID_KEY_ACTIONS = {"focus_agent", "none"}
VALID_ROTATE_ACTIONS = {"cycle_attention_agents", "cycle_agents", "brightness", "none"}
VALID_PRESS_ACTIONS = {"focus_selected", "escape", "enter", "interrupt", "none"}



@dataclass
class EncoderConfig:
    rotate: str = "none"
    press: str = "none"
    # Focus the agent as soon as the knob lands on it, instead of needing a press.
    focus_on_turn: bool = False


@dataclass
class Config:
    session: str | None = None
    reconnect_seconds: float = 1.0
    poll_seconds: float = 1.5
    watchdog_ms: int = 2000
    led_reassert_seconds: float = 5.0
    brightness: float = 0.35
    brightness_step: float = 0.05
    underglow: bool = True
    pulse: bool = True
    colors: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_COLORS))
    preserve_slots: bool = True
    static: dict[int, str] = field(default_factory=dict)
    key_press: str = "focus_agent"
    main_encoder: EncoderConfig = field(
        default_factory=lambda: EncoderConfig("cycle_attention_agents", "focus_selected", focus_on_turn=True)
    )
    left_encoder: EncoderConfig = field(default_factory=lambda: EncoderConfig("cycle_agents", "escape"))
    right_encoder: EncoderConfig = field(default_factory=lambda: EncoderConfig("brightness", "enter"))
    action_keys: dict[int, str] = field(default_factory=dict)
    action_colors: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_ACTION_COLORS))
    long_press_ms: int = 300
    require_long_press_for: list[str] = field(
        default_factory=lambda: ["approve", "enter", "interrupt"]
    )


def _encoder(raw: dict[str, Any] | None, default: EncoderConfig, where: str) -> EncoderConfig:
    if not raw:
        return default
    rotate = raw.get("rotate", default.rotate)
    press = raw.get("press", default.press)
    if rotate not in VALID_ROTATE_ACTIONS:
        raise ValueError(f"{where}.rotate: unknown action {rotate!r} (expected one of {sorted(VALID_ROTATE_ACTIONS)})")
    if press not in VALID_PRESS_ACTIONS:
        raise ValueError(f"{where}.press: unknown action {press!r} (expected one of {sorted(VALID_PRESS_ACTIONS)})")
    focus_on_turn = bool(raw.get("focus_on_turn", default.focus_on_turn))
    if focus_on_turn and rotate not in {"cycle_attention_agents", "cycle_agents"}:
        raise ValueError(f"{where}.focus_on_turn needs a rotate action to focus onto")
    return EncoderConfig(rotate=rotate, press=press, focus_on_turn=focus_on_turn)


def load_config(path: str | Path | None = None) -> Config:
    """Load and validate config.yaml. Invalid config is a hard error, never a silent default."""
    cfg = Config()
    if path is None:
        return cfg
    path = Path(path)
    if not path.exists():
        return cfg

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    herdr = raw.get("herdr") or {}
    cfg.session = herdr.get("session")
    cfg.reconnect_seconds = float(herdr.get("reconnect_seconds", cfg.reconnect_seconds))
    cfg.poll_seconds = float(herdr.get("poll_seconds", cfg.poll_seconds))
    if cfg.poll_seconds <= 0:
        raise ValueError(f"herdr.poll_seconds must be positive, got {cfg.poll_seconds}")

    km16 = raw.get("km16") or {}
    cfg.watchdog_ms = int(km16.get("watchdog_ms", cfg.watchdog_ms))
    cfg.led_reassert_seconds = float(km16.get("led_reassert_seconds", cfg.led_reassert_seconds))
    cfg.brightness = float(km16.get("brightness", cfg.brightness))
    cfg.brightness_step = float(km16.get("brightness_step", cfg.brightness_step))
    cfg.underglow = bool(km16.get("underglow", cfg.underglow))
    cfg.pulse = bool(km16.get("pulse", cfg.pulse))
    if not 0.0 <= cfg.brightness <= 1.0:
        raise ValueError(f"km16.brightness must be within 0..1, got {cfg.brightness}")
    if not 0.0 < cfg.brightness_step <= 0.5:
        raise ValueError(f"km16.brightness_step must be within 0..0.5, got {cfg.brightness_step}")
    if cfg.watchdog_ms and cfg.watchdog_ms < 100:
        raise ValueError(f"km16.watchdog_ms too small to ping reliably: {cfg.watchdog_ms}")
    if cfg.led_reassert_seconds < 0:
        raise ValueError(
            f"km16.led_reassert_seconds cannot be negative, got {cfg.led_reassert_seconds}"
        )

    for name, value in (raw.get("colors") or {}).items():
        if name not in DEFAULT_COLORS:
            raise ValueError(f"colors.{name}: unknown state (expected one of {sorted(DEFAULT_COLORS)})")
        cfg.colors[name] = parse_color(value)

    mapping = raw.get("mapping") or {}
    cfg.preserve_slots = bool(mapping.get("preserve_slots", cfg.preserve_slots))
    for slot, identity in (mapping.get("static") or {}).items():
        slot = int(slot)
        if not 0 <= slot < 16:
            raise ValueError(f"mapping.static: slot {slot} out of range 0..15")
        cfg.static[slot] = identity

    controls = raw.get("controls") or {}
    cfg.key_press = controls.get("key_press", cfg.key_press)
    if cfg.key_press not in VALID_KEY_ACTIONS:
        raise ValueError(f"controls.key_press: unknown action {cfg.key_press!r}")
    cfg.main_encoder = _encoder(controls.get("main_encoder"), cfg.main_encoder, "controls.main_encoder")
    cfg.left_encoder = _encoder(controls.get("left_encoder"), cfg.left_encoder, "controls.left_encoder")
    cfg.right_encoder = _encoder(controls.get("right_encoder"), cfg.right_encoder, "controls.right_encoder")

    for slot, action in (raw.get("action_keys") or {}).items():
        slot = int(slot)
        if not 0 <= slot < 16:
            raise ValueError(f"action_keys: slot {slot} out of range 0..15")
        if action not in VALID_ACTION_KEYS:
            raise ValueError(
                f"action_keys.{slot}: unknown action {action!r} "
                f"(expected one of {sorted(VALID_ACTION_KEYS)})"
            )
        if action != "none":
            cfg.action_keys[slot] = action
    # A key cannot be both an action and a pinned agent slot.
    clash = set(cfg.action_keys) & set(cfg.static)
    if clash:
        raise ValueError(f"slots {sorted(clash)} are both action_keys and mapping.static")

    for name, value in (raw.get("action_colors") or {}).items():
        if name not in DEFAULT_ACTION_COLORS:
            raise ValueError(
                f"action_colors.{name}: unknown action "
                f"(expected one of {sorted(DEFAULT_ACTION_COLORS)})"
            )
        cfg.action_colors[name] = parse_color(value)

    safety = raw.get("safety") or {}
    cfg.long_press_ms = int(safety.get("long_press_ms", cfg.long_press_ms))
    if "require_long_press_for" in safety:
        cfg.require_long_press_for = list(safety["require_long_press_for"])

    return cfg
