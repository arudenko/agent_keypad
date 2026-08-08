# MMD KM16 + Herdr + Claude Code Physical Agent Controller

**Handoff date:** 2026-08-08  
**Goal:** Turn an MMD KM16 macropad into a bidirectional physical control surface for multiple Claude Code CLI agents running inside Herdr.

This document is intended to be pasted or uploaded into a fresh LLM coding session. The next LLM should use it as project context, verify the local environment, then implement and configure the system end to end.

---

## 1. User context and desired result

The user runs most coding agents as **Claude Code CLI sessions inside Herdr**.

The user has purchased:

- **MMD KM16 Prebuilt Macropad**
- Silver version
- MMD Princess V2 tactile switches
- 16 keys in a 4x4 grid
- 3 clickable rotary encoders
- RGB lighting
- USB-C
- Hotswap PCB
- VIA compatible in stock form

The user bought the silver tactile version because they prefer the silver case. The switches are hot-swappable, so they can replace the tactile switches with linear switches later without changing the case.

The target is not merely keyboard macros. The target is a **bidirectional Herdr agent controller**:

1. Physical key presses control/focus Herdr agents.
2. Each key's RGB reflects the mapped agent's state.
3. Rotary encoders provide navigation and actions.
4. The software should keep working regardless of which desktop application currently has keyboard focus.
5. It should recover cleanly when Herdr or the controller process restarts.

Example:

```text
Herdr agent state       KM16 key
---------------------------------------
working                 blue
blocked                 red / pulsing red
done                    green
idle                    dim white
unknown                 amber
no mapped agent         off
```

---

# 2. Hardware facts

## MMD KM16

Verified hardware details from the RawMacroPad reverse-engineering project:

- MCU: STM32F103 ARM Cortex-M3 or similar
- 128 KB flash
- 20 KB RAM
- 72 MHz
- Bootloader ID: `1eaf:0003`
- STM32duino "Maple" bootloader
- 16 mechanical keys in a 4x4 grid
- 3 rotary encoders, each with push button
- 16 individually controllable per-key RGB LEDs
- 6 underglow RGB LEDs
- 1 layer indicator LED

RawMacroPad key mapping:

- Main keypad keys: indexes `0..15`
- Main encoder push: key index `16`
- Small left encoder push: key index `17`
- Small right encoder push: key index `18`

Encoder rotation events use the corresponding encoder index.

LED chains:

- Chain 0: 16 under-key LEDs
- Chain 1: 6 underglow LEDs
- Chain 2: 1 layer indicator

Important: the physical LED order is not necessarily a trivial row-major electrical order. Use the RawMacroPad logical mapping rather than guessing.

Source:

https://github.com/toptensoftware/rawMacroPad

Hardware notes:

https://github.com/toptensoftware/rawMacroPad/blob/main/notes/km16_hardware_spec.md

PantheonKeys product page:

https://pantheonkeys.com/products/mmd-km16-prebuilt-macropad-kit

PantheonKeys confirms:

- anodized aluminium top case
- polycarbonate bottom
- gasket mount
- **hotswap PCB**
- USB-C
- RGB back/accent light
- VIA compatible

---

# 3. Recommended firmware: RawMacroPad

Use the open-source **RawMacroPad** firmware by Topten Software rather than relying on stock VIA/QMK mappings.

Repository:

https://github.com/toptensoftware/rawMacroPad

Why:

- RAW HID interface
- bypasses normal OS keyboard focus
- host application gets key press/release events directly
- host application gets encoder rotation events directly
- host can directly control RGB LEDs
- avoids writing LED changes repeatedly into device flash
- supports a watchdog that can show a flashing red indicator if the host daemon dies
- project includes Node.js and Python clients
- currently specifically supports the MMD KM16

Raw HID VID/PID after flashing:

```text
VID 0x1209
PID 0x88BF
```

Packets are 64 bytes.

---

# 4. CRITICAL safety step before flashing

**Do not flash immediately.**

The RawMacroPad author explicitly warns that the firmware was reverse engineered from one specific MMD KM16 and that units sold under the same name can theoretically use different hardware revisions.

