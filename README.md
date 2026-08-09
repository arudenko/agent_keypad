# herdr-km16

An MMD KM16 macropad as a bidirectional physical control surface for the Claude Code agents
running inside [Herdr](https://herdr.dev).

- The top 12 keys are live agents. Press one to focus it.
- Each key's RGB shows that agent's state — see [Using the pad](#using-the-pad).
- The bottom row approves, rejects, interrupts, or jumps to whatever needs you next.
- The 6 underglow LEDs summarise the whole session at a glance.
- Three encoders navigate agents and send Esc / Enter.
- Runs over RAW HID, so it works regardless of which desktop app has keyboard focus.

Design rationale and acceptance criteria: [`herdr-km16-controller-handoff.md`](herdr-km16-controller-handoff.md).
Working notes and hard-won protocol details: [`CLAUDE.md`](CLAUDE.md).

## Current status

| Phase | State |
| --- | --- |
| 0 — inspect environment | **done**, recorded in `CLAUDE.md` |
| 1 — firmware backup | **done** — 122880 bytes verified, offsite copy, see `docs/firmware-backup.md` |
| 2 — flash RawMacroPad | **done** — patched build, device live on RAW HID `1209:88bf` |
| 3 — hardware self-test | **done** — 19/19 keys, 3/3 encoders, all LED chains confirmed |
| 4 — Herdr client | **done and verified** against the live session |
| 5 — integration | **working** — agents map to keys, LEDs track state, keys focus and act |
| 6 — polish | config, logging, reconnect done; startup service not yet set up |

LED updates are event-driven and land within milliseconds of a state change. A periodic
resync (`herdr.poll_seconds`, default 5 s) runs behind them purely as a backstop.

The only outstanding item is the startup service — the daemon does not yet launch at logon.

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

## Setup

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest
```

## What you need to do next

### 1. Flashing toolchain — done

Already installed and verified on this machine:

| | |
| --- | --- |
| `arduino-cli` | 1.5.1, `C:\Program Files\Arduino CLI\` (winget `ArduinoSA.CLI`) |
| `dfu-util` | 0.11, `C:\Soft\dfu-util\win64` — added to the **user** PATH |
| STM32 core | `STMicroelectronics:stm32@3.0.0` |
| RawMacroPad | cloned to `%USERPROFILE%\km16-firmware-backup\rawMacroPad` at `ead652e` |
| Firmware | **compiled**: 22684 bytes, 17% of flash |

`dfu-util` is not in winget, and neither Chocolatey nor Scoop is on this machine, so it was
installed by hand from <https://dfu-util.sourceforge.net/releases/>. A shell opened before
the PATH edit won't see it — open a new one.

Note the build output is `km16.ino.bin`, named after the sketch — **not** the
`firmware.ino.bin` that the upstream readme and the handoff doc both claim. The flash script
discovers it rather than hardcoding a name.

You will also need a USB driver that `dfu-util` can talk to. On Windows the STM32duino
bootloader usually needs WinUSB bound to it — use [Zadig](https://zadig.akeo.ie/), select the
device that appears while it is in bootloader mode (`Maple DFU` / `STM32 BOOTLOADER`),
and install the **WinUSB** driver. Do this only for the bootloader device, not for the
normal KM16 keyboard interface.

### 2. Back up the stock firmware — do this before anything else

Unplug the KM16, **hold the top-left key**, and plug it back in. Then:

```powershell
.\scripts\backup-km16-firmware.ps1
```

The script refuses to continue unless it sees bootloader `1eaf:0003` and gets exactly
122880 bytes back. `Error during upload (LIBUSB_ERROR_PIPE)` at the end is expected and fine.

If the bootloader ID is anything else, **stop** — your unit may be a different hardware
revision from the one RawMacroPad was reverse engineered against.

The backup lands in `%USERPROFILE%\km16-firmware-backup\` deliberately, outside the repo.
Copy it somewhere safe.

### 3. Flash RawMacroPad

Bootloader mode again (hold top-left key while plugging in), then:

```powershell
.\scripts\flash-rawmacropad.ps1
```

It won't run without a verified backup. Afterwards the layer indicator should **flash red** —
that is the watchdog telling you no host client is connected yet. Correct, not an error.

The device now enumerates as RAW HID `1209:88BF` instead of the stock `5343:0080`.

### 4. Prove the hardware

```powershell
.\.venv\Scripts\python.exe tools\hw_selftest.py
```

Walks every LED, then waits for you to press all 19 keys and turn all 3 encoders.

Watch step 4 in particular: it sets **one** LED white. If the whole pad goes white, the
upstream `0x04`/`0x06` bug has come back — see `CLAUDE.md`.

### 5. Run the controller

```powershell
.\.venv\Scripts\herdr-km16.exe
```

That's it. It runs in the foreground and logs what it does; **Ctrl+C** to stop. Add `-v` for
debug logging, or `-c <path>` for a different config.

It works from any directory — with no `-c` it uses `./config.yaml` if present, otherwise the
copy in the repo, and logs which one it picked. A `-c` path that doesn't exist is a hard
error rather than a silent fall back to defaults.

Start order doesn't matter: the daemon tolerates Herdr not running and the keypad being
unplugged, and reconnects to either. While no client is connected the pad's layer indicator
flashes red — that's the firmware watchdog, and it stops once the daemon attaches.

To run it without a console window:

```powershell
Start-Process -WindowStyle Hidden .\.venv\Scripts\pythonw.exe -ArgumentList '-m','herdr_km16.main'
```

You can check the Herdr half at any time, no hardware needed:

```powershell
.\.venv\Scripts\python.exe tools\herdr_watch.py
```

That prints the current agents, the slot assignment, an ANSI mock of the 4x4 pad and the exact
LED frame that would be pushed, then streams live events.

### 6. Run it at startup

Not yet set up. Once you are happy with it, register a Task Scheduler job at logon running
`.venv\Scripts\pythonw.exe -m herdr_km16.main`. The daemon already tolerates Herdr and the
keypad being absent, so it is safe to start before either.

## Rolling back

```powershell
.\scripts\restore-km16-firmware.ps1
```

Bootloader mode first. The pad returns to stock and works with VIA again.

## Safety

The keypad drives agents that can execute shell commands, so:

- **Agent keys (0–11) only focus.** Selecting an agent never answers a prompt or sends a
  keystroke, so navigating the pad is always safe.
- Approving is a deliberate, separate act: the **approve key** (12), held for 300 ms.
  `approve`, `enter` and `interrupt` are all gated (`safety.require_long_press_for`).
- Approve sends Enter, accepting whichever option Claude Code has highlighted — usually but
  not always "Yes". It is a fast path for prompts you have read, not a way to skip reading.
- Keys are debounced at 50 ms, and every control action is logged.
- Focusing a `done` agent marks it seen, so green turns to dim white. That's Herdr's
  semantics, not a bug.

## Layout

```
CLAUDE.md          working reference: verified environment + protocol gotchas
config.yaml        all tunable behaviour
docs/              committed Herdr schema + skill doc (regenerate after Herdr updates)
scripts/           firmware backup / flash / restore, schema refresh
src/herdr_km16/    the daemon
tools/             hardware + Herdr probes:
                     check_device.py       what the pad enumerates as
                     hw_selftest.py        LEDs, keys, encoders (Phase 3)
                     herdr_watch.py        agents, slots, LED frame, events (Phase 4)
                     diag_read.py          isolate HID read failures
                     diag_chains.py        find which LED command misbehaves
                     diag_event_vs_poll.py prove events fire, against real transitions
tests/             unit tests; no hardware or Herdr required
```
