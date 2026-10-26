# Hardware and NVDM Format (Sony WH-1000XM4)

This document describes what is known about the WH-1000XM4 storage that the tools read. Every statement carries an evidence level, using the labels defined in [STATUS.md](STATUS.md). The format below was checked against a real 512 KiB dump from a retail unit (`MDRID294300`, firmware v3.0.1 at dump time, later v2.5.0).

## 1. Platform

| Item | Level | Note |
|---|---|---|
| Bluetooth SoC: MediaTek MT2811 | Verified (teardown) | [52Audio teardown](http://www.52audio.com/archives/54160.html) names the MediaTek MT2811 platform. The RACE protocol, the `AB15/…` NVKEY group and the NVKEY `0x3901` value `"AB1530BLE"` (read live, 2026-10-26) point to an Airoha-derived SDK; the exact relation to Airoha AB155x is unverified. |
| ANC processor: Sony CXD90050 = "HD Noise Cancelling Processor QN1" | Verified (teardown) | Same teardown. Sony's [product page](https://www.sony-asia.com/electronics/headband-headphones/wh-1000xm4) describes the QN1 working with a separate Bluetooth SoC. |
| External flash marked `Q128JWYIQ` | Partly verified | Marking from the teardown. A 128 Mbit (16 MB) part is likely from the marking but unverified. |
| Other ICs: Cirrus Logic CS48L32 audio codec/DSP, Lattice iCE40UL1K FPGA, TI BQ27421 fuel gauge, MPS MP2625B charger, Atmel ATSAMD21J17D touch MCU, barometric pressure sensor marked "344 KS" | Verified (teardown) | Same teardown. Firmware log strings tagged `[AC][StC]` (likely speak-to-chat) mention `CS48L32`; the pressure sensor fits NC Optimizer's `config/nc_atm_value`. |
| Partition map (bootloader, FOTA, voice prompts at `0x00510000`) | Unverified | No partition table has been read and published yet. |
| Public schematics or service manual | Not found | FCC ID is likely `AK8WH1000XM4` ([fccid.io](https://fccid.io/AK8WH1000XM4)); the board is reported as HDX-2943. No public schematic was found. |

## 2. NVDM store as seen over RACE

`tools/backup_nvdm.py` reads the NVDM through RACE page reads (`0x0403`) with `storage_type = 1` and logical addresses starting at `0`. **Device-verified:**

- The store is **512 KiB**: 128 physical erase blocks (PEBs) of 4096 bytes, 16 pages of 256 bytes each.
- Every PEB starts with the `NVDM` magic.
- The MDR update-mode handshake that `tools/backup_nvdm.py` sends first is **not needed** for these reads: a full 512 KiB read without it took about 70 s on 2026-10-26, and the owner reported that video playback on the headset was not interrupted.
- NVKEYs (group `AB15`, item name = key ID in hex) can be read with RACE `0x0A00` (payload `<keyID:2 LE><maxLen:2 LE>`, reply `<len:2 LE><data>`), command format from [lx-exe/stealth600-gen3-usb-fix](https://github.com/lx-exe/stealth600-gen3-usb-fix). Four keys read live matched the dump byte for byte.
- Where this store sits in physical flash is **unknown**. The earlier claim "Partition 6 at `0x000DF000`, 128 KB" is not supported by the working tool.

## 3. On-disk format

### 3.1 PEB header (12 bytes, little-endian)

| Offset | Size | Field | Observed values |
|---|---|---|---|
| `0x00` | 4 | magic | `4E 56 44 4D` (`"NVDM"`) |
| `0x04` | 4 | erase count | e.g. `0x42` |
| `0x08` | 1 | status | `0xE0` in 126 blocks, `0xFE` in 2 blocks |
| `0x09` | 1 | unknown | `0xF0` (active blocks) or `0xFF` (the two `0xFE` blocks) |
| `0x0A` | 1 | unknown, probably a version | `0x01` |
| `0x0B` | 1 | unknown | `0xFF` |

Only the values in the table were observed. The meaning of `0xE0` (in use) and `0xFE` (formatted, empty) fits the free space found in those blocks. Other status values that earlier versions listed (`0xF0`, `0xC0`, `0xFC`, `0x80`) were not seen and are unverified.

### 3.2 Item header (20 bytes, little-endian, `<BBHHBBHHII`)

Device-verified on all 230 active items of the dump:

| Offset | Size | Field | Note |
|---|---|---|---|
| `0x00` | 1 | status | `0xFC` = valid, `0xF8` = deleted (both observed) |
| `0x01` | 1 | PEB number | Index of the containing block |
| `0x02` | 2 | reserved | `0x00FF` observed |
| `0x04` | 2 | offset | Offset of this header **relative to the end of the PEB header**, so the physical position is `offset + 12` |
| `0x06` | 1 | group name size | **Includes** the trailing NUL (e.g. `9` for `"CXD90050\0"`) |
| `0x07` | 1 | item name size | **Includes** the trailing NUL (e.g. `11` for `"ALIGN_FF_L\0"`) |
| `0x08` | 2 | value size | Bytes of payload |
| `0x0A` | 2 | index | 16-bit; values above 255 occur (e.g. `0x02CC`). Earlier docs wrongly split this into an 8-bit index plus a version byte. |
| `0x0C` | 4 | sequence number | Grows with every write. The highest valid sequence for a name wins. |
| `0x10` | 4 | name hash | See 3.4 |

The header is followed by the group name, the item name, the value, and a 2-byte checksum. The parser then advances to the next 4-byte boundary when the next record starts there (`xm4_optimizer/nvdm/parser.py`).

### 3.3 Checksum (device-verified)

The 16-bit checksum covers `header[1:20] + group_name + item_name + value`, with names including their NUL bytes. The status byte is excluded, which lets firmware mark a record deleted (`0xFC` → `0xF8`) without rewriting it. Bytes at even positions are summed into the low byte, bytes at odd positions into the high byte, each modulo 256:

```python
def checksum(buf: bytes) -> int:   # buf = header[1:20] + group + name + value
    lo = sum(buf[0::2]) & 0xFF
    hi = sum(buf[1::2]) & 0xFF
    return (hi << 8) | lo
```

### 3.4 Name hash (device-verified)

32-bit BKDR-style hash over `group + name` **without** NUL bytes. Example: `CXD90050` + `ALIGN_FF_L` → `0x0743AC70`, which matches the stored value.

```python
def name_hash(group: str, name: str) -> int:
    h, a, b = 0, 63689, 378551
    for c in (group + name).encode("ascii"):
        h = (h * a + c) & 0xFFFFFFFF
        a = (a * b) & 0xFFFFFFFF
    return h
```

## 4. Items relevant to ANC

| Item | Size | Level | What is known |
|---|---|---|---|
| `CXD90050/ALIGN_FF_L`, `ALIGN_FF_R`, `ALIGN_FB_L`, `ALIGN_FB_R` | 8 B | Device-verified | Eight ASCII hex digits. The meaning (a per-unit trim word) is a hypothesis. |
| `config/optimizer_coeff` | 40 B | Device-verified | The active value reads as ten big-endian 32-bit words. Words 0 and 5 are `0x10000000` (1.0 in Q4.28) and the rest are 0, which looks like two identity biquads. Older deleted copies hold non-identity values. Interpretation is a hypothesis. Firmware strings mention `[AC][optimizer] Cannot write optimizer coeff to nvdm`. |
| `DNC/howling_pre_notice_off` | 1 B | Device-verified | Present. |
| `DNC/ALIGN_*` (108-byte biquad blocks), `DNC/HOWLING_PRE_PARAM`, `DNC/HOWLING_DET_PARAM`, `DNC/OPTIMIZER_PARAM` | — | **Contradicted** | Not present in the dump. The structs that earlier versions documented for them (`<If` + `5 × <5i`, `<ffIIffff`, `<ffIIff`, `<fffffIIfff`) have no evidence. The code in `xm4_optimizer/nvdm/cxd90050.py` still implements this model and is only used by the emulator. |

## 5. Other item groups seen

`production/*` (model name, serial, Bluetooth address, colour, destination), `config/*` (EQ, multipoint addresses, ANC settings), `BT/*` (pairing records), `BT_SINK/devinfo`, `AB15/*` (numeric keys), `DCXO/CAPID Value`, `PS/calib`, `port_service/*`. Several of these are personal; see the privacy section of [STATUS.md](STATUS.md#privacy-what-an-nvdm-dump-contains).
