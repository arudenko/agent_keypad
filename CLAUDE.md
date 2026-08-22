# agent_keypad — MMD KM16 → agterm agent controller (macOS fork)

Turn an MMD KM16 macropad into a bidirectional physical control surface for the Claude Code
agents running inside agterm: keys focus sessions, per-key RGB reflects agent state, encoders
navigate and act.

This fork ports the original Windows/Herdr controller (by bramdes) to macOS + agterm. The
legacy Herdr client stays importable at `src/herdr_km16/herdr.py` but is unmaintained; its
hard-won transport notes live in that module's docstring and in git history.

## Environment (verified 2026-08-22 — do not re-derive, but do re-verify if something breaks)

| Thing | Value |
| --- | --- |
| OS | macOS (Darwin 25.5.0), Apple Silicon |
| Python | `.venv` in repo root, CPython 3.13 (`python3`) |
| agterm | control socket `~/Library/Application Support/agterm/agterm.sock`; bundle id `com.umputun.agterm` |
| agterm hooks | Claude Code status hooks installed in `~/.claude/settings.json` (blocked / active --blink / completed --auto-reset) |
| KM16 (stock) | USB `VID 0x5343 / PID 0x0080`, product string `KM16`, **8 HID interfaces on macOS** |
| KM16 (now) | **flashed**, RAW HID `VID 0x1209 / PID 0x88BF`, usage page **0xFF00**, 64-byte packets |
| Stock backup | `~/km16-firmware-backup/backup.bin` + `.sha256`, 122880 bytes |
| dfu-util / arduino-cli | 0.11 / 1.5.1 via Homebrew; STM32 core 3.0.0 |

Claude Code runs *inside* agterm here, so `AGTERM_ENABLED=1` and this session is itself one
of the agents the keypad will control. Handy for testing; also means a careless
`session.type` can hit your own session.

## Python conventions

Always use the repo-local virtualenv. Never install globally.

```bash
.venv/bin/pip install <pkg>     # then record it in pyproject.toml
.venv/bin/python -m pytest      # run tests
```

Dependencies: `hidapi` (`import hid` is lazy in km16.py), `pyyaml`, `pytest`.

## agterm control socket — verified facts

All verified empirically against the live install by capturing `agtermctl`'s wire traffic
and probing the socket (see `src/herdr_km16/agterm.py`'s docstring, and
`tests/test_agterm.py` which pins the framing):

- AF_UNIX socket; resolve like agtermctl: `$AGTERM_SOCKET`, else `$AGTERM_STATE_DIR`, else
  `~/Library/Application Support/agterm/agterm.sock`.
- Requests are one JSON object per line: `{"cmd": ..., "target": ..., "args": {...}}` —
  `target` and `args` are top-level and optional.
- Responses: `{"ok": true, "result": {...}}` or `{"ok": false, "error": "<string>"}`.
- **Every connection is one-shot** — the server closes it after a single response; a second
  request on the same connection gets a reset. Fresh connection per request.
- **Events are a cursor poll, not a subscription** (`events.read`). Result:
  `{"events": {"run": UUID, "items": [...], "next": N}}`. No cursor = baseline at the
  current tail (empty items). Resume with `{"run": <run>, "after": "<next>", "limit": N}` —
  **`after` is a string on the wire**. A changed `run` UUID means the app restarted and the
  cursor is void: resync from `tree`. agtermctl idles 250 ms between polls; so do we.
- Event kinds used: `status` (payload `{status, blink, name, pane?}`, explicit `"idle"`),
  `session.created`, `session.closed`, `tree.changed` (empty payload). `notify` is ignored.
- `tree` returns the frontmost window's workspaces → sessions. A session node carries
  `status` (`active|completed|blocked`, **absent = idle**), `name`, `cwd`, `title`, ids.
- Commands used: `session.select` (focus), `session.type` (`args: {text, select:false}`;
  a newline is a Return press), `session.go` (`args: {to: "next-attention"}`, returns the
  landed session id).
