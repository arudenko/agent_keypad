# agent_keypad — MMD KM16 → Herdr agent controller

Turn an MMD KM16 macropad into a bidirectional physical control surface for the Claude Code
agents running inside Herdr: keys focus agents, per-key RGB reflects agent state, encoders
navigate and act.

Full background, rationale and acceptance criteria: `herdr-km16-controller-handoff.md`.
Read that once; this file is the working reference.

## Environment (verified 2026-08-08 — do not re-derive, but do re-verify if something breaks)

| Thing | Value |
| --- | --- |
| OS | Windows 11 Pro 26200 |
| Shell | PowerShell primary; Git Bash available |
| Python | `.venv` in repo root, **CPython 3.12.10** (`py -3.12`) |
| Node | v24.14.0 (not used; Python implementation) |
| Herdr | 0.8.0-preview.2026-08-04-d78e3d3b5126, **protocol 19** |
| Herdr socket | `%APPDATA%\herdr\herdr.sock` via `HERDR_SOCKET_PATH` |
| KM16 (stock) | USB `VID 0x5343 / PID 0x0080`, product string `KM16` |
| KM16 (now) | **flashed**, RAW HID `VID 0x1209 / PID 0x88BF`, 64-byte packets |
| dfu-util | 0.11, `C:\Soft\dfu-util\win64` (on user PATH; not in winget) |
| arduino-cli | 1.5.1, `C:\Program Files\Arduino CLI\`; STM32 core 3.0.0 |

Claude Code runs *inside* Herdr here, so `HERDR_ENV=1` and this session is itself one of the
agents the keypad will control. Handy for testing; also means a careless `agent.send_keys`
can hit your own pane.

## Python conventions

Always use the repo-local virtualenv. Never install globally.

```powershell
.\.venv\Scripts\python.exe -m pip install <pkg>   # then record it in pyproject.toml
.\.venv\Scripts\python.exe -m pytest               # run tests
```

Dependencies: `hidapi` (ships the DLL on Windows; `import hid`), `pyyaml`, `pytest`.

## Herdr socket API — hard-won facts

The bundled schema is the source of truth and is committed at `docs/herdr-api.schema.json`.
Regenerate after any Herdr update:

```powershell
.\scripts\refresh-herdr-schema.ps1
```

Transport and framing, all verified empirically against this install:

- **Windows transport is a named pipe**, not an AF_UNIX socket. The pipe name literally
  embeds the path: `\\.\pipe\C:\Users\<user>\AppData\Roaming\herdr\herdr.sock`.
  The file at `HERDR_SOCKET_PATH` is *not* a socket — it holds `<server_pid>:<token>`.
- **Do not use Python's `open()` on the pipe.** Interleaving reads and writes on the
  CRT-backed handle fails with `OSError: [Errno 22]` after the first response. Use
  `ProactorEventLoop.create_pipe_connection()` (see `src/herdr_km16/herdr.py`).
- Requests are newline-delimited JSON: `{"id": ..., "method": ..., "params": {...}}`.
  **`params` is mandatory** even when empty — omitting it is an `invalid_request`.
- Any protocol error **closes the connection**. There is no recovery; reconnect.
- **A plain RPC connection is one-shot**: the server closes it after the single response.
  Open a fresh connection per request.
- **After `events.subscribe` the connection becomes an event stream only.** Further requests
  on it are not answered — the next line you read is an event. So the daemon keeps
  *two* kinds of connection: short-lived RPC connections, and one long-lived event stream.
- The response body and its trailing `\n` arrive as **separate pipe messages**. Always read
  by line, never assume one read == one message.

**Naming trap — Herdr is inconsistent, and it cost real time here.** Subscription types are
always dot-separated, but the *envelope* spelling varies per event type. Verified on
protocol 19:

```json
{"event": "pane_agent_detected",       "data": {"type": "pane_agent_detected", ...}}
{"event": "pane.agent_status_changed", "data": {...}}   // dots, and no "type" field
```

Matching only the snake_case spelling silently drops every status change and looks exactly
like "Herdr never sends this event" — polling then covers for it, so nothing appears broken.
Always normalise with `herdr.event_kind()` rather than comparing `event` directly.

Subscription coverage:

- `pane.agent_status_changed` **requires an explicit `pane_id`** — there is no wildcard.
  The daemon must subscribe per agent pane and re-subscribe when new panes appear
  (which means re-opening the event stream, since you cannot send on a subscribed one).
- `pane.created` / `pane.closed` / `pane.exited` / `pane.agent_detected` are global (no filter).
- `pane.focused` fires constantly. Do not subscribe unless you actually need it.
- `pane.agent_detected` replays for all existing agent panes right after you subscribe.

Agent states: `idle`, `working`, `blocked`, `done`, `unknown`. Note `done` collapses to
`idle` once seen, and *focusing* an agent marks it seen — so pressing a key will normally
turn a green (done) LED into dim white (idle). That is correct behaviour, not a bug.

## RawMacroPad protocol

Host→device packets are 64 bytes, preceded by a `0x00` report-ID byte, so buffers are 65
bytes and the command lands at `buf[1]`.

| Cmd | Meaning |
| --- | --- |
| `0x01` | set watchdog / ping — `buf[2]`=MSB, `buf[3]`=LSB of interval ms |
| `0x02` | enable all LEDs |
| `0x03` | enable LED chain |
| `0x04` | set whole chain to one colour |
| `0x05` | set whole chain from an RGB array |
| `0x06` | set one LED — `buf[2]`=chain, `buf[3]`=index, `buf[4..6]`=RGB |
| `0xFF` | reset device |

Device→host: `0x01` key event (`[1]`=index, `[2]`=1 press/0 release), `0x02` encoder
(`[1]`=index, `[2]`=signed delta).

KM16 mapping: keys `0..15` top-left to bottom-right; main encoder push `16`; small left `17`;
small right `18`. Encoder turns reuse the same index. LED chain `0` = 16 under-key,
`1` = 6 underglow, `2` = 1 layer indicator (1-bit colour, from the MSB of each component,
and it ignores chain-enable).

### Two upstream bugs, and they mask each other

Both confirmed at upstream HEAD `ead652e` (Feb 2026).

**1. Client bug.** The stock Python and Node clients implement `set_led()` with command
`0x04` *plus* an index byte, but `0x04` is "set whole chain to one colour". It silently
paints the entire chain. `src/herdr_km16/km16.py` is a reviewed local implementation that
uses `0x06` correctly; `tests/test_led_protocol.py` guards it. Do not `pip install` the
upstream client over it.

**2. Firmware bug — found here, not documented anywhere upstream.** In `km16.ino` the
`case 0x06:` block is **missing its `break;`** and falls straight through into
`case 0xFF: NVIC_SystemReset()`. So a correctly-formed single-LED write **reboots the MCU**.
Symptom: LED writes appear to work, then `hid.read()` fails with `OSError('read error')`
about a second later as the device re-enumerates.

The two bugs hide each other. Upstream's client sends `0x04` for `set_led`, so it never
reaches the broken `0x06` case — fixing bug 1 the way the handoff recommends is precisely
what exposes bug 2.

Fixed locally by `firmware/patches/0001-km16-add-missing-break-to-set-single-led.patch`.
`scripts/flash-rawmacropad.ps1` pins upstream to `ead652e`, applies every patch in that
directory, and refuses to flash if a source scan still finds the fall-through. **Never flash
unpatched firmware** — `set_led` is unusable on it.

Prefer pushing a whole 16-LED frame with `0x05` on change rather than many `0x06` writes.
The upstream client also reuses one shared buffer without clearing it between commands;
ours builds a fresh buffer per packet.

### The LED chains default to OFF, and failure is silent

`KM16.h` has **three independent switches**, all initialised `false`:

| Command | Function | Pin |
| --- | --- | --- |
| `0x02` | `setEnableLeds` — master power | PB14 |
| `0x03` chain 0 | `setEnableKeyLeds` | PB13 |
| `0x03` chain 1 | `setEnableUnderglow` | PB12 |

`setKeyLed()` only marks the frame dirty **if its chain is enabled**. Send colours with just
the master on and they are stored in `_keyleds[]` and never shifted out — no error, no
warning, just a dark pad. Use `KM16.power_on_leds()`, which sends all three in order (power
first, because enabling a chain pushes its pixels immediately).

### The watchdog trips on pings not *arriving*, not on us not *sending*

This distinction is the whole trap, and it survived the first fix. `km16._watchdog_loop`
infers a trip from the gap between its **own iterations**, which only catches a stalled event
loop. Windows **Modern Standby** suspends USB while the process keeps running normally: the
loop never gaps, `watchdog_gap_tripped()` stays false, nothing is logged, and the firmware
disables both chains anyway. Result is a dark pad, fully working keys, and a clean log — which
reads exactly like "the daemon isn't running", and it is not.

Verified 2026-08-12 on an 80-hour-uptime daemon: standby 23:16–23:34 with agent state changes
logged at 23:23, 23:24, 23:26 and 23:33, no watchdog warning, dark pad on resume. Sending
nothing but the three enable commands lit it back up, which is what proves the LED frames were
arriving the whole time. Modern Standby fires constantly on this machine (dozens of entries a
day, one of them five hours), so this is routine, not an edge case.

Writes are fire-and-forget and the device is silent unless a key moves, so there is **no ack to
test and no event to wait for**. Do not add cleverer detection — `Controller.led_reassert_loop`
re-sends the three enables every `km16.led_reassert_seconds` (default 5) unconditionally.
Enabling a chain re-pushes the pixels the firmware already holds, so it repaints nothing and is
invisible when nothing is wrong. `tests/test_led_reassert.py` pins that it keeps firing on a
*healthy* pad; a change making it conditional would pass every other test in the suite.

Separately, a real USB drop (device re-enumerates) does self-heal: hidapi raises, `device_loop`
reconnects and calls `power_on_leds()`. That path is fine and was seen working at 21:21 the
same day. Only the silent suspend needed fixing.

### hidapi handles are not thread-safe

Reading on one thread while writing on another makes the Windows backend fail with
`OSError('read error')` within seconds. `KM16` therefore gives the handle a **single owning
thread** with a write queue (`_io_loop`), and reads with a short timeout so queued writes
stay responsive. Do not call `hid.write()` from the event loop.

Note this was *not* the cause of the `read error` originally chased here — that was the
firmware `0x06` reset above — but the race is real and was fixed on the way past.

## Layout

```
docs/          committed generated artifacts + protocol notes
scripts/       PowerShell: firmware backup/flash/restore, schema refresh
src/herdr_km16/  the daemon
tools/         interactive hardware + Herdr probes (Phase 3/4)
tests/         unit tests (no hardware, no Herdr required)
```

## Safety rules

These are not optional — the keypad can drive agents that execute shell commands.

- Never auto-approve a `blocked` agent. Selecting or focusing an agent must never answer a
  prompt — approval is only ever a separate, deliberate action on its own key.
- **Agent keys (0–11) focus and nothing more.** They never send a keystroke.
- Approval lives on the bottom row (keys 12–15), added at the user's request 2026-08-09.
  `approve` and `interrupt` require a 300 ms hold; `reject` and `next_attention` are instant
  because neither can approve anything. Guards are enforced in `ActionRouter.run_action` and
  tested in `tests/test_action_keys.py` — treat those tests as requirements, not examples.
- Approve sends `enter`, which accepts whatever option Claude Code has highlighted. That is
  usually but not always "Yes", so it is a fast path for prompts the user has already read.
- Use structured socket requests or `subprocess` argument arrays. Never `shell=True`,
  never interpolate an agent name into a command string.
- Debounce physical keys.
- Log every control action while developing.
- **Firmware backup before any flash.** `original_firmware.bin` must be 122880 bytes and is
  stored *outside* the repo; `.gitignore` blocks `*.bin` deliberately.

## Status

All phases through integration are done and verified on hardware:

- Stock firmware backed up (122880 bytes, hash in `docs/firmware-backup.md`) with an
  offsite copy. Restore procedure is scripted and the bootloader identity matched exactly.
- **Patched** RawMacroPad firmware flashed; device live on `1209:88bf`.
- 19/19 keys, 3/3 encoders and all three LED chains confirmed by `tools/hw_selftest.py`.
- Daemon drives the pad from live agent state; keys focus, bottom row acts.

LED updates are event-driven: `pane.agent_status_changed` does fire, and arrives slightly
ahead of what polling detects. `reconcile_loop` (`herdr.poll_seconds`, 5 s) is only a
backstop for a dropped subscription or a not-yet-resubscribed pane.

Remaining: no startup service yet. `tools/diag_event_vs_poll.py` re-checks event delivery
against real transitions if this is ever in doubt again -- it distinguishes subscribed from
unsubscribed panes and proves its own listener stayed alive, because three earlier attempts
at that experiment each produced a confident wrong answer without those controls.

Getting into DFU needs WinUSB bound to the bootloader via Zadig (`C:\Soft\zadig-2.9.exe`);
without it `dfu-util` sees `1eaf:0003` but cannot open it. Enter bootloader mode by holding
the top-left key while plugging in USB.
