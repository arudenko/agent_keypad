# herdr-km16

**A 16-key macropad as a physical control surface for AI coding agents.**

If you run several Claude Code sessions at once, you lose track of them. One is waiting on a
permission prompt, one finished ten minutes ago, one is still grinding — and you find out by
alt-tabbing around.

This puts them on your desk. Each key is an agent, lit by its live state. Press a key to jump
to that agent; the bottom row approves, rejects or interrupts whichever one you have selected.
It runs over RAW HID, so it works no matter which window has focus.

```
   +---------+---------+---------+---------+
   |  blue   |  green  |dim white|   off   |     blue   = working
   | working |  done   |  idle   |         |     green  = done, unseen
   +---------+---------+---------+---------+     white  = idle
   | RED     |         |         |         |     RED    = blocked (pulsing)
   | blocked |         |         |         |     off    = no agent
   +---------+---------+---------+---------+
   |         |         |         |         |
   +---------+---------+---------+---------+
   | APPROVE | REJECT  |  STOP   |  NEXT   |  <- actions, on the selected agent
   +---------+---------+---------+---------+
      [ main knob ]   [ left ]   [ right ]
       cycle+focus      esc      brightness
```

The 6 underglow LEDs summarise everything at a glance — red if anything is blocked, green if
anything finished, blue if anything is working. So the pad tells you across the room.

