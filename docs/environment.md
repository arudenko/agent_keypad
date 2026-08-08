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
| dfu-util | not installed |
| arduino-cli | not installed |

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

Currently on **stock firmware**, enumerating as:

| | |
| --- | --- |
| VID / PID | `0x5343` / `0x0080` |
| Product string | `KM16` |
| Instance | `USB\VID_5343&PID_0080\...` |
| Interfaces | composite; 4 HID interfaces (MI_00..MI_03) |

After flashing RawMacroPad it becomes RAW HID `0x1209` / `0x88BF`.

`hidapi` 0.15.0 sees the device fine from the venv with no extra driver work — that applies to
the HID interfaces only. DFU is a different matter and needs WinUSB via Zadig.

## RawMacroPad upstream

Inspected at commit `ead652e9597a0ccada7d5fa720c26cff0e8b416e` (2026-02-20).

The `set_led()` bug described in the handoff is **still present** in both the Python and Node
clients: they send command `0x04` (set whole chain to one colour) plus an index byte, where the
protocol spec and the firmware both define `0x06` for a single LED. Verified against
`readme.md`, `python/raw_macro_pad.py` and `firmware/km16/km16.ino`.

Firmware buffer indexing, for cross-checking packet builders: the firmware's `buf[n]`
corresponds to host packet byte `n+1`, because the host prepends a `0x00` report ID.
Command `0x06` reads `buf[1]`=chain, `buf[2]`=index, `buf[3..5]`=RGB.

`KM16::setKeyLed` applies an internal `ledmap[]`, so the host addresses **logical** LED
indices 0..15 and the firmware handles the non-obvious physical wiring order.

Encoder turn events are emitted as `onEncoder(16|17|18, delta)` — the same indices as the
encoder push buttons, confirmed in `firmware/km16/KM16.h`.
