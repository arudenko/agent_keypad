# herdr-km16

An MMD KM16 macropad as a bidirectional physical control surface for the Claude Code agents
running inside [Herdr](https://herdr.dev).

- Each of the 16 keys is a live agent. Press one to focus it.
- Each key's RGB shows that agent's state: blue working, red blocked, green done, dim white
  idle, amber unknown, off empty.
- The 6 underglow LEDs summarise the whole session at a glance.
- Three encoders navigate agents and send Esc / Enter.
- Runs over RAW HID, so it works regardless of which desktop app has keyboard focus.

Design rationale and acceptance criteria: [`herdr-km16-controller-handoff.md`](herdr-km16-controller-handoff.md).
Working notes and hard-won protocol details: [`CLAUDE.md`](CLAUDE.md).

## Current status

| Phase | State |
| --- | --- |
| 0 — inspect environment | **done**, recorded in `CLAUDE.md` |
| 1 — firmware backup | **not started** — needs `dfu-util` + physical replug |
| 2 — flash RawMacroPad | **not started** |
| 3 — hardware self-test | tool ready (`tools/hw_selftest.py`), untested — needs Phase 2 |
| 4 — Herdr client | **done and verified** against the live session |
| 5 — integration | daemon written; LED/mapping/protocol unit-tested, end-to-end blocked on Phase 2 |
| 6 — polish | config, logging, reconnect done; startup service not yet set up |

The keypad is currently on **stock firmware** and still works as a normal VIA macropad.
Nothing so far has touched the device.

## Setup

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest
```

## What you need to do next

### 1. Install the flashing toolchain

`arduino-cli` is in winget:

```powershell
winget install ArduinoSA.CLI
```

`dfu-util` is **not** in winget (checked), and neither Chocolatey nor Scoop is installed on
this machine. Download the prebuilt Windows binaries instead:

1. Grab `dfu-util-0.11-binaries.tar.xz` from <https://dfu-util.sourceforge.net/releases/>
2. Extract the `win64` folder somewhere permanent, e.g. `%USERPROFILE%\tools\dfu-util\`
3. Add that folder to your `PATH`:

```powershell
$dir = "$env:USERPROFILE\tools\dfu-util"
[Environment]::SetEnvironmentVariable(
    'Path', [Environment]::GetEnvironmentVariable('Path','User') + ";$dir", 'User')
```

Reopen the shell afterwards so `PATH` picks both tools up, then confirm:

```powershell
dfu-util --version; arduino-cli version
```

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
.\.venv\Scripts\python.exe -m herdr_km16.main --config config.yaml -v
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

- A key press only **focuses** an agent. It never answers a blocked prompt.
- `enter` and `interrupt` need a long press (600 ms default, `config.yaml`).
- Focusing a `done` agent marks it seen, so green turns to dim white. That's Herdr's
  semantics, not a bug.

## Layout

```
CLAUDE.md          working reference: verified environment + protocol gotchas
config.yaml        all tunable behaviour
docs/              committed Herdr schema + skill doc (regenerate after Herdr updates)
scripts/           firmware backup / flash / restore, schema refresh
src/herdr_km16/    the daemon
tools/             interactive probes for Phase 3 (hardware) and Phase 4 (Herdr)
tests/             45 unit tests; no hardware or Herdr required
```
