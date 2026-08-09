"""Daemon entry point.

Structure:

* one task owns the Herdr event stream and keeps the agent cache fresh
* one task owns the KM16 (HID reads, watchdog pings)
* one task renders LED frames

Both connections are optional at any moment: the daemon starts with neither Herdr nor the
keypad present and reconnects rather than exiting.

Because ``pane.agent_status_changed`` must be subscribed per pane and a subscribed connection
cannot accept further requests, the event stream is torn down and reopened whenever the set of
agent panes changes.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .actions import ActionRouter
from .config import Config, load_config
from .herdr import HerdrClient, HerdrError, HerdrEventStream, event_kind
from .km16 import CHAIN_KEYS, CHAIN_UNDERGLOW, KM16
from .leds import LedRenderer
from .mapping import Agent, SlotMap

log = logging.getLogger("herdr_km16")

ANIMATION_HZ = 8

# Events that change which panes exist, so the subscription set must be rebuilt.
TOPOLOGY_EVENTS = {"pane_created", "pane_closed", "pane_exited", "pane_agent_detected", "pane_moved"}


def _agent_from_record(record: dict) -> Agent:
    return Agent(
        pane_id=record["pane_id"],
        status=record.get("agent_status", "unknown"),
        name=record.get("name"),
        cwd=record.get("cwd"),
        title=record.get("terminal_title_stripped"),
        terminal_id=record.get("terminal_id"),
    )


class Controller:
    def __init__(self, config: Config):
        self.config = config
        self.client = HerdrClient()
        self.slots = SlotMap(
            static=config.static,
            preserve_slots=config.preserve_slots,
            action_slots=frozenset(config.action_keys),
        )
        self.renderer = LedRenderer(
            colors=config.colors,
            brightness=config.brightness,
            underglow=config.underglow,
            pulse=config.pulse,
            action_keys=config.action_keys,
            action_colors=config.action_colors,
        )
        self.router = ActionRouter(
            config=config, client=self.client, slots=self.slots, renderer=self.renderer
        )
        self.device: KM16 | None = None
        self.dirty = asyncio.Event()
        self._input_queue: asyncio.Queue[dict] = asyncio.Queue()

    # --- Herdr ------------------------------------------------------------

    async def _reconcile(self) -> list[str]:
        records = await self.client.list_agents()
        self.slots.sync([_agent_from_record(r) for r in records])
        self.dirty.set()
        return [r["pane_id"] for r in records]

    async def herdr_loop(self) -> None:
        while True:
            try:
                panes = await self._reconcile()
                log.info("tracking %d agent pane(s): %s", len(panes), ", ".join(panes) or "none")
                stream = HerdrEventStream(panes)
                async for event in stream:
                    kind = event_kind(event)
                    data = event.get("data", {})
                    if kind == "pane_agent_status_changed":
                        slot = self.slots.update_status(data["pane_id"], data["agent_status"])
                        if slot is not None:
                            log.info("slot %s -> %s", slot, data["agent_status"])
                            self.dirty.set()
                            continue
                        # Unknown pane: cache is stale, fall through and resync.
                    elif kind not in TOPOLOGY_EVENTS:
                        continue

                    # Topology may have moved. Resync over a separate RPC connection (the
                    # subscribed one cannot carry requests) and only rebuild the stream if
                    # the pane set actually changed -- Herdr replays pane_agent_detected for
                    # every existing pane right after subscribing, and treating those as
                    # changes would resubscribe in a tight loop.
                    current = await self._reconcile()
                    if set(current) != set(panes):
                        log.info("pane set changed; resubscribing")
                        break
            except (HerdrError, OSError, FileNotFoundError) as exc:
                log.warning("herdr unavailable (%s); retrying in %ss", exc, self.config.reconnect_seconds)
                if self.slots.live_agents():
                    self.slots.sync([])
                    self.dirty.set()
            except asyncio.CancelledError:
                raise
            except Exception:
                # Anything else -- a malformed event, an unexpected envelope shape, a
                # missing field -- must not take the daemon down. Herdr's event shapes have
                # already varied once (see herdr.event_kind), so assume they will again.
                log.exception("unexpected error in the event loop; resubscribing")
            await asyncio.sleep(self.config.reconnect_seconds)

    async def reconcile_loop(self) -> None:
        """Backstop resync behind the event stream.

        Events are the primary path (see herdr.event_kind for the naming trap that had them
        silently dropped). This catches what events cannot: a dropped subscription, or a
        pane that appeared before we resubscribed. agent.list is 0.69 ms median locally, so
        the interval is cheap either way.
        """
        while True:
            await asyncio.sleep(self.config.poll_seconds)
            with contextlib.suppress(HerdrError, OSError):
                await self._reconcile()

    # --- device -----------------------------------------------------------

    @staticmethod
    async def _device_present() -> bool:
        """hid.enumerate() blocks; on the event loop it can stall the watchdog ping past
        the firmware timeout, which disables the LED chains."""
        return await asyncio.get_running_loop().run_in_executor(None, KM16.is_present)

    async def device_loop(self) -> None:
        warned = None
        while True:
            if not await self._device_present():
                # Log each distinct situation once; this loop polls every 2s.
                reason = "stock" if await asyncio.get_running_loop().run_in_executor(
                    None, KM16.stock_firmware_present
                ) else "absent"
                if reason != warned:
                    warned = reason
                    if reason == "stock":
                        log.warning("KM16 found on STOCK firmware - flash RawMacroPad first (see README)")
                    else:
                        log.info("waiting for a KM16 on RAW HID 1209:88BF")
                await asyncio.sleep(2.0)
                continue
            warned = None
            try:
                device = KM16.open()
            except Exception as exc:  # hidapi raises assorted OS errors
                log.warning("cannot open KM16: %s", exc)
                await asyncio.sleep(2.0)
                continue

            log.info("KM16 connected")
            self.device = device
            device.on_event(self._input_queue.put_nowait)
            device.power_on_leds()
            device.set_watchdog(self.config.watchdog_ms)
            device.start_reading()
            self.dirty.set()
            try:
                while await self._device_present():
                    await asyncio.sleep(1.0)
                log.warning("KM16 disconnected")
            finally:
                self.device = None
                with contextlib.suppress(Exception):
                    device.close()

    async def input_loop(self) -> None:
        while True:
            event = await self._input_queue.get()
            try:
                if event["kind"] == "key":
                    await self.router.handle_key(event["key"], event["pressed"])
                else:
                    await self.router.handle_encoder(event["encoder"], event["delta"])
            except Exception:
                log.exception("error handling input %s", event)
            self.dirty.set()

    # --- rendering --------------------------------------------------------

    async def render_loop(self) -> None:
        start = time.monotonic()
        while True:
            animating = self.renderer.wants_animation(self.slots)
            if animating:
                await asyncio.sleep(1 / ANIMATION_HZ)
            else:
                # Purely event-driven while nothing pulses: no needless HID traffic.
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self.dirty.wait(), timeout=1.0)
            self.dirty.clear()

            device = self.device
            if device is None:
                continue
            phase = (time.monotonic() - start) if animating else 0.0
            try:
                device.set_frame(CHAIN_KEYS, self.renderer.key_frame(self.slots, self.router.selected, phase))
                device.set_frame(CHAIN_UNDERGLOW, self.renderer.underglow_frame(self.slots, phase))
            except Exception as exc:
                log.warning("LED write failed: %s", exc)

    async def run(self) -> None:
        tasks = [
            asyncio.create_task(coro)
            for coro in (
                self.herdr_loop(),
                self.reconcile_loop(),
                self.device_loop(),
                self.input_loop(),
                self.render_loop(),
            )
        ]
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            if self.device is not None:
                with contextlib.suppress(Exception):
                    self.device.set_frame(CHAIN_KEYS, [0] * 16, force=True)
                    self.device.set_watchdog(0)
                    self.device.close()


def _resolve_config(explicit: Path | None) -> Path | None:
    """Find config.yaml, or fail loudly.

    Running from the wrong directory must not silently fall back to built-in defaults --
    the whole point of the file is that behaviour is configurable without touching source.
    """
    if explicit is not None:
        if not explicit.exists():
            raise SystemExit(f"config file not found: {explicit}")
        return explicit
    for candidate in (Path.cwd() / "config.yaml", Path(__file__).resolve().parents[2] / "config.yaml"):
        if candidate.exists():
            return candidate
    return None  # genuinely no config anywhere: defaults are the intended behaviour


def default_log_path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/.local/state")
    return Path(base) / "herdr-km16" / "herdr-km16.log"


def setup_logging(verbose: bool, log_file: Path | None) -> Path | None:
    """Log to stdout and, unless disabled, to a rotating file.

    Running windowless (the documented way to start at logon) throws stdout away, so
    without a file there is no evidence at all after a failure -- which is exactly the
    situation that made the LED blackout hard to diagnose.
    """
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file is not None:
        try:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            handlers.append(
                RotatingFileHandler(log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
            )
        except OSError as exc:  # never let logging setup stop the daemon
            print(f"warning: cannot write log file {log_file}: {exc}")
            log_file = None

    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=handlers,
    )
    return log_file


def run() -> None:
    parser = argparse.ArgumentParser(prog="herdr-km16", description="KM16 -> Herdr agent controller")
    parser.add_argument("-c", "--config", default=None, type=Path,
                        help="path to config.yaml (default: ./config.yaml, else the repo copy)")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--log-file", type=Path, default=None,
                        help=f"rotating log file (default: {default_log_path()})")
    parser.add_argument("--no-log-file", action="store_true", help="log to stdout only")
    args = parser.parse_args()

    log_file = None if args.no_log_file else (args.log_file or default_log_path())
    log_file = setup_logging(args.verbose, log_file)
    if log_file:
        log.info("logging to %s", log_file)
    config_path = _resolve_config(args.config)
    log.info("config: %s", config_path or "(built-in defaults)")
    config = load_config(config_path)
    try:
        asyncio.run(Controller(config).run())
    except KeyboardInterrupt:
        log.info("shutting down")


if __name__ == "__main__":
    run()
