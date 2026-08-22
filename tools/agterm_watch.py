"""Live ANSI mock of the pad, driven by the real agterm socket -- the no-hardware proof.

    .venv/bin/python tools/agterm_watch.py

Lists sessions, shows the slot map and the LED frame that *would* be pushed, then follows
the event cursor exactly like the daemon: a status event repaints its cell, a topology
event resyncs from `tree`. Leave it running while agents work: with the agterm status
hooks installed, cells track active/blocked/completed within the poll interval.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from herdr_km16.agterm import (  # noqa: E402
    TOPOLOGY_KINDS,
    AgtermClient,
    AgtermError,
    AgtermEventStream,
    map_status,
)
from herdr_km16.config import load_config  # noqa: E402
from herdr_km16.leds import LedRenderer  # noqa: E402
from herdr_km16.main import _agent_from_record, _resolve_config  # noqa: E402
from herdr_km16.mapping import SlotMap  # noqa: E402

SWATCH = {
    "working": "\x1b[44m",
    "blocked": "\x1b[41m",
    "done": "\x1b[42m",
    "idle": "\x1b[47;30m",
    "unknown": "\x1b[43m",
}
RESET = "\x1b[0m"


def render_pad(slots: SlotMap, action_keys: dict[int, str]) -> str:
    """A 4x4 text mock of the pad, so you can sanity-check colours without hardware."""
    lines = []
    for row in range(4):
        cells = []
        for col in range(4):
            key = row * 4 + col
            action = action_keys.get(key)
            if action:
                cells.append(f"[{action[:6]:6}]")
                continue
            agent = slots.agent_at(key)
            if agent is None:
                cells.append("  ....  ")
            else:
                cells.append(f"{SWATCH.get(agent.status, '')} {agent.status[:6]:6} {RESET}")
        lines.append(" ".join(cells))
    return "\n".join(lines)


async def resync(client: AgtermClient, slots: SlotMap) -> None:
    records = await client.list_agents()
    slots.sync([_agent_from_record(r) for r in records])


async def main() -> int:
    client = AgtermClient()
    print(f"socket: {client.path}")

    # Mirror the daemon exactly: same config, same slot map, same renderer inputs --
    # otherwise the preview allocates sessions onto the action row and the advertised
    # frame differs from what the daemon would push.
    config = load_config(_resolve_config(None))
    slots = SlotMap(
        static=config.static,
        preserve_slots=config.preserve_slots,
        action_slots=frozenset(config.action_keys),
        compact=config.compact,
    )
    renderer = LedRenderer(
        colors=config.colors,
        brightness=config.brightness,
        underglow=config.underglow,
        pulse=False,  # a static preview cannot show animation
        action_keys=config.action_keys,
        action_colors=config.action_colors,
    )
    await resync(client, slots)

    print(f"\n{len(slots.live_agents())} session(s):")
    for slot in range(16):
        agent = slots.agent_at(slot)
        if agent:
            print(f"  key {slot:2}  {agent.status:8}  {agent.pane_id[:8]}  {agent.title or agent.cwd}")

    print("\npad preview:")
    print(render_pad(slots, config.action_keys))

    frame = renderer.key_frame(slots)
    print("\nLED frame that would be pushed (chain 0):")
    print("  " + " ".join(f"{c:06x}" for c in frame[:8]))
    print("  " + " ".join(f"{c:06x}" for c in frame[8:]))

    print("\nfollowing the event cursor. Ctrl+C to stop.\n")
    while True:
        try:
            async for event in AgtermEventStream():
                kind = event.get("kind")
                if kind == "status":
                    session = event.get("session")
                    status = map_status(event.get("payload", {}).get("status"))
                    slot = slots.update_status(session, status) if session else None
                    if slot is None:
                        await resync(client, slots)
                    print(f"STATUS  {(session or '?')[:8]} -> {status:8} (key {slot})")
                    print(render_pad(slots, config.action_keys))
                elif kind in TOPOLOGY_KINDS:
                    await resync(client, slots)
                    print(f"{kind:16} resynced: {len(slots.live_agents())} session(s)")
                    print(render_pad(slots, config.action_keys))
        except (AgtermError, OSError) as exc:
            print(f"agterm unavailable ({exc}); retrying in 1s")
            await asyncio.sleep(1.0)


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        pass