The next LLM/operator must:

1. Connect the KM16.
2. Test stock functionality.
3. Save screenshots/notes of the stock VIA configuration if useful.
4. Put the KM16 into bootloader mode.
5. Verify that the expected STM32 bootloader is present.
6. Back up the original firmware.
7. Verify the backup file size.
8. Store the backup somewhere outside the repo before flashing.

Bootloader mode documented by RawMacroPad:

> Hold the **top-left key** while plugging in USB.

Install `dfu-util`, then:

```bash
dfu-util -l
```

Expected family of output:

```text
Found DFU: [1eaf:0003] ...
name="STM32duino bootloader v1.0 ..."
```

If the unit does **not** present as `1eaf:0003` / expected STM32duino bootloader, STOP and investigate the hardware revision before flashing.

Back up the stock firmware:

```bash
dfu-util -d 1eaf:0003 -a 2 -U original_firmware.bin
```

The project's documented successful backup is:

```text
Received a total of 122880 bytes
```

and the resulting file should be approximately:

```text
120 KB
```

`dfu-util` may print `LIBUSB_ERROR_PIPE` at the end of the read. The project documentation says this is expected if the full 122880 bytes were received.

Keep `original_firmware.bin` safe.

Restore command if necessary:

```bash
dfu-util -d 1eaf:0003 -a 2 -D original_firmware.bin
```

---

# 5. Building/flashing RawMacroPad

Repository:

```bash
git clone https://github.com/toptensoftware/rawMacroPad.git
cd rawMacroPad
```

Install:

- `dfu-util`
- `arduino-cli`

Install STM32 Arduino board support:

```bash
arduino-cli config add board_manager.additional_urls \
  https://github.com/stm32duino/BoardManagerFiles/raw/main/package_stmicroelectronics_index.json

arduino-cli core update-index
arduino-cli core install STMicroelectronics:stm32
```

Put KM16 into bootloader mode again by holding the top-left key while plugging in USB.

On Linux/macOS or Windows Git Bash, the project provides:

```bash
cd firmware/km16
./build-and-flash
```

Equivalent manual build:

```bash
arduino-cli compile \
  --fqbn STMicroelectronics:stm32:GenF1:pnum=BLACKPILL_F103CB,upload_method=dfu2Method,xserial=generic,usb=none \
  --export-binaries .
```

Flash:

```bash
dfu-util -d 1eaf:0003 -a 2 \
  -D build/STMicroelectronics.stm32.GenF1/firmware.ino.bin
```

After a successful flash, RawMacroPad says the device should reboot and the layer indicator should flash red because the watchdog has no connected client yet.

---

# 6. Important bug/inconsistency discovered in RawMacroPad client libraries

The RawMacroPad protocol README and the KM16 firmware agree that:

```text
0x06 = Set Individual LED
```

Firmware:

```cpp
case 0x06:
    // Set a single LED
```

However, as of 2026-08-08, both the repository's **Python and Node.js `setLed` implementations appear to send command `0x04` instead of `0x06`**.

Python currently contains approximately:

```python
def set_led(self, chain, index, color):
    self._buf[1] = 0x04
```

Node currently contains approximately:

```javascript
setLed(chain, index, color) {
    this.#buf[1] = 0x04;
}
```

But command `0x04` is documented and implemented in firmware as "set entire LED chain to a single color".

This looks like a library bug.

Before relying on `setLed()`:

1. Inspect the latest repository HEAD in case this has been fixed.
2. If still present, patch the client to use `0x06`.
3. Add a regression test.
4. Alternatively use the "set LED chain to color array" command `0x05`, which can update all 16 key LEDs in a single packet and is actually attractive for this project.

For the Herdr controller, **prefer sending the entire 16-LED array with command `0x05`** when state changes. It keeps the whole physical display consistent and avoids many individual HID writes.

Relevant files:

https://github.com/toptensoftware/rawMacroPad/blob/main/python/raw_macro_pad.py

https://github.com/toptensoftware/rawMacroPad/blob/main/node/index.js

