# Stock firmware backup

Taken 2026-08-08, before any write to the device. The binary itself is deliberately **not**
version controlled (`.gitignore` blocks `*.bin`) — this file records where it lives and how to
prove a copy is intact.

## Provenance

| | |
| --- | --- |
| Device | MMD KM16, silver, MMD Princess V2 tactile |
| Bootloader | `1eaf:0003`, STM32duino bootloader v1.0, `ver=0201`, serial `LLM 003` |
| Read with | `dfu-util 0.11`, `-d 1eaf:0003 -a 2 -U` |
| Size | 122880 bytes (exactly the documented value) |
| SHA256 | `AD9DFD86869841F21C1D37DCF7FF28BFF632929FD6A95A32B0CCD1445B1FCF32` |

## Copies

- `%USERPROFILE%\km16-firmware-backup\original_firmware.bin` (primary)
- a second copy kept off the machine (cloud sync, another drive) (offsite)

## Verifying a copy

```powershell
$p = "$env:USERPROFILE\km16-firmware-backup\original_firmware.bin"
(Get-Item $p).Length            # must be 122880
(Get-FileHash $p -Algorithm SHA256).Hash
```

The image was sanity-checked beyond its length: the first word is `0x20000400`, a stack
pointer in SRAM, and the second is `0x08002239`, a reset vector in flash at the `0x8002000`
offset the bootloader uploads from. 95.9% of bytes are non-zero. That is a real Cortex-M
vector table, not a blank or truncated read.

## Restoring

Hold the top-left key while plugging in USB, then:

```powershell
.\scripts\restore-km16-firmware.ps1
```

The pad returns to stock and works with VIA again. WinUSB is bound only to the bootloader
device (`1eaf:0003`), so it does not interfere with normal operation either way.
