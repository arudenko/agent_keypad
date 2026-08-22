"""agterm client against a mock control-socket server.

These encode the wire facts verified against the live agterm install (one-shot
connections, `{"cmd": ...}` requests, `{"ok": ..., "result"/"error": ...}` responses,
string `after` cursors); a future agterm change that breaks framing fails loudly here.
"""

import asyncio
import json
import os
import shutil
import tempfile

import pytest

from herdr_km16 import agterm as a

RUN = "11111111-2222-3333-4444-555555555555"


class FakeAgterm:
    """One-shot-per-connection JSON server, like the real app.

    `responses` maps cmd -> a list of response dicts, popped one per request. Every
    request payload is recorded for wire-format assertions.
    """

    def __init__(self):
        # tmp_path can exceed the 104-char AF_UNIX limit on macOS; mkdtemp stays short.
        self.dir = tempfile.mkdtemp(prefix="km16-")
        self.path = os.path.join(self.dir, "agterm.sock")
        self.requests: list[dict] = []
        self.responses: dict[str, list[dict]] = {}
        self._server = None

    async def __aenter__(self):
        self._server = await asyncio.start_unix_server(self._handle, self.path)
        return self

    async def __aexit__(self, *exc):
        self._server.close()
        await self._server.wait_closed()  # 3.13 unlinks the socket file itself
        shutil.rmtree(self.dir, ignore_errors=True)

    async def _handle(self, reader, writer):
        line = await reader.readline()
        payload = json.loads(line)
        self.requests.append(payload)
        queue = self.responses.get(payload["cmd"], [])
        response = queue.pop(0) if queue else {"ok": True, "result": {}}
        writer.write((json.dumps(response) + "\n").encode())
        await writer.drain()
        writer.close()


def ok(result):
    return {"ok": True, "result": result}


def events_page(items, next_seq, run=RUN):
    return ok({"events": {"run": run, "items": items, "next": next_seq}})


# --- status mapping (R3) ----------------------------------------------------


@pytest.mark.parametrize("raw,internal", [
    ("active", "working"),
    ("completed", "done"),
    ("blocked", "blocked"),
    ("idle", "idle"),
    (None, "idle"),                 # the tree omits `status` entirely when idle
    ("hallucinating", "unknown"),   # a future agterm state must render, not crash
])
def test_status_maps_to_the_internal_vocabulary(raw, internal):
    assert a.map_status(raw) == internal


def test_internal_states_still_cover_the_colour_config():
    from herdr_km16.leds import DEFAULT_COLORS
    assert set(a.AGENT_STATES) <= set(DEFAULT_COLORS), "config.yaml colour keys must keep working"


# --- wire format ------------------------------------------------------------


def test_request_is_newline_terminated_with_top_level_target():
    payload = json.loads(a._encode("session.select", "abc", None))
    assert payload == {"cmd": "session.select", "target": "abc"}
    assert a._encode("tree", None, None).endswith(b"\n")


def test_unwrap_returns_result():
    assert a._unwrap({"ok": True, "result": {"tree": {}}}) == {"tree": {}}


def test_unwrap_raises_on_error_response():
    with pytest.raises(a.AgtermError) as excinfo:
        a._unwrap({"ok": False, "error": "no session matches x"})
    assert "no session matches x" in excinfo.value.message


def test_socket_path_resolution(monkeypatch):
    monkeypatch.delenv("AGTERM_SOCKET", raising=False)
    monkeypatch.delenv("AGTERM_STATE_DIR", raising=False)
    assert a.socket_path().endswith("Library/Application Support/agterm/agterm.sock")
    monkeypatch.setenv("AGTERM_STATE_DIR", "/tmp/state")
    assert a.socket_path() == "/tmp/state/agterm.sock"
    monkeypatch.setenv("AGTERM_SOCKET", "/tmp/explicit.sock")
    assert a.socket_path() == "/tmp/explicit.sock"  # in-session override wins


# --- client calls against the mock server -----------------------------------


TREE = {
    "workspaces": [
        {"sessions": [
            {"id": "AAAA", "name": "one", "cwd": "/a", "status": "active"},
            {"id": "BBBB", "name": "two", "cwd": "/b"},              # idle: no status field
        ]},
        {"sessions": [
            {"id": "CCCC", "name": "three", "cwd": "/c", "status": "blocked"},
        ]},
    ]
}