https://github.com/toptensoftware/rawMacroPad/blob/main/firmware/km16/km16.ino

---

# 7. Herdr integration facts

Herdr should be the software integration layer, not Claude Code directly.

Herdr already:

- knows which panes contain coding agents
- supports Claude Code
- exposes agent state
- can focus an agent
- can send keys to an agent
- can prompt agents
- can read agent output
- can list/get agents
- exposes a local socket API
- supports event subscriptions
- exposes a complete session snapshot
- has state values suitable for LEDs

Herdr states:

```text
idle
working
blocked
done
unknown
```

Important Herdr semantics:

- `blocked`: Herdr detected an approval/question/permission UI.
- `done`: underlying idle state after background work completed and before the user has viewed/focused it.
- focusing a `done` agent can make it become `idle` because it is now seen.
- `unknown`: an agent exists but Herdr cannot classify its lifecycle confidently.
- Claude Code currently uses **screen manifest detection** for state rather than a complete lifecycle hook authority.

That last point matters. Claude Code's visible UI can change. A new prompt shape may temporarily be classified as `idle` instead of `blocked`.

If state detection looks wrong:

```bash
herdr agent explain <target>
```

and update/reload agent manifests as needed.

Herdr agent docs:

https://herdr.dev/docs/agents/

Herdr automation docs:

https://herdr.dev/docs/agent-automation/

Herdr socket API:

https://herdr.dev/docs/socket-api/

---

# 8. Herdr CLI capabilities useful to this project

Examples from Herdr docs:

Focus/control should use **agent operations**, not raw keyboard injection whenever possible.

Relevant commands include:

```bash
herdr agent get <agent-or-pane>
herdr agent read <agent-or-pane>
herdr agent focus <agent-or-pane>
herdr agent send-keys <agent-or-pane> esc
herdr agent send-keys <agent-or-pane> enter
herdr agent send-keys <agent-or-pane> ctrl+c
herdr agent prompt <agent-or-pane> "..."
herdr agent wait <agent-or-pane> --until blocked
herdr agent explain <agent-or-pane>
```

The API exposes:

```text
agent.list
agent.get
agent.read
agent.send_keys
agent.prompt
agent.wait
agent.rename
agent.focus
agent.start
```

For MVP debugging, use the CLI wrappers.

For the final controller daemon, use the **raw socket API** because it supports:

- one-time bootstrap via `session.snapshot`
- long-lived event subscriptions
- low-latency updates
- no need to spawn a process on every state refresh

---

# 9. Herdr socket API design

Transport:

- newline-delimited JSON
- Unix: Unix domain socket
- Windows: named pipe

Default Unix socket:

```text
~/.config/herdr/herdr.sock
```

Named session:

```text
~/.config/herdr/sessions/<name>/herdr.sock
```

Resolution order used by Herdr:

1. explicit `--session`
2. `HERDR_SOCKET_PATH`
3. `HERDR_SESSION`
4. default session

The installed Herdr binary can output the exact protocol schema matching the installed version:

```bash
herdr api schema
herdr api schema --json
herdr api schema --output herdr-api.schema.json
```

**Do this instead of hard-coding assumptions from online docs.**

Bootstrap:

```text
session.snapshot
```

This returns a one-time snapshot including:

- protocol/version metadata
- focused workspace/tab/pane
- workspaces
- tabs
- panes
- layouts
- agent records

Then subscribe to events.

Relevant event type:

```text
pane.agent_status_changed
```

Example documented subscription:

```json
{
  "id": "sub_1",
  "method": "events.subscribe",
  "params": {
    "subscriptions": [
      {
        "type": "pane.agent_status_changed",
        "pane_id": "w1:p1",
        "agent_status": "blocked"
      }
    ]
  }
}
```

The API also emits pane lifecycle events such as:

```text
pane.created
pane.updated
pane.closed
pane.focused
pane.moved
pane.exited
pane.agent_detected
pane.agent_status_changed
```

For a robust controller:

1. connect
2. `session.snapshot`
3. build local state cache
4. subscribe to relevant pane/agent status and lifecycle events
5. update local cache
6. recompute mapping/LED frame
7. send one 16-LED frame to KM16 only if changed
8. on disconnect, reconnect and take a fresh `session.snapshot`

---

# 10. Recommended controller architecture

Use a small local daemon:

```text
             +---------------------+
             |       Herdr         |
             | Claude Code agents  |
             +----------+----------+
                        |
                 local socket API
                        |
                        v
             +---------------------+
             | herdr-km16 daemon   |
             |                     |
             | agent state cache   |
             | key mapping         |
             | button actions      |
             | LED renderer        |
             +----------+----------+
                        |
                      RAW HID
                        |
                        v
             +---------------------+
             |      MMD KM16       |
             | 16 RGB keys         |
             | 3 encoders          |
             +---------------------+
```

Do not route normal KM16 input through the desktop keyboard stack unless there is a specific reason.

RAW HID gives us:

- deterministic behavior
- no application-focus dependency
- direct key-up/key-down events
- direct encoder events
- RGB feedback

---

# 11. Suggested language

**Python is recommended for the first implementation** because:

- Herdr socket protocol is simple newline-delimited JSON.
- RawMacroPad already contains a Python HID implementation.
- `asyncio` is a good fit for simultaneous HID input, Herdr event subscription and LED animation.
- It is easy to run as a local background service.

However, inspect the current RawMacroPad Python library before importing it directly because of the `set_led` command bug described above.

A clean option is to vendor a tiny, reviewed RawMacroPad transport module into the project.

---

# 12. Suggested project layout

```text
herdr-km16/
├── README.md
├── pyproject.toml
├── config.yaml
├── scripts/
│   ├── backup-km16-firmware.sh
│   ├── flash-rawmacropad.sh
│   └── restore-km16-firmware.sh
├── src/
│   └── herdr_km16/
│       ├── __init__.py
│       ├── main.py
│       ├── km16.py
│       ├── herdr.py
│       ├── mapping.py
│       ├── leds.py
│       ├── actions.py
│       └── config.py
└── tests/
    ├── test_led_protocol.py
    ├── test_agent_mapping.py
    └── test_herdr_events.py
```

---

# 13. Recommended UX / mappings

Do not hard-code the final UX too early. Make it configurable.

A sensible first version is:

## 16 main keys

Map each key to an active Herdr agent.

Key press:

```text
mapped agent -> herdr agent focus <agent>
```

If the slot is empty, do nothing.

Do **not** automatically approve a blocked prompt merely because the user pressed its agent key. A blocked state can represent a permission request or a question. Agent key press should focus the session first.

## Main encoder

Suggested:

```text
rotate left     previous agent needing attention
rotate right    next agent needing attention
press           focus selected agent
```

Attention priority:

1. blocked
2. done
3. working
4. idle
5. unknown

This should be configurable.

## Small left encoder

Possible default:

```text
rotate          scroll/cycle workspace or agent list
press           send Esc to selected/focused Herdr agent
```

## Small right encoder

Possible default:

```text
rotate          optional terminal scrolling / configurable action
press           Enter on selected/focused agent
```

For destructive or approval-like actions, consider:

- long press
- double press
- dedicated modifier mode

Avoid making a single accidental knob press blindly accept an unexpected Claude Code permission request.

---

# 14. Suggested LED scheme

Start with:

```text
blocked   = red
done      = green
working   = blue
idle      = dim white
unknown   = amber
empty     = off
selected  = brighter version or subtle pulse
```

Animations:

- blocked: slow red pulse
- done: solid green
- working: solid or subtle blue breathing
- idle: very dim
- selected: increase brightness, do not completely replace semantic state color

The LED rendering code should generate a 16-element RGB array and push it as a single frame using RawMacroPad command `0x05`.

Do not write LED state continuously at high frequency if nothing changed.

Animation loop can be around 5-10 Hz if pulsing is desired, but state-only changes can be event driven.

## Underglow

Optional global summary:

```text
any blocked        red
else any done      green
else any working   blue
else               dim/off
```

This gives peripheral attention feedback even without looking at individual key LEDs.

---