- The socket never raises the app over other macOS applications — that needs
  `open -b com.umputun.agterm` (see `AgtermClient.activate_app`).

**Identity trap — session names are OSC titles.** Claude Code rewrites the sidebar name
constantly, and any program rendering output in a session can set it. Identity and command
target are therefore ALWAYS the session UUID; `_agent_from_record` deliberately leaves
`Agent.name` unset and `tests/test_agterm.py` pins that. Never key anything on the name.

Status mapping (one translation layer, `agterm.map_status`): `active→working`,
`completed→done`, `blocked→blocked`, absent/`idle`→`idle`, anything else→`unknown`. The
internal vocabulary stays Herdr's so config colour keys and the renderer are unchanged.

Note the Claude Code hook sets `completed --auto-reset`, so revealing a session in agterm
collapses `done` back to idle — pressing a green key turns it dim white. Correct behaviour,
not a bug.

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
docs/          legacy Herdr artifacts + protocol notes (historical)
scripts/       firmware backup/flash/restore: bash (macOS) + legacy .ps1 (Windows)
src/herdr_km16/  the daemon; agterm.py is the live backend, herdr.py is legacy
tools/         interactive hardware + agterm probes (agterm_watch, probe_device, ...)
tests/         unit tests (no hardware, no agterm required)
```

## Safety rules

These are not optional — the keypad can drive agents that execute shell commands.

- Never auto-approve a `blocked` agent. Selecting or focusing a session must never answer a
  prompt — approval is only ever a separate, deliberate action on its own key.
- **Agent keys (0–11) focus and nothing more.** They never send a keystroke.
- Approval lives on the bottom row (keys 12–15), added at the user's request 2026-08-09.
  `approve` and `interrupt` require a 300 ms hold; `reject` and `next_attention` are instant
  because neither can approve anything. Guards are enforced in `ActionRouter.run_action` and
  tested in `tests/test_action_keys.py` — treat those tests as requirements, not examples.
- Approve types a literal Return (`"\n"` via `session.type`), which accepts whatever option
  Claude Code has highlighted. That is usually but not always "Yes", so it is a fast path
  for prompts the user has already read.
- Use structured socket requests or `subprocess` argument arrays. Never `shell=True`,
  never interpolate an agent name into a command string.
- Debounce physical keys.
- Log every control action while developing.
- **Firmware backup before any flash.** The backup pair (`backup.bin` + `backup.sha256`,
  exactly 122880 bytes) is stored *outside* the repo; `.gitignore` blocks `*.bin`
  deliberately, and the flash script refuses to run without a verified pair.

## Status (macOS fork, 2026-08-22)

The port is done and verified on hardware:

- Stock firmware backed up on this unit (122880 bytes, `~/km16-firmware-backup/backup.bin`
  + `.sha256`); the hash in `docs/firmware-backup.md` is the ORIGINAL author's unit --
  hashes are device-specific, the exact byte count is the load-bearing check.
- **Patched** RawMacroPad firmware flashed; device live on `1209:88bf`, usage page 0xFF00
  (NOT QMK's 0xFF60 -- the stock VIA firmware is the one that exposes 0xFF60).
- Keys, encoders and LED chains verified end-to-end against live agterm sessions; keys
  focus (and raise the app via `open -b`), bottom row acts, Next jumps server-side.
- `mapping.compact: true` closes key gaps on session close; the reconcile makes the
  selection follow the AGENT across the shift so a post-close approve cannot misfire.
- Both live states animate as full-depth fades to true dark and back: `blocked` red at
  1 s, `working` blue at 2 s -- pace signals urgency (`leds.PULSE_*`; the square-wave
  blink variant remains available in `pulse_factor`).

Remaining: no launchd startup service yet.

macOS DFU needs no driver step at all (no Zadig/WinUSB); `brew install dfu-util` and the
bash scripts are enough. Enter bootloader mode by holding the top-left key while plugging
in USB. Input Monitoring permission is required for HID *reads* -- a pad that lights but
ignores keys is that permission missing (see README).