def test_list_agents_flattens_all_workspaces_and_maps_status():
    async def run():
        async with FakeAgterm() as server:
            server.responses["tree"] = [ok({"tree": TREE})]
            records = await a.AgtermClient(server.path).list_agents()
            assert server.requests == [{"cmd": "tree"}]
            return records

    records = asyncio.run(run())
    assert [r["pane_id"] for r in records] == ["AAAA", "BBBB", "CCCC"]
    assert [r["agent_status"] for r in records] == ["working", "idle", "blocked"]
    # The volatile sidebar name is display-only; identity and target are the UUID.
    assert all(r["terminal_id"] == r["pane_id"] for r in records)
    assert all("name" not in r for r in records)


def test_focus_send_and_jump_wire_shapes():
    async def run():
        async with FakeAgterm() as server:
            client = a.AgtermClient(server.path)
            server.responses["session.go"] = [ok({"id": "CCCC"})]
            await client.focus_agent("AAAA")
            await client.send_keys("AAAA", ["\n"])
            jumped = await client.next_attention()
            return server.requests, jumped

    requests, jumped = asyncio.run(run())
    assert requests[0] == {"cmd": "session.select", "target": "AAAA"}
    assert requests[1] == {
        "cmd": "session.type", "target": "AAAA",
        "args": {"text": "\n", "select": False},
    }
    assert requests[2] == {"cmd": "session.go", "args": {"to": "next-attention"}}
    assert jumped == "CCCC"


def test_error_response_raises_agterm_error():
    async def run():
        async with FakeAgterm() as server:
            server.responses["session.select"] = [{"ok": False, "error": "notFound"}]
            await a.AgtermClient(server.path).focus_agent("nope")

    with pytest.raises(a.AgtermError, match="notFound"):
        asyncio.run(run())


# --- event stream: connect, cursor resume, all four kinds, resync -----------


def make_event(kind, seq, session=None, payload=None):
    event = {"kind": kind, "seq": seq, "ts": 0.0, "window": "W", "payload": payload or {}}
    if session is not None:
        event["session"] = session
    return event


FOUR_KINDS = [
    make_event("status", 1, "AAAA", {"status": "blocked", "blink": True, "name": "one"}),
    make_event("session.created", 2, "DDDD", {"name": "four"}),
    make_event("session.closed", 3, "BBBB", {}),
    make_event("tree.changed", 4),
]


def collect(server_pages, take):
    """Drive a stream against scripted events.read pages; return (events, requests)."""

    async def run():
        async with FakeAgterm() as server:
            server.responses["events.read"] = list(server_pages)
            stream = a.AgtermEventStream(server.path, poll_seconds=0.001)
            received = []
            async for event in stream:
                received.append(event)
                if len(received) >= take:
                    stream.close()
            return received, server.requests

    return asyncio.run(run())


def test_stream_baselines_at_the_tail_then_resumes_with_a_string_cursor():
    received, requests = collect(
        [events_page([], 7), events_page(FOUR_KINDS[:1], 8)], take=1
    )
    assert requests[0] == {"cmd": "events.read", "args": {"limit": 500}}, \
        "first poll carries no cursor: subscribe from the current tail"
    assert requests[1]["args"] == {"limit": 500, "run": RUN, "after": "7"}, \
        "resume cursor is (run, after) with `after` as a string"
    assert received[0]["kind"] == "status"


def test_stream_yields_all_four_event_kinds_in_order():
    received, requests = collect(
        [events_page([], 0), events_page(FOUR_KINDS, 4)], take=4
    )
    assert [e["kind"] for e in received] == [
        "status", "session.created", "session.closed", "tree.changed"
    ]
    assert set(a.TOPOLOGY_KINDS) == {"session.created", "session.closed", "tree.changed"}


def test_stream_advances_the_cursor_past_delivered_events():
    _, requests = collect(
        [
            events_page([], 0),
            events_page(FOUR_KINDS, 4),
            events_page([make_event("status", 5, "AAAA", {"status": "idle"})], 5),
        ],
        take=5,
    )
    assert requests[2]["args"]["after"] == "4", "the cursor must move past delivered events"