# 15. Stable agent-to-key mapping

Avoid naïvely re-sorting all agents on every state change. If keys constantly change which agent they represent, the device becomes confusing.

Preferred mapping algorithm:

1. If an agent already owns a key, retain it while the agent remains alive.
2. For new agents, allocate the first free key.
3. Persist mappings where useful.
4. Prefer stable Herdr agent names if present.
5. Otherwise use the live pane identity.
6. Handle `pane.moved`, because Herdr says moving across workspaces can change the public pane ID while retaining the running terminal.
7. On reconnect, rebuild mappings carefully from `session.snapshot`.

Possible config override:

```yaml
agent_slots:
  0: reviewer
  1: backend
  2: frontend
```

Allow remaining keys to be dynamically allocated.

---

# 16. Claude Code / Herdr state caveat

Herdr documentation states that Claude Code is detected with a screen manifest for lifecycle state.

This means:

- Herdr sees the foreground Claude process.
- It reads the live bottom-buffer screen snapshot.
- Manifest rules classify it as `idle`, `working`, or `blocked`.
- If a new/unusual Claude prompt is not in the manifest, Herdr can fall back to `idle`.
- This affects the LED, but should not cause the controller to send destructive input automatically.

Troubleshooting:

```bash
herdr agent explain <target>
```

To fetch/reload newer manifests:

```bash
herdr server update-agent-manifests
```

After a manual local override:

```bash
herdr server reload-agent-manifests
```

Keep this in mind if a physical key shows the wrong state.

---

# 17. First implementation milestones

The next LLM should implement this incrementally.

## Phase 0: inspect local environment

Determine without guessing:

```bash
uname -a
python --version
node --version
herdr --version
herdr api schema --json
```

Identify:

- OS
- Herdr version
- whether Herdr uses a named session
- Python environment
- KM16 USB enumeration

Do not make the user manually answer information that commands can detect.

## Phase 1: hardware safety

- test stock KM16
- enter bootloader
- verify `1eaf:0003`
- back up original firmware
- verify 122880-byte backup
- save backup safely

STOP if hardware identity differs.

## Phase 2: flash RawMacroPad

- clone repository
- install toolchain
- compile
- flash
- verify Raw HID `1209:88BF`
- verify watchdog indicator behavior

## Phase 3: standalone hardware test

Create a tiny test app that:

- connects to KM16
- keeps watchdog alive
- prints all key press/release events
- prints all encoder events
- turns all LEDs off
- lights each key one by one
- exercises all 16 LEDs with distinct test colors
- tests the six underglow LEDs
- confirms the three encoder push buttons

Patch the `set_led` bug if still present.

Do not proceed until hardware input and LED output are proven.

## Phase 4: standalone Herdr test

Using CLI first:

- list/get active agents
- inspect their status
- focus a named agent
- send a harmless key such as `esc`
- inspect `session.snapshot`
- dump the installed schema

Then create a socket client that:

- sends `ping`
- requests `session.snapshot`
- subscribes to `pane.agent_status_changed`
- logs events

## Phase 5: integrate

- map live Herdr agents to KM16 slots
- render LEDs
- key press focuses mapped agent
- encoder navigation works
- recover after Herdr reconnect
- recover after USB disconnect/reconnect

## Phase 6: polish

- YAML config
- startup service
- logging
- graceful shutdown
- tests
- configurable colors/brightness
- optional pulse animations
- optional underglow summary
- configurable action mappings
- device reconnect
- Herdr reconnect
- protect against accidental repeated Enter / Ctrl+C

---

# 18. Acceptance criteria

The project is done when all of these work:

