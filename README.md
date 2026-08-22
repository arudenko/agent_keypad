# agterm-km16

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

It talks to [agterm](https://github.com/umputun/agterm), a native macOS terminal whose agent
status hooks already know which sessions contain agents and what state they are in, over its
local control socket. (The original Windows/Herdr backend this was forked from is kept in-tree
but unmaintained; see Credits.)

## Status

Working, and in daily use on the author's machine. Be aware of the caveats:

- **Tested on exactly two KM16s** (one Windows/Herdr, one macOS/agterm). The firmware is
  community reverse-engineered and units sold under the same name could differ. The backup
  step below is not optional.
- **This fork targets macOS + agterm.** The Herdr client (`src/herdr_km16/herdr.py`) and the
  PowerShell scripts remain importable but unmaintained.
- **agterm wire format verified empirically** (one-shot connections, cursor-polled
  `events.read`); a future agterm change that breaks framing fails loudly in the test suite.
- Flashing replaces the stock VIA firmware. You can restore it; see [Rolling back](#rolling-back).

One known cosmetic issue: occasional LED flicker, cause not yet identified. `km16.pulse: false`
stops it.

## macOS + agterm (this fork)

This fork runs the daemon natively on macOS against [agterm](https://github.com/umputun/agterm)
instead of Herdr: the backend is `src/herdr_km16/agterm.py`, statuses map
`active→working`, `completed→done`, `blocked→blocked`, absent→`idle`, and the Next key
delegates to agterm's `session.go --to next-attention`. The colour table, key layout and
safety gates below are unchanged.

```bash
git clone https://github.com/arudenko/agent_keypad
cd agent_keypad
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest              # full suite; no hardware or agterm needed
.venv/bin/python tools/agterm_watch.py  # live ANSI mock of the pad, no hardware
.venv/bin/python tools/probe_device.py  # which HID interface the daemon would open
.venv/bin/python -m herdr_km16.main     # the daemon
```

Logs go to `~/Library/Logs/agterm-keypad/daemon.log` (falling back to
`~/.local/state/agterm-keypad/` off macOS); `--log-file` / `--no-log-file` still apply.

Flashing on macOS uses the bash ports of the scripts below — `scripts/backup-km16-firmware.sh`,
`scripts/flash-rawmacropad.sh`, `scripts/restore-km16-firmware.sh` — with
`brew install dfu-util arduino-cli` as the only setup; there is no Zadig/WinUSB step at all.
`backup` refuses anything but an exact 122880-byte read and records `backup.bin` +
`backup.sha256`; `flash` refuses to run without that verified pair and asks for explicit
confirmation. Everything else in [Flashing the keypad](#flashing-the-keypad) still applies.

### Waiting on background subagents

Claude Code fires its `Stop` hook whenever a turn ends — including when the turn ends
*because* the agent is parked waiting for a background subagent — so the stock wiring
shows a waiting agent as green/idle. `scripts/agterm-bgwait-hook.sh` fixes that with
per-subagent marker files: `SubagentStart`/`SubagentStop` hooks maintain the markers and
the `Stop` hook chooses between "still waiting" (`active --blink`, purple-tinted glyph)
and a real `completed`. Backgrounded Bash commands are deliberately not tracked — their
completion re-invokes the agent, whose normal activity hooks recover the status. Wire it
in `~/.claude/settings.json` per the header comment; stale markers expire after 4 hours
and are cleared on session start.

### Input Monitoring permission

The process running the daemon (your terminal app, or the Python binary when launched by
launchd) needs **System Settings ▸ Privacy & Security ▸ Input Monitoring**. Without it,
hidapi opens the keypad without any error and every `read()` silently returns nothing:
LEDs still light (writes go through), but key presses never arrive and the log stays
clean. A pad that lights up yet ignores every key is this permission missing, not a
hardware fault. `tools/probe_device.py` shows the enumeration; if it lists the raw
interface and keys still do nothing, grant the permission and restart the daemon.

### The usage-page trap

macOS enumerates one HID interface per usage page — a stock KM16 shows up eight times.
Only the vendor-defined page `0xFF00` interface speaks the RawMacroPad protocol, so
`KM16.open()` filters `hid.enumerate()` on that page and opens by path. Opening by bare
VID/PID (what the upstream client does) grabs whichever interface enumerates first and
reads nothing, indistinguishable from the missing-permission symptom above.

## Requirements

| | |
| --- | --- |
| Hardware | MMD KM16 macropad (16 keys, 3 encoders, per-key RGB) |
| Firmware | [RawMacroPad](https://github.com/toptensoftware/rawMacroPad), **patched** — see below |
| Software | [agterm](https://github.com/umputun/agterm) with its agent status hooks installed, Python 3.12+ |
| Flashing | `brew install dfu-util arduino-cli` (Windows upstream additionally needs WinUSB via [Zadig](https://zadig.akeo.ie/)) |

## Quick start

```bash
git clone https://github.com/arudenko/agent_keypad
cd agent_keypad
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest              # full suite; no hardware or agterm needed
```

You can exercise the whole agterm half before touching the keypad:

```bash
.venv/bin/python tools/agterm_watch.py
```

That prints your live sessions, how they map to keys, an ANSI mock of the 4x4 pad, and the
exact LED frame that would be pushed — then follows the event cursor live.

Then follow [Flashing the keypad](#flashing-the-keypad), and run it:

```bash
.venv/bin/python -m herdr_km16.main     # or the installed `agterm-km16` script
```

Start order does not matter: it tolerates agterm not running and the keypad being unplugged,
and reconnects to either.

## Using the pad

### LED colours

Every colour below is a default in [`config.yaml`](config.yaml) and can be changed there.

| Colour | State | What it means | Default |
| --- | --- | --- | --- |
| ⚪ dim white | `idle` | Ready for input, and you have seen it | `#202020` |
| 🔵 blue | `working` | Busy right now | `#0066ff` |
| 🟢 green | `done` | Finished work you have **not looked at yet** | `#00ff44` |
| 🔴 red | `blocked` | Waiting on a permission prompt or a question | `#ff0000` |
| 🟠 amber | `unknown` | A session reported a status this build does not know | `#ff9900` |
| ⚫ off | `empty` | No agent on that key | `#000000` |

Two of them move, so urgency reads from the corner of your eye:

- **Both live states fade** from fully dark to lit and back (100% depth): urgent red
  (`blocked`) cycles once a second, calm blue (`working`) breathes at half that pace — so
  urgency reads from the corner of your eye even before the colour does.
- The at-rest states are steady, and no LED frames are sent while nothing pulses (only
  the watchdog pings and the periodic chain re-asserts continue).

The whole pad runs at `brightness` (default `0.35`) so it is not glaring. The **currently
selected** key is drawn at up to double brightness — brighter, never a different colour, so
the state still reads correctly.

**Green is a one-shot flag.** `done` means "I finished something while you were not looking".
The Claude Code hook sets `completed --auto-reset`, so revealing the session in agterm drops
it back to idle — pressing a green key turns it dim white. Not a bug; the signal has done its
job.

### Underglow

The 6 underglow LEDs summarise the entire session, so you get peripheral warning without
reading individual keys. Most urgent state present anywhere wins:

```
any blocked  ->  red        (fast fade)
else done    ->  green
else working ->  blue       (slow fade)
else         ->  dim white
```

Set `km16.underglow: false` to switch it off.

### Layer indicator (the LED by the MMD logo, between the knobs)

While the daemon runs, it shows the **most urgent state present anywhere**, like the
underglow: red if anything is blocked (blinking with the fade), green if anything finished,
blue if anything is working, dark when everything is idle — so a lit logo always means
something is happening. This LED keeps only the top bit of each colour channel, so it shows
full-intensity colours and ignores the brightness knob.

When the daemon is **not** running (or has died), the firmware takes it back and flashes it
red — the watchdog complaining that nobody is pinging it.

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

**Pressing an agent key selects that session in agterm and raises the agterm app — nothing
else.** (Set `agterm.activate_app: false` to keep the app in the background.) It never answers
a prompt, never sends a keystroke, and never approves a `blocked` agent. Pressing an empty key
does nothing. Sending input is the bottom row's job, described below.

With the shipped `mapping.compact: true` the pad **mirrors agterm's sidebar order**,
gap-free: closing a session shifts the ones below it down, reordering sessions in the
sidebar remaps the keys to match, and a new session takes the key its sidebar position
says. A state change never reshuffles anything. Set it `false` for fully sticky keys,
where a session keeps its key for life and a close just leaves that key unlit. Pin
a session to a specific key by its agterm session UUID:

```yaml
mapping:
  static:
    0: "EF084D05-83BB-44BD-AE3B-DD7BD9E8C27D"
```

A pinned key stays reserved (and unlit) until that agent shows up. Pinning to an action key
is a config error, not a silent override.

### Action keys (bottom row)

These act on the **selected** agent — the one you last pressed or cycled to, shown at double
brightness. They never change the agterm selection, and they do nothing at all when no agent is
selected (their keys dim to a quarter brightness to show that).

| Key | Action | Sends | Guard |
| --- | --- | --- | --- |
| 12 | **Approve** | Return — accepts the highlighted prompt option | **hold 300 ms** |
| 13 | **Reject** | Esc — declines | instant |
| 14 | **Interrupt** | Ctrl-C — stops the agent | **hold 300 ms** |
| 15 | **Next** | *no keystrokes* — agterm's own `session.go --to next-attention` jump | instant |

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
only focuses where you stop: each detent cancels the previous pending focus, so agterm is not
strobed through every session on the way past.

The other two only move the selection and talk to nobody; the highlighted key brightens and
agterm is contacted when you press. Set `focus_on_turn` on any encoder to change that.

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

**1. Toolchain.**

```bash
brew install dfu-util arduino-cli
```

(There is no driver step on macOS. On Windows, upstream's `.ps1` scripts apply and the
bootloader additionally needs WinUSB bound via [Zadig](https://zadig.akeo.ie/) — only ever to
the `1EAF 0003` bootloader device, never the keyboard interface.)

**2. Bootloader mode.** Unplug the pad, hold the **top-left key**, and plug it back in. No LEDs
light — that is correct.

**3. Back up, then flash.**

```bash
./scripts/backup-km16-firmware.sh      # refuses anything but exactly 122880 bytes;
                                       # records backup.bin + backup.sha256
./scripts/flash-rawmacropad.sh         # refuses to run without that verified pair,
                                       # and asks for a typed 'flash' confirmation
```

The flash script pins upstream to a known revision, applies the patches in `firmware/patches/`,
and refuses to build if it can still find the fall-through bug described below.

Afterwards the layer indicator flashes red — that is the watchdog saying no client is connected
yet, and it stops when the daemon starts.

**4. Prove the hardware.**

```bash
.venv/bin/python tools/check_device.py    # should report 1209:88bf
.venv/bin/python tools/probe_device.py    # the interface table, and which one opens
.venv/bin/python tools/hw_selftest.py     # LEDs, then all 19 keys and 3 encoders
```

### Rolling back

```bash
./scripts/restore-km16-firmware.sh
```

Bootloader mode first. The pad returns to stock and works with VIA again.

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
  compact: true      # mirror the sidebar order; false = fully sticky keys
  static:            # pin a session to a key by its agterm session UUID
    0: "EF084D05-83BB-44BD-AE3B-DD7BD9E8C27D"

action_keys:         # free a key here and it goes back to being an agent slot
  12: approve        # approve | reject | interrupt | next_attention | none

safety:
  long_press_ms: 300
  require_long_press_for: [approve, enter, interrupt]
```

Identity is stable, keys are not: a session is always tracked by its agterm UUID (the
volatile sidebar name — an OSC title Claude Code rewrites constantly — can never move or
steal a key), but in compact mode its key follows the sidebar position, so closes,
insertions and reorders remap keys. Only a status change is guaranteed to move nothing.

State reaches the LEDs through a 250 ms `events.read` cursor poll, so a key changes within a
beat of the agent changing. A periodic full resync (`agterm.poll_seconds`, default 5 s) runs
behind that purely as a backstop for anything the cursor ever misses.

### Logs

Stdout **and** a rotating file, so a failure leaves evidence even when run windowless:

```
~/Library/Logs/agterm-keypad/daemon.log      (1 MB x 4 files; ~/.local/state off macOS)
```

`--log-file <path>` to move it, `--no-log-file` for stdout only. Worth grepping for
`watchdog gap ... exceeded`: the pings stalled long enough for the firmware to disable the LED
chains. The daemon re-enables them, but repeats mean something is blocking the event loop.

### If the pad goes dark but the keys still work

The firmware watchdog disables both LED chains when pings stop **arriving**, which is not the
same as the daemon stopping **sending**. OS sleep (Windows Modern Standby, macOS sleep)
suspends USB while the process keeps running normally, so the gap check above sees nothing,
logs nothing, and never recovers: a dark pad, working keys, and a clean log. Observed after an
18-minute standby with the daemon logging state changes throughout.

There is no ack to test and no event to wait for, so the daemon does not try to detect this —
it re-sends the three enable switches every `km16.led_reassert_seconds` (default 5, `0`
disables). Enabling a chain re-pushes the pixels the firmware already holds, so it is invisible
when nothing is wrong and costs three 65-byte writes with no repaint.

If you are ever dark with a running daemon, this is the one-liner that distinguishes disabled
chains from frames not arriving — it lights the pad without painting anything:

```bash
.venv/bin/python -c "import hid; from herdr_km16.km16 import *; d=hid.device(); d.open_path(KM16.raw_interface_path()); [d.write(p) for p in (build_enable_all_leds(True), build_enable_chain(CHAIN_KEYS,True), build_enable_chain(CHAIN_UNDERGLOW,True))]"
```

## Layout

```
CLAUDE.md          working notes: verified environment + protocol gotchas
config.yaml        all tunable behaviour
docs/              legacy Herdr schema, environment notes, backup provenance
firmware/patches/  the fall-through fix, applied at flash time
scripts/           firmware backup / flash / restore (bash for macOS, .ps1 for Windows)
src/herdr_km16/    the daemon (agterm.py backend; herdr.py legacy, unmaintained)
tools/             hardware and agterm probes, including the ones that found the bugs above
tests/             unit tests; no hardware or agterm required
```

[`herdr-km16-controller-handoff.md`](herdr-km16-controller-handoff.md) is the original design
brief this was built from, kept for provenance — including the acceptance criteria it was
measured against.

`CLAUDE.md` is the interesting one if you want the hard-won details: the agterm wire format
as actually observed, the macOS usage-page trap, the firmware fall-through bug, and why
hidapi needs a single owning thread.

## Credits

- [RawMacroPad](https://github.com/toptensoftware/rawMacroPad) by Topten Software — the RAW HID
  firmware this depends on, and the reverse engineering of the KM16. MIT licensed.
- [agterm](https://github.com/umputun/agterm) — the agent-aware macOS terminal underneath
  this fork.
- [Herdr](https://herdr.dev) — the terminal workspace manager the original Windows version
  was built for, by [bramdes](https://github.com/bramdes/agent_keypad).

## License

MIT — see [LICENSE](LICENSE). The firmware patch under `firmware/patches/` is a diff against
MIT-licensed RawMacroPad and carries that project's terms.