def test_run_change_raises_so_the_caller_resyncs():
    """A new run UUID means agterm restarted: the cursor is void and slots may be stale."""

    async def run():
        async with FakeAgterm() as server:
            server.responses["events.read"] = [
                events_page([], 3),
                events_page([], 9, run="99999999-8888-7777-6666-555555555555"),
            ]
            async for _ in a.AgtermEventStream(server.path, poll_seconds=0.001):
                pass

    with pytest.raises(a.AgtermError, match="run_changed"):
        asyncio.run(run())


def test_closed_connection_raises_rather_than_hanging():
    async def run():
        server = FakeAgterm()

        async def slam(reader, writer):
            writer.close()

        srv = await asyncio.start_unix_server(slam, server.path)
        try:
            await a.AgtermClient(server.path).tree()
        finally:
            srv.close()
            await srv.wait_closed()
            shutil.rmtree(server.dir, ignore_errors=True)

    with pytest.raises((a.AgtermError, OSError)):
        asyncio.run(run())


# --- the daemon resyncs on topology events (R1) -----------------------------


def test_topology_events_trigger_a_tree_resync(monkeypatch):
    from herdr_km16 import main as m
    from herdr_km16.config import Config

    controller = m.Controller(Config())
    resyncs = {"n": 0}

    async def fake_reconcile():
        resyncs["n"] += 1
        return []

    class OneShotStream:
        def __init__(self, *args, **kwargs):
            pass

        async def __aiter__(self):
            for event in FOUR_KINDS[1:]:  # created, closed, tree.changed
                yield event
            raise asyncio.CancelledError

    monkeypatch.setattr(controller, "_reconcile", fake_reconcile)
    monkeypatch.setattr(m, "AgtermEventStream", OneShotStream)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(controller.agterm_loop())

    assert resyncs["n"] == 4, "one initial resync plus one per topology event"


def test_status_event_repaints_without_a_resync(monkeypatch):
    from herdr_km16 import main as m
    from herdr_km16.config import Config
    from herdr_km16.mapping import Agent

    controller = m.Controller(Config())
    controller.slots.sync([Agent("AAAA", "working", terminal_id="AAAA")])
    resyncs = {"n": 0}

    async def fake_reconcile():
        resyncs["n"] += 1
        return ["AAAA"]

    class OneShotStream:
        def __init__(self, *args, **kwargs):
            pass

        async def __aiter__(self):
            yield make_event("status", 1, "AAAA", {"status": "blocked", "blink": True})
            raise asyncio.CancelledError

    monkeypatch.setattr(controller, "_reconcile", fake_reconcile)
    monkeypatch.setattr(m, "AgtermEventStream", OneShotStream)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(controller.agterm_loop())

    assert resyncs["n"] == 1, "a known session's status change needs no tree call"
    assert controller.slots.agent_at(0).status == "blocked"
    assert controller.dirty.is_set()


def test_selection_follows_the_agent_across_compaction(monkeypatch):
    """A close shifts keys down (mapping.compact); an approve right after must still hit
    the agent the user selected, not whoever slid onto the old key number."""
    from herdr_km16 import main as m
    from herdr_km16.config import Config

    controller = m.Controller(Config(compact=True))

    async def serve(records):
        async def fake_list_agents():
            return records
        monkeypatch.setattr(controller.client, "list_agents", fake_list_agents)
        await controller._reconcile()

    three = [
        {"pane_id": s, "terminal_id": s, "agent_status": "working"}
        for s in ("AAAA", "BBBB", "CCCC")
    ]
    asyncio.run(serve(three))
    controller.router.selected = 2  # the user selects CCCC on key 2

    asyncio.run(serve([three[0], three[2]]))  # BBBB closes; CCCC compacts down to key 1
    assert controller.slots.slot_of("CCCC") == 1
    assert controller.router.selected == 1, "selection follows the agent, not the key"

    asyncio.run(serve([three[0]]))  # CCCC itself closes
    assert controller.router.selected is None, "a dead selection must clear, not dangle"