1. KM16 boots into RawMacroPad reliably.
2. Original firmware backup exists and restoration procedure is documented.
3. The daemon starts without requiring the Herdr window to be focused.
4. Every key event is captured through RAW HID.
5. All 16 under-key LEDs can be independently controlled.
6. All 3 rotary encoders and their push buttons are captured.
7. The daemon discovers current Herdr agents.
8. An agent is assigned to a stable key slot.
9. Pressing the key focuses that agent in Herdr.
10. `working`, `blocked`, `done`, `idle`, and `unknown` produce distinct LED states.
11. A Herdr state change updates the relevant key quickly.
12. A newly started agent appears on a free key.
13. An exited agent releases its slot.
14. A Herdr restart reconnects without restarting the entire machine.
15. A KM16 unplug/replug reconnects cleanly.
16. No blocked request is auto-approved solely because the user selects/focuses its key.
17. The controller can be configured without editing core source.
18. There are unit tests for key-to-agent mapping and LED protocol generation.

---

# 19. Suggested config format

Example only:

```yaml
herdr:
  session: null
  reconnect_seconds: 1

km16:
  watchdog_ms: 2000
  brightness: 0.35
  underglow: true

colors:
  working: "#0066ff"
  blocked: "#ff0000"
  done: "#00ff44"
  idle: "#202020"
  unknown: "#ff9900"
  empty: "#000000"

mapping:
  preserve_slots: true
  static:
    # "0": reviewer
    # "1": backend

controls:
  key_press: focus_agent

  main_encoder:
    rotate: cycle_attention_agents
    press: focus_selected

  left_encoder:
    rotate: cycle_agents
    press: escape

  right_encoder:
    rotate: none
    press: enter
```

The exact behavior should remain configurable.

---

# 20. Service/startup

Once stable, run the daemon automatically.

Implement the startup method appropriate to the detected OS:

- Linux: user `systemd` service
- macOS: LaunchAgent
- Windows: Task Scheduler or a suitable user service wrapper

The daemon should:

1. tolerate Herdr not running yet
2. tolerate KM16 not plugged in yet
3. reconnect rather than exit
4. log useful errors
5. use RawMacroPad watchdog so hardware visibly indicates when the daemon is dead

---

# 21. Security / safety

This controller can send input to coding agents, which can execute shell commands.

Therefore:

- selecting/focusing an agent must be safe
- do not auto-approve arbitrary `blocked` states
- require an explicit action for Enter/approval
- debounce physical keys
- consider long-press for dangerous actions
- do not run shell snippets constructed from untrusted agent names
- use structured Herdr socket requests or subprocess argument arrays
- avoid `shell=True`
- validate config
- log all control actions at least while developing

---

# 22. References

## Hardware / firmware

RawMacroPad repository:

https://github.com/toptensoftware/rawMacroPad

RawMacroPad README:

https://github.com/toptensoftware/rawMacroPad/blob/main/readme.md

KM16 hardware reverse engineering:

https://github.com/toptensoftware/rawMacroPad/blob/main/notes/km16_hardware_spec.md

KM16 firmware implementation:

https://github.com/toptensoftware/rawMacroPad/blob/main/firmware/km16/km16.ino

Python RAW HID client:

https://github.com/toptensoftware/rawMacroPad/blob/main/python/raw_macro_pad.py

Node RAW HID client:

https://github.com/toptensoftware/rawMacroPad/blob/main/node/index.js

PantheonKeys MMD KM16:

https://pantheonkeys.com/products/mmd-km16-prebuilt-macropad-kit

## Herdr

Agents:

https://herdr.dev/docs/agents/

Agent automation:

https://herdr.dev/docs/agent-automation/

Socket API:

https://herdr.dev/docs/socket-api/

---

# 23. Prompt for the next LLM

Use the following as the opening instruction after attaching this document:

> I have the MMD KM16 described in the attached handoff and I run multiple Claude Code CLI agents inside Herdr. I want you to implement the controller described here end to end. Work incrementally and inspect my local environment rather than guessing. First verify my KM16 hardware/bootloader and make a recoverable stock firmware backup before any firmware write. Then flash/test RawMacroPad, build a Python Herdr/KM16 daemon, and configure it for dynamic per-agent RGB and physical control. Use the installed `herdr api schema --json` as the source of truth for my Herdr version. Pay particular attention to the apparent RawMacroPad `setLed()` 0x04 vs 0x06 bug documented in this handoff. Do not auto-approve blocked Claude prompts just because I select an agent. Keep the implementation in a clean Git repo with tests and a README.

