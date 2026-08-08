# Verified environment

Everything here was measured on this machine on 2026-08-08, not assumed. Re-measure after any
Herdr or firmware change; `scripts/refresh-herdr-schema.ps1` regenerates the schema artifacts.

## Host

| | |
| --- | --- |
| OS | Windows 11 Pro 10.0.26200 |
| Python | 3.12.10 in `.venv` (also available: 3.14 via `py -3.14`) |
| Node | v24.14.0 |
| git | 2.53.0.windows.2 |
| STM32 core | `STMicroelectronics:stm32@3.0.0` |
| Zadig | 2.9 at `C:\Soft\zadig-2.9.exe`, WinUSB bound to the bootloader |
| dfu-util | 0.11 at `C:\Soft\dfu-util\win64`, added to the user PATH |
| arduino-cli | 1.5.1 at `C:\Program Files\Arduino CLI\` (winget `ArduinoSA.CLI`) |

## Herdr

| | |
| --- | --- |
| Version | 0.8.0-preview.2026-08-04-d78e3d3b5126 |
| Protocol | 19 |
| Schema version | 1 |
| Binary | `%LOCALAPPDATA%\Programs\Herdr\bin\herdr.exe` |
| Config | `%APPDATA%\herdr\config.toml` |
| Logs | `%APPDATA%\herdr\herdr.log` |
| Capabilities | `live_handoff: false`, `detached_server_daemon: false` |

Environment variables present inside a Herdr pane:

```
HERDR_ENV=1
HERDR_SOCKET_PATH=C:\Users\bramd\AppData\Roaming\herdr\herdr.sock
HERDR_WORKSPACE_ID=wA
HERDR_TAB_ID=wA:t1
HERDR_PANE_ID=wA:p1
```

Two `herdr.exe` processes run: a client and a server. `herdr status server` reports the live one.

### Transport

The file at `HERDR_SOCKET_PATH` is **not** a socket. It is 25 bytes of `<server_pid>:<token>`,
e.g. `19792:1785935212961784300`. There is no TCP listener on that port number — it is the PID.

The real transport is a **named pipe** whose name embeds the whole path:

```
\\.\pipe\C:\Users\bramd\AppData\Roaming\herdr\herdr.sock
```

A second pipe, `...\herdr-client.sock`, belongs to the client and is not ours to use.

The API surface is 90 methods. Full list in `herdr-api.schema.json`; the ones this project
uses are `ping`, `session.snapshot`, `agent.list`, `agent.focus`, `agent.send_keys`,
`agent.explain`, `events.subscribe`.

## KM16

**Flashed** with patched RawMacroPad firmware on 2026-08-08. Now enumerates as:

| | |
| --- | --- |
| VID / PID | `0x1209` / `0x88BF` |
| Manufacturer | `Topten Software` |
| Product | `RAW HID Macropad` |
| Usage page / usage | `0xff00` / `0x0001` (vendor-defined) |
| Interfaces | 1 |

Before flashing it was the stock `0x5343` / `0x0080`, product string `KM16`, a composite
device with 4 HID interfaces. `KM16.stock_firmware_present()` still checks for those IDs so
the daemon can say something useful if the pad is ever reverted.

In DFU mode it appears as `1eaf:0003` "SmartBoot", serial `LLM 003`. Windows binds no driver
to it by default, so `dfu-util` finds it but fails with `LIBUSB_ERROR_NOT_SUPPORTED` until
WinUSB is attached with Zadig.

`hidapi` 0.15.0 sees the device fine from the venv with no extra driver work — that applies to
the HID interfaces only. DFU is a different matter and needs WinUSB via Zadig.

## RawMacroPad upstream

Inspected at commit `ead652e9597a0ccada7d5fa720c26cff0e8b416e` (2026-02-20).

Two bugs, both confirmed at that revision.

**Client bug** (the one the handoff predicted): the Python and Node clients send command
`0x04` (set whole chain to one colour) plus an index byte, where the protocol spec and the
firmware both define `0x06` for a single LED. Verified against `readme.md`,
`python/raw_macro_pad.py` and `firmware/km16/km16.ino`.

**Firmware bug** (found here, undocumented upstream): `case 0x06:` in `km16.ino` has no
`break;` and falls through into `case 0xFF: NVIC_SystemReset()`, so a correct single-LED
write reboots the MCU. Symptom is `OSError('read error')` about a second later as the device
re-enumerates. Patched by `firmware/patches/0001-*.patch`; the flash script refuses to build
without it. The two bugs mask each other -- upstream's client never reaches the broken case,
so fixing the client is what exposes the firmware.

**LED chains default to off.** `KM16.h` has three independent switches -- master power
(`0x02`, PB14), key chain and underglow (`0x03` per chain, PB13/PB12) -- and all start
`false`. `setKeyLed()` only marks the frame dirty when its chain is enabled, so with only the
master on, colours are stored and silently never shifted out. `KM16.power_on_leds()` sends
all three in order.

Firmware buffer indexing, for cross-checking packet builders: the firmware's `buf[n]`
corresponds to host packet byte `n+1`, because the host prepends a `0x00` report ID.
Command `0x06` reads `buf[1]`=chain, `buf[2]`=index, `buf[3..5]`=RGB.

`KM16::setKeyLed` applies an internal `ledmap[]`, so the host addresses **logical** LED
indices 0..15 and the firmware handles the non-obvious physical wiring order.

Encoder turn events are emitted as `onEncoder(16|17|18, delta)` — the same indices as the
encoder push buttons, confirmed in `firmware/km16/KM16.h`.
