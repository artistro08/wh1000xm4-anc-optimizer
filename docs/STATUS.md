# Project Status and Evidence Ledger

Last reviewed: 2026-10-26. This file is the single place where claims about the project are graded by evidence. If another document disagrees with this file, this file wins until the other document is fixed.

## Evidence levels

| Label | Meaning |
|---|---|
| **Device-verified** | Observed on a real WH-1000XM4 (`MDRID294300`): a tool log, a flash readback, or a dump. The raw logs and dumps are kept by the maintainer and are not published, because dumps contain personal data (see [Privacy](#privacy-what-an-nvdm-dump-contains)). |
| **User-reported** | A listening observation by the device owner. Real evidence, but subjective and not measured. |
| **Code/simulation** | Covered by the test suite or an offline simulation. Says nothing about the real device. |
| **Unverified** | Stated somewhere earlier in the project without a source or measurement. Do not rely on it. |
| **Contradicted** | Evidence exists against it. |

## Hardware access

| Claim | Level | Evidence / note |
|---|---|---|
| RFCOMM channel 11 speaks Sony MDR; channel 21 speaks Airoha RACE (`0x05 0x5A` frames) | Device-verified | Backup and firmware flash both completed over these channels (2026-10-25). |
| A 512 KiB NVDM store (128 × 4 KiB blocks) can be read with RACE `0x0403` page reads, `storage_type=1`, addresses starting at 0 | Device-verified | `tools/backup_nvdm.py`. All 128 blocks start with the `NVDM` magic. |
| NVDM reads need the MDR update-mode handshake first | **Contradicted** | Full 512 KiB reads without it worked three times on 2026-10-26 (about 70 s while streaming, 22 s idle); the owner reported that video playback was not interrupted. |
| NVDM lives in "Partition 6" at flash address `0x000DF000`, size 128 KB | Unverified / partly contradicted | The working tool uses logical NVDM addressing, not a physical flash address, and the store it reads is 512 KiB, not 128 KB. The physical location is unknown. |
| NVDM item header layout and checksum | Device-verified | The repository parser (`python3 -m xm4_optimizer parse <dump>`) validates the checksum and name hash of all 230 active items in a real dump. The corrected layout is in [HARDWARE_AND_NVDM.md](HARDWARE_AND_NVDM.md). |
| 16 MB NOR flash, bootloader / FOTA / voice-prompt address map | Unverified | No partition table read has been published. |
| The Sony control service is UUID `96cc203e-5068-46ad-b32d-e316f5e069ba` with the MDR v1 framing used by [Gadgetbridge](https://codeberg.org/Freeyourgadget/Gadgetbridge) | Device-verified | Live test 2026-10-26: firmware version request returned `"2.5.0"`, battery returned 80%, ambient-sound GET returned the current mode, and SET to ambient (level 10) and back to noise cancelling was confirmed by readback. |
| Channel 11 (`b9b213ce…`, "Serial MC") is the control channel | **Contradicted** | Live test 2026-10-26: it sends a hello frame and ACKs every frame, but answers no GET request. The backup tool uses it only for the update-mode handshake. |
| CLI `anc` and `status` | Device-verified (fixed 2026-10-26) | The old CLI sent to channel 11 with message type `0x00` and had no effect. `xm4_optimizer/transport/sony_control.py` now finds the control service by SDP (`transport/sdp.py`) and reads back every change. Tested: firmware `2.5.0`, battery, noise cancelling, ambient (levels 8, 10, 15; focus on voice on/off), wind noise reduction, off. The headset reports ambient level 1 when level 0 is requested, and keeps its focus-on-voice flag when leaving ambient mode. |
| RFCOMM channel of the control service | Device-verified | SDP on this unit: control `96cc203e…` = channel 9, `b9b213ce…` = 11, Airoha `8901dfa8…` = 21. The V2 service `956c7b26…` is not advertised. Channels can differ between models and firmware; always resolve by SDP. |
| `bluetoothctl info` Modalias encodes the firmware version | Partly verified | `usb:v054Cp0D58d0250` on a unit whose MDR firmware string is `2.5.0`. Other versions not checked. |
| RACE SDK version (`0x0301`) and build version (`0x1E08`) queries | No reply | Live test 2026-10-26 on firmware 2.5.0, after the update-mode handshake: no response, while a page read on the same connection worked. |
| RACE page-read response layout | Device-verified | 270-byte frame: 6-byte header + 8 bytes (status `0x00`, …, address) + 256 bytes of data. Data starts at byte 14. |
| Firmware signature is RSA-2048 checked against eFuses | Unverified | No source. The flasher only shows that the device ran an integrity check (`0x1C01`) and accepted an official image. |

## ANC calibration (`inject`, `verify`, `rollback`)

| Claim | Level | Evidence / note |
|---|---|---|
| `DNC/ALIGN_FF_L/R`, `DNC/ALIGN_FB_L/R` are 108-byte blocks holding five Q4.28 biquads | **Contradicted** | In a real dump these items are in group `CXD90050`, not `DNC`, and each value is 8 bytes of ASCII hex text. |
| `DNC/HOWLING_PRE_PARAM`, `DNC/HOWLING_DET_PARAM`, `DNC/OPTIMIZER_PARAM` exist | **Contradicted** | None of these items exists in the dump. A 1-byte `DNC/howling_pre_notice_off` exists. |
| A 40-byte `config/optimizer_coeff` item exists; it holds NC Optimizer output | Device-verified | Firmware strings: `[AC][optimizer] generation end! %x` ×10 and "Cannot write optimizer coeff to nvdm". Two older, deleted records hold real values. Decoded as 2 stages × `[b0, b1, b2, −a1, −a2]`, signed Q4.28, big-endian, stage 1 is a stable low-frequency shelf (+3.84 dB at 20 Hz, +2.39 dB at 50 Hz, +0.53 dB at 100 Hz, 0 dB above 500 Hz; pole radius 0.995) and stage 2 is near identity. The layout is an interpretation supported by these plausible, stable responses. All other records, including the active one, are identity. |
| Starting NC Optimizer over MDR (`84 01 00 01`) | Device-verified | 2026-10-26 on firmware 2.5.0: status messages `0x85` went WEARING_CONDITION → ATMOSPHERIC_PRESSURE → ANALYZING → FINISHED → NOT_RUNNING in about 15 s (status names from Gadgetbridge). Pressure reported 1.0 atm. |
| NC Optimizer result is stored immediately | **Contradicted** | Nothing in the NVDM changed after the run. After a power cycle `optimizer_coeff` was rewritten, still identity. |
| When `optimizer_coeff` is written | Device-verified | It is the first item of a batch the firmware writes at power off (sequence numbers directly before volume, ANC mode, `nc_atm_value`, `nc_is_optimized`, `power_off_reason`, …). So the firmware saves its RAM value at power off: a value written directly into the NVDM while the headset runs would be overwritten at the next power off. `nc_is_optimized` is `"1"` in all 32 records. |
| NVKEY read with RACE `0x0A00` | Device-verified | Four `AB15/<key>` items read live matched the dump byte for byte. Command format from [lx-exe/stealth600-gen3-usb-fix](https://github.com/lx-exe/stealth600-gen3-usb-fix). `config/*` items are not NVKEYs; a RACE command that writes them is not known yet. |
| Writing a new `config/optimizer_coeff` over RACE works | **Contradicted** | The only device attempt (2026-10-25) failed its readback check (`Readback mismatch at 0x00021E00`). The active value in a later dump is still the identity value. |
| "+6.5 to +7.5 dB extra low-frequency noise reduction" | Code/simulation | This is the simulated magnitude response of the synthesized filter. It is not an ANC measurement. Real noise reduction depends on the acoustic paths, which were never measured. |
| A bad NVDM checksum cannot bootloop the headset (ROM fallback) | Unverified | No source or test. |
| Rollback restores the dump "bit-exact" | Unverified | The rollback path only issues page-program commands. NOR flash cannot turn 0 bits back into 1 without an erase, and the emulator in `virtual_device.py` hides this. |
| `AT+HWPTOOL=COEFSET` / `AT+ENVDM` ATCI commands | Unverified | These strings exist in the firmware v3.0.1 image. Their argument format and whether retail firmware accepts them over RACE are unknown. |

Code facts about the old write paths (confirmed by reading the code after a user report, 2026-10-26):

- `inject` reads the NVDM through raw flash reads (`storage_type=0`) at `0x000DF000`. On a real headset this returns error `0x03` before anything is written.
- The injector updates an item in place only when the new value has the same size as the old one. The real `ALIGN_*` values are 8 bytes, the injector's are 108 bytes, so it would fall back to re-serializing every active item. It also requires a 128 KB image, while a real backup is 512 KiB.
- `rollback` accepts only 128 KB images, so it rejects a real 512 KiB backup.
- `--dry-run --image <real dump>` finds no 108-byte `ALIGN_*` block, falls back to an identity filter, and prints the same table as the README example. The "+7.5 dB" is the computed gain of the synthesized filter, never a measurement on a unit.

Because of these findings, the CLI refuses `inject` and `rollback` on real hardware unless `--allow-unverified-write` is given. `--mock` still runs them against the emulator.

## Firmware flashing

| Claim | Level | Evidence / note |
|---|---|---|
| Official firmware v2.5.0 can be flashed over Bluetooth with `tools/flash_firmware.py`, downgrading from v3.0.1 | Device-verified | 2026-10-25: 5,452 pages (1,395,504 bytes) programmed, integrity check `0x1C01` passed, commit `0x1C02`, device rebooted. |
| After the downgrade, ANC sounded stronger and the hiss was gone | User-reported | No measurement. |
| Sony reduced ANC by 4–6 dB in later firmware | Unverified | No measurement or source. Treat it as a hypothesis. |
| Brick risk is "none" with official firmware | Unverified | No source. The flasher does not yet check the input file against known hashes (planned). |

### Where the v2.5.0 file comes from

The project does not host Sony firmware. The v2.5.0 image used on the test device came from the community archive [lzghzr/MDR_Proxy](https://github.com/lzghzr/MDR_Proxy) (MIT). Its `mdrdownload.ts` downloads firmware from Sony's own update server (`info.update.sony.net`).

| Field | Value |
|---|---|
| Path in that repository | `firmware/2943/MDRID294300/UPG_2.5.0_20210413_enc.97529f0f6630238e77ce76bfc6766aa5616e78e7.bin` |
| Added in | commit `4724e44`, 2022-12-11 |
| Size | 1,395,504 bytes |
| SHA-1 | `97529f0f6630238e77ce76bfc6766aa5616e78e7` (also embedded in the file name) |
| SHA-256 | `ee2a4b60c5f322d56fd9e6ea51778cbbc847f816781c385f40d45959555ac508` |

The same directory also holds 2.6.0, 2.7.1, 2.9.0 and 3.0.1. `MDRID294301`–`294303` contain only pointer files ("same as MDRID294300").

## Voice prompts

| Claim | Level | Evidence / note |
|---|---|---|
| A custom voice pack (Disco Elysium) was installed and heard | Device-verified / user-reported | Install script reported success (2026-10-25) and the owner heard the prompts. The script is not in this repository yet. |
| Safer route: the same upload sequence the Sony app uses for voice packs | Source | [aaron-p1/fix-whxm4-voice](https://github.com/aaron-p1/fix-whxm4-voice) (MIT). Its `docs/adr/0001-direct-bluetooth-upload.md` says "stray RACE flash writes are known to brick devices" and that its tool refuses writes outside the FOTA partition. The same file says "Firmware cannot be downgraded". Our v2.5.0 log above contradicts that for the FOTA flasher used here. Voice-pack support is planned for the CLI. |

## Spatial audio (AeroSpatial, Linux)

| Claim | Level | Evidence / note |
|---|---|---|
| Stereo is rendered as two virtual speakers with Steam Audio HRTFs | Code/simulation | C++ tests in `aerospatial/tests`. |
| Variable PipeWire block sizes no longer drop samples | Code/simulation | Ring-buffer FIFO and `test_variable_quantum_continuity` (2026-10-26). |
| First live Spotify trial sounded muffled, lost the EQ and collapsed to the center | User-reported | The routing script bypassed EasyEffects; this was fixed in the script. A new live listening test is still pending. |
| At ±30° a deep notch near 10 kHz appears; ±60° removes it | Reported by a previous session | The measurement script is not in the repository and has not been reproduced. |
| "Sub-2 ms deterministic latency" | Unverified | Default block is 256 samples at 48 kHz = 5.33 ms. Nothing has been measured end to end. |
| Content-aware or "real" 3D (instrument positions from stereo) | Not provided | Stereo input cannot recover object positions. See [SPATIAL_ROADMAP.md](SPATIAL_ROADMAP.md). |

## Privacy: what an NVDM dump contains

A real dump contains items such as `production/bt_addr`, `production/model_serial`, `config/device1_addr`, `config/device2_addr` and `BT/paired_00` … `BT/paired_03` (70 bytes each, the pairing records of phones and computers). Never post a raw dump publicly. A redaction step is planned before any "share your dump" workflow is offered.

## Known documentation and repository issues fixed on 2026-10-26

- License: the repository is GPL-3.0-or-later. README and `pyproject.toml` wrongly said MIT.
- README preset paths pointed to files that do not exist.
- The README test count (407) was outdated. The suite currently has 427 tests.
- Turkish documents were translated to English. Agent working notes were removed from the repository; their verified content is summarized here.
