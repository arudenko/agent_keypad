r"""Correlate real status transitions against event delivery.

    .\.venv\Scripts\python.exe tools\diag_event_vs_poll.py [seconds]

Subscribes to pane.agent_status_changed for every agent pane AND polls agent.list twice a
second on a separate connection. Every transition the poller sees is a transition Herdr
knows about, so the report answers the question properly:

    N transitions actually happened, M events were delivered.

"0 events" on its own proves nothing -- nothing may simply have changed.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from herdr_km16.herdr import HerdrClient, _connect, _encode, _unwrap, event_kind  # noqa: E402

POLL_SECONDS = 0.5


async def main() -> int:
    duration = float(sys.argv[1]) if len(sys.argv) > 1 else 900.0
    client = HerdrClient()

    panes = [a["pane_id"] for a in await client.list_agents()]
    own = os.environ.get("HERDR_PANE_ID")
    if own and own not in panes:
        panes.append(own)
    print(f"panes: {panes} (own={own})", flush=True)

    subs = [{"type": "pane.agent_status_changed", "pane_id": p} for p in panes]
    reader, writer = await _connect(client.path)
    writer.write(_encode("corr", "events.subscribe", {"subscriptions": subs}))
    await writer.drain()
    _unwrap(json.loads(await reader.readline()))
    print(f"subscribed to {len(subs)} pane(s); running {duration:.0f}s\n", flush=True)

    start = time.monotonic()
    transitions: list[tuple[float, str, str, str]] = []
    events: list[tuple[float, str, str]] = []

    subscribed = set(panes)
    unsubscribed_transitions: list[tuple[float, str, str, str]] = []

    async def poll() -> None:
        seen: dict[str, str] = {}
        while time.monotonic() - start < duration:
            try:
                for a in await client.list_agents():
                    pane, status = a["pane_id"], a.get("agent_status", "unknown")
                    if pane in seen and seen[pane] != status:
                        t = time.monotonic() - start
                        row = (t, pane, seen[pane], status)
                        # Only transitions on subscribed panes can prove anything: Herdr
                        # renumbers pane IDs on move, and new agent panes appear mid-run,
                        # so a transition on an unwatched pane is not evidence of anything.
                        if pane in subscribed:
                            transitions.append(row)
                            tag = "POLL  "
                        else:
                            unsubscribed_transitions.append(row)
                            tag = "POLL* "  # not subscribed; excluded from the verdict
                        print(f"  +{t:6.1f}s  {tag} {pane} {seen[pane]} -> {status}", flush=True)
                    seen[pane] = status
            except Exception as exc:
                print(f"  poll error: {exc}", flush=True)
            await asyncio.sleep(POLL_SECONDS)

    listener_alive_until = [0.0]

    async def listen() -> None:
        """Read events in short slices, so liveness is provable rather than assumed.

        A single long wait_for cannot distinguish "no events arrived" from "this coroutine
        died an hour ago", which would make a zero count meaningless.
        """
        while True:
            now = time.monotonic() - start
            listener_alive_until[0] = now
            if now >= duration:
                return
            try:
                line = await asyncio.wait_for(reader.readline(), timeout=5.0)
            except asyncio.TimeoutError:
                continue  # idle slice; loop and re-stamp liveness
            if not line:
                print(f"  +{now:6.1f}s  event stream CLOSED by server", flush=True)
                return
            msg = json.loads(line)
            if event_kind(msg) == "pane_agent_status_changed":
                d = msg["data"]
                events.append((now, d["pane_id"], d["agent_status"]))
                print(f"  +{now:6.1f}s  EVENT  {d['pane_id']} -> {d['agent_status']}", flush=True)
            else:
                # Any other event proves the subscription connection is still delivering.
                print(f"  +{now:6.1f}s  (other event: {msg.get('event')})", flush=True)

    try:
        await asyncio.gather(poll(), listen())
    finally:
        writer.close()

    print(f"\n=== result over {duration:.0f}s ===")
    print(f"  transitions on SUBSCRIBED panes : {len(transitions)}")
    print(f"  transitions on other panes      : {len(unsubscribed_transitions)} (excluded)")
    print(f"  pane_agent_status_changed events: {len(events)}")
    print(f"  listener alive through          : +{listener_alive_until[0]:.0f}s")
    if listener_alive_until[0] < duration * 0.95:
        print("\n  WARNING: the listener stopped early, so the event count is not trustworthy.")
        return 1
    if not transitions and unsubscribed_transitions:
        print("\n  INCONCLUSIVE: everything that moved was on a pane we never subscribed to")
        print("  (Herdr renumbers pane IDs on move, and new agent panes appear mid-run).")
        print("  Rerun so a subscribed pane changes state.")
        return 1
    if transitions and not events:
        print("\n  VERDICT: Herdr changed agent status but delivered no events.")
        print("  The subscription is accepted and other event types arrive on the same")
        print("  connection, so polling is the only reliable path on this build.")
    elif events:
        print("\n  VERDICT: events DO fire. The daemon should prefer them over polling.")
    else:
        print("\n  INCONCLUSIVE: nothing changed state; rerun while an agent is working.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        pass