It talks to [Herdr](https://herdr.dev), a terminal workspace manager that already knows which
panes contain agents and what state they are in, over its local socket API.

## Status

Working, and in daily use on the author's machine. Be aware of the caveats:

- **Tested on exactly one KM16.** The firmware is community reverse-engineered and units sold
  under the same name could differ. The backup step below is not optional.
- **Windows-only so far.** The Herdr client handles AF_UNIX for Linux/macOS but that path is
  unexercised; the flashing scripts are PowerShell.
- **Pinned to Herdr protocol 19** (0.8.0-preview). The bundled schema is committed and
  `scripts/refresh-herdr-schema.ps1` regenerates it.
- Flashing replaces the stock VIA firmware. You can restore it; see [Rolling back](#rolling-back).

One known cosmetic issue: occasional LED flicker, cause not yet identified. `km16.pulse: false`
stops it.

## Requirements

| | |
| --- | --- |
| Hardware | MMD KM16 macropad (16 keys, 3 encoders, per-key RGB) |
| Firmware | [RawMacroPad](https://github.com/toptensoftware/rawMacroPad), **patched** — see below |
| Software | [Herdr](https://herdr.dev), Python 3.12+ |
| Flashing | `dfu-util`, `arduino-cli`, and WinUSB via [Zadig](https://zadig.akeo.ie/) on Windows |

## Quick start

```powershell
git clone https://github.com/bramdes/agent_keypad
cd agent_keypad
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest        # full suite; no hardware or Herdr needed
```

You can exercise the whole Herdr half before touching the keypad:

```powershell
.\.venv\Scripts\python.exe tools\herdr_watch.py
```

That prints your live agents, how they map to keys, an ANSI mock of the 4x4 pad, and the exact
LED frame that would be pushed — then streams state changes.

Then follow [Flashing the keypad](#flashing-the-keypad), and run it:

```powershell
.\.venv\Scripts\herdr-km16.exe
```

Start order does not matter: it tolerates Herdr not running and the keypad being unplugged, and
reconnects to either.

## Using the pad

### LED colours

Every colour below is a default in [`config.yaml`](config.yaml) and can be changed there.

| Colour | State | What it means | Default |
| --- | --- | --- | --- |
| ⚪ dim white | `idle` | Ready for input, and you have seen it | `#202020` |
| 🔵 blue | `working` | Busy right now | `#0066ff` |
| 🟢 green | `done` | Finished work you have **not looked at yet** | `#00ff44` |
| 🔴 red | `blocked` | Waiting on a permission prompt or a question | `#ff0000` |
| 🟠 amber | `unknown` | An agent is there, but Herdr cannot classify it | `#ff9900` |
| ⚫ off | `empty` | No agent on that key | `#000000` |

Two of them move, so urgency reads from the corner of your eye:

- **`blocked` pulses strongly** (65% depth) — this is the one that wants you.
- **`working` breathes gently** (20% depth).
- Everything else is steady, and the pad sends no HID traffic at all while nothing pulses.

The whole pad runs at `brightness` (default `0.35`) so it is not glaring. The **currently
selected** key is drawn at up to double brightness — brighter, never a different colour, so
the state still reads correctly.

**Green is a one-shot flag.** `done` means "I finished something while you were not looking".
Focusing an agent marks it seen, so pressing a green key turns it dim white. That is Herdr's
semantics, not a bug — the signal has done its job.

### Underglow

The 6 underglow LEDs summarise the entire session, so you get peripheral warning without
reading individual keys. Most urgent state present anywhere wins:

```
any blocked  ->  red        (pulsing)
else done    ->  green
else working ->  blue
else         ->  dim white
```

Set `km16.underglow: false` to switch it off.

### Layer indicator

Not driven by agent state. The firmware flashes it **red** whenever no client is pinging the
watchdog — i.e. the daemon is not running or has died. A calm indicator means the daemon is
alive.

### Key mapping

Keys are numbered top-left to bottom-right. The top three rows are agent slots; the bottom
row is bound to actions, so there are **12 agent slots**:

```
+---------+---------+---------+---------+
|    0    |    1    |    2    |    3    |
|         |     agent slots             |
+---------+---------+---------+---------+
|    4    |    5    |    6    |    7    |
|         |  colour = agent state       |
+---------+---------+---------+---------+
|    8    |    9    |   10    |   11    |
|         |                             |
+---------+---------+---------+---------+
|   12    |   13    |   14    |   15    |
| APPROVE | REJECT  |  STOP   |  NEXT   |
|  green  |   red   |  amber  |  blue   |
+---------+---------+---------+---------+
```

Upstream's readme confusingly calls this "top-left to bottom-right in RTL order". It is plain
row-major, left to right. The physical LED strip does zigzag, but the firmware corrects it
(`ledmap[] = {0,1,2,3, 7,6,5,4, 8,9,10,11, 15,14,13,12}` in `KM16.h`), so the host addresses
logical indices and key *N* always lights LED *N*. Confirmed on this unit by
`tools/hw_selftest.py`.

**Pressing an agent key focuses that agent in Herdr, and nothing else.** It never answers a
prompt, never sends a keystroke, and never approves a `blocked` agent. Pressing an empty key
does nothing. Sending input is the bottom row's job, described below.

Slots are **sticky**: an agent keeps its key for as long as it is alive, new agents take the
lowest free key, and a state change never reshuffles the pad. When an agent exits, its key
frees up for the next one. Pin an agent to a specific key by Herdr agent name:

```yaml
mapping:
  static:
    0: reviewer
    1: backend
```

A pinned key stays reserved (and unlit) until that agent shows up. Pinning to an action key
is a config error, not a silent override.

### Action keys (bottom row)

These act on the **selected** agent — the one you last pressed or cycled to, shown at double
brightness. They never change Herdr focus, and they do nothing at all when no agent is
selected (their keys dim to a quarter brightness to show that).

| Key | Action | Sends | Guard |
| --- | --- | --- | --- |
| 12 | **Approve** | `enter` — accepts the highlighted prompt option | **hold 300 ms** |
| 13 | **Reject** | `esc` — declines | instant |
| 14 | **Interrupt** | `ctrl+c` — stops the agent | **hold 300 ms** |
| 15 | **Next** | *nothing* — selects the next agent needing attention | instant |

Approve sends Enter, which accepts **whichever option Claude Code currently has highlighted**
— normally "Yes", but it is not guaranteed to be. Approve is a fast path for prompts you have
already read, not a substitute for reading them. Reject and Next are instant because neither
can approve anything.

Action keys are steady and never pulse: they are controls, not status.

Rebind or remove any of them in `config.yaml`; each key you free goes back to being an agent
slot.

```yaml
action_keys:
  12: approve      # approve | reject | interrupt | next_attention | none
  13: reject
  14: interrupt
  15: next_attention
```

### Encoders

All three are also push buttons. Turning and pressing use the same index.

| Control | Index | Turn | Press |
| --- | --- | --- | --- |
| Main encoder | 16 | Cycle **by attention priority**, focusing as it lands | Focus the selected agent |
| Small left | 17 | Cycle agents in slot order | Send **Esc** |
| Small right | 18 | **Brightness** — dim/brighten the whole pad | Send **Enter** — long press only |

Attention priority is `blocked` → `done` → `working` → `idle` → `unknown`, so turning the main
knob walks you through whatever needs you most first. Ties break by slot number, so the order
stays stable.

The main encoder focuses as you turn (`focus_on_turn: true`) — no press needed. A fast spin
only focuses where you stop: each detent cancels the previous pending focus, so Herdr is not
strobed through every agent on the way past.

The other two only move the selection and talk to nobody; the highlighted key brightens and
Herdr is contacted when you press. Set `focus_on_turn` on any encoder to change that.

The right knob adjusts brightness live, `km16.brightness_step` (0.05) per detent. It clamps
at 1.0 and floors at 0.03 rather than 0 — a pad you can accidentally turn completely dark
looks broken. The change is **runtime only**: `config.yaml` is never rewritten, so restarting
returns to your configured `km16.brightness`.

### Safety-critical behaviour

`approve`, `enter` and `interrupt` are gated behind a **300 ms long press**
(`safety.long_press_ms`), so a knocked key or knob cannot accept a Claude Code permission
request. A short press is logged and ignored. Which actions are gated is configurable:

```yaml
safety:
  long_press_ms: 300
  require_long_press_for: [approve, enter, interrupt]
```

**Pressing an agent key still only focuses.** Approving is a separate, deliberate action on a
separate key with a hold — selecting an agent never approves anything on its own.

Keys are debounced at 50 ms. Every control action is logged.

## Flashing the keypad

> **Back up the stock firmware first.** The RawMacroPad author reverse engineered one specific
> unit, and hardware revisions could differ. The backup script refuses to continue unless it
> sees the expected bootloader and reads back exactly 122880 bytes.

**1. Toolchain.** `arduino-cli` is in winget; `dfu-util` is not, so take the Windows binaries
from [dfu-util.sourceforge.net](https://dfu-util.sourceforge.net/releases/) and put them on
your PATH.

```powershell
winget install ArduinoSA.CLI
```

**2. Bootloader mode.** Unplug the pad, hold the **top-left key**, and plug it back in. No LEDs
light — that is correct.

**3. WinUSB.** Windows binds no driver to the bootloader, so `dfu-util` will see `1eaf:0003`
but fail with `LIBUSB_ERROR_NOT_SUPPORTED`. Use [Zadig](https://zadig.akeo.ie/): *Options → List
All Devices*, select **SmartBoot** (verify the USB ID reads `1EAF 0003`), install **WinUSB**.
Only bind the bootloader device, never the normal keyboard interface.

**4. Back up, then flash.**

```powershell
.\scripts\backup-km16-firmware.ps1     # verifies 122880 bytes, then records a SHA256
.\scripts\flash-rawmacropad.ps1        # refuses to run without a verified backup
```

The flash script pins upstream to a known revision, applies the patches in `firmware/patches/`,
and refuses to build if it can still find the fall-through bug described below.

Afterwards the layer indicator flashes red — that is the watchdog saying no client is connected
yet, and it stops when the daemon starts.

**5. Prove the hardware.**

```powershell
.\.venv\Scripts\python.exe tools\check_device.py    # should report 1209:88bf
.\.venv\Scripts\python.exe tools\hw_selftest.py     # LEDs, then all 19 keys and 3 encoders
```

### Rolling back

```powershell
.\scripts\restore-km16-firmware.ps1
```

Bootloader mode first. The pad returns to stock and works with VIA again. WinUSB is bound only
to the bootloader, so it does not interfere either way.

## Two firmware bugs, and they hide each other

Worth knowing if you build anything else on RawMacroPad. Both confirmed at upstream `ead652e`.

**The client sends the wrong command.** `set_led()` in the bundled Python and Node clients
sends `0x04` — "set the whole chain to one colour" — plus an index byte. The protocol spec and
the firmware both define `0x06` for a single LED. So `set_led()` silently paints all 16 keys.

**The firmware resets on the right one.** In `km16.ino`, `case 0x06:` is missing its `break;`
and falls through into `case 0xFF: NVIC_SystemReset()`. A correctly formed single-LED write
**reboots the MCU**. The symptom is an LED write that appears to work, then `hid.read()` failing
with `OSError('read error')` a second later as the device re-enumerates.

Each bug conceals the other. Upstream's client never sends `0x06`, so it never reaches the
broken case — and fixing the client, which is the obvious correct thing to do, is precisely what
detonates the firmware. Patched here in
[`firmware/patches/`](firmware/patches/); the flash script will not build without it.

A third trap, not a bug but undocumented: **the LED chains default to off.** There are three
independent switches — master power, key chain, underglow — and `setKeyLed()` only marks a frame
dirty when its chain is enabled. Send colours with just the master on and they are stored and
silently never shifted out. No error, just a dark pad.

## Configuration

Everything lives in [`config.yaml`](config.yaml) — colours, brightness, key bindings, encoder
actions, which actions are guarded, and how agents pin to keys. No source edits required.

```yaml
mapping:
  static:            # pin an agent to a key by Herdr agent name
    0: reviewer

action_keys:         # free a key here and it goes back to being an agent slot
  12: approve        # approve | reject | interrupt | next_attention | none

safety:
  long_press_ms: 300
  require_long_press_for: [approve, enter, interrupt]
```

Slots are sticky: an agent keeps its key while it lives, new agents take the lowest free key,
and a state change never reshuffles the pad. Identity survives Herdr renumbering a pane.

State reaches the LEDs by subscription, so a key changes within milliseconds of the agent
changing. A periodic resync (`herdr.poll_seconds`, default 5 s) runs behind that purely as a
backstop, for a dropped subscription or a pane that appeared before the daemon resubscribed.

### Logs

Stdout **and** a rotating file, so a failure leaves evidence even when run windowless:

```
%LOCALAPPDATA%\herdr-km16\herdr-km16.log     (1 MB x 4 files)
```

`--log-file <path>` to move it, `--no-log-file` for stdout only. Worth grepping for
`watchdog gap ... exceeded`: the pings stalled long enough for the firmware to disable the LED
chains. The daemon re-enables them, but repeats mean something is blocking the event loop.

To run without a console window:

```powershell
Start-Process -WindowStyle Hidden .\.venv\Scripts\pythonw.exe -ArgumentList '-m','herdr_km16.main'
```

## Layout

```
CLAUDE.md          working notes: verified environment + protocol gotchas
config.yaml        all tunable behaviour
docs/              committed Herdr schema, environment notes, backup provenance
firmware/patches/  the fall-through fix, applied at flash time
scripts/           firmware backup / flash / restore, schema refresh
src/herdr_km16/    the daemon
tools/             hardware and Herdr probes, including the ones that found the bugs above
tests/             unit tests; no hardware or Herdr required
```

[`herdr-km16-controller-handoff.md`](herdr-km16-controller-handoff.md) is the original design
brief this was built from, kept for provenance — including the acceptance criteria it was
measured against.

`CLAUDE.md` is the interesting one if you want the hard-won details: the Windows named-pipe
transport, why a subscribed connection cannot carry requests, the event-envelope naming
inconsistency, and why hidapi needs a single owning thread.

## Credits

- [RawMacroPad](https://github.com/toptensoftware/rawMacroPad) by Topten Software — the RAW HID
  firmware this depends on, and the reverse engineering of the KM16. MIT licensed.
- [Herdr](https://herdr.dev) — the agent-aware terminal workspace manager underneath.

## License

MIT — see [LICENSE](LICENSE). The firmware patch under `firmware/patches/` is a diff against
MIT-licensed RawMacroPad and carries that project's terms.
