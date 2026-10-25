# Hardware Architecture & Airoha NVDM Partition 6 Specification

This document details the flash memory layout, Non-Volatile Data Management (NVDM) filesystem structures, integrity algorithms, and CXD90050 calibration parameter formats used on the **Sony WH-1000XM4** (`MT2811` / `AB155x` SoC + `CXD90050` QN1 ANC processor).

---

## 1. SPI NOR Flash Memory Map (MT2811 / AB155x)

The Sony WH-1000XM4 uses a 16 MB (128 Mbit) external SPI NOR flash connected to the MediaTek / Airoha MT2811 SoC. Flash operations over the Airoha RACE protocol (`0x0402` Page Program, `0x0403` Page Read, `0x0404` Partition Erase) address this memory directly:

| Flash Region | Base Address | Size | Granularity | Purpose |
|---|---|---|---|---|
| Bootloader & Partition Table | `0x00000000` | `512 KB` | `4 KB` sector | Secure boot header, BLOADER, partition directory |
| **NVDM Partition 6 (ANC / Audio)** | **`0x000DF000`** | **`128 KB` (`0x20000`)** | `32` PEBs × `4096 B` (`512` pages × `256 B`) | Key-value calibration store: `CXD90050` Biquad filters, `DNC` howling/optimizer structs, Bluetooth link keys |
| Main Firmware Image (`FOTA`) | `0x00100000` | `~4.0 MB` | `256 B` page | RTOS kernel, Sony MDR state machine, Airoha RACE/ATCI handlers, `nvdm_audio_coef_default.h` ROM fallback |
| Voice Prompt Container (`VP`) | `0x00510000` | `~903 KB` (`3,530` pages) | `256 B` page | `VP_data.bin` offset table + 12 MP3 voice prompts (`power_on`, `noise_canceling`, `ambient_sound`, etc.) |

---

## 2. Physical Erase Block (PEB) Layout

Partition 6 (`0x000DF000` – `0x000FEFFF`) is divided into 32 contiguous 4096-byte Physical Erase Blocks (`pnum = 0 .. 31`).

### 2.1 PEB Header (`12` bytes, `<IIBBBB`)

Located at offset `0x000` of every 4096-byte PEB:

| Offset | Type | Field | Description |
|---|---|---|---|
| `0x00` | `uint32_t` | `magic` | `0x4D44564E` (`"NVDM"` in little-endian ASCII). `0xFFFFFFFF` on virgin/unformatted flash. |
| `0x04` | `uint32_t` | `erase_count` | Monotonic erase cycle counter used by the wear-leveling garbage collector. |
| `0x08` | `uint8_t` | `status` | Block lifecycle state (bit-cleared on NOR flash without erasing). |
| `0x09` | `uint8_t` | `peb_reserved` | Default `0xF0`. |
| `0x0A` | `uint8_t` | `version` | NVDM layout version (`0x01`). |
| `0x0B` | `uint8_t` | `reserved` | Default `0x00`. |

### 2.2 PEB Lifecycle States (`status` byte)

Because NOR flash bits can only transition `1 -> 0` without a full 4 KB sector erase, Airoha NVDM encodes block states by progressively clearing bits from `0xFF`:

| State Constant | Hex Value | Meaning |
|---|---|---|
| `VIRGIN` | `0xFF` | Unformatted / freshly erased block |
| `EMPTY` | `0xFE` | Formatted with valid `"NVDM"` header, ready to accept data items |
| `ACTIVED` | `0xE0` | Currently active block containing valid data item records |
| `TRANSFERING` | `0xF0` | Intermediate garbage-collection source state |
| `TRANSFERRED` | `0xC0` | Valid items copied out; awaiting erase |
| `RECLAIMING` | `0xFC` | Marked for reclamation |
| `ERASING` | `0x80` | Sector erase in progress |

---

## 3. NVDM Data Item Record Format

Data items begin immediately after the PEB header at offset `0x00C` (12 bytes) and pack sequentially toward offset `0xFFF` (4095).

### 3.1 Data Item Header (`20` bytes, `<BBHHBBHBBII`)

| Offset | Type | Field | Description |
|---|---|---|---|
| `0x00` | `uint8_t` | `status` | `0xFC` = `VALID`, `0xF8` = `DELETE`, `0xFE` = `WRITING`, `0xFF` = `EMPTY` (end of items in PEB) |
| `0x01` | `uint8_t` | `pnum` | Index of the containing PEB (`0..31`) |
| `0x02` | `uint16_t` | `reserved` | `0x0000` |
| `0x04` | `uint16_t` | `offset` | Byte offset of this item header from the start of the PEB (`>= 0x000C`) |
| `0x06` | `uint8_t` | `group_name_size` | Byte length of the ASCII group name (e.g. `3` for `"DNC"`) |
| `0x07` | `uint8_t` | `data_item_name_size` | Byte length of the ASCII item name (e.g. `10` for `"ALIGN_FF_L"`) |
| `0x08` | `uint16_t` | `value_size` | Byte length of the binary payload (`108` for alignment blocks) |
| `0x0A` | `uint8_t` | `index` | Internal LUT slot index |
| `0x0B` | `uint8_t` | `version` | Item format version (`0x01`) |
| `0x0C` | `uint32_t` | `sequence_number` | Monotonic write counter. If multiple `VALID` items share the same `(group, name)`, the highest sequence number under RFC 1982 modular arithmetic wins. |
| `0x10` | `uint32_t` | `hash_name` | 32-bit BKDR hash of `group_name + item_name` |

### 3.2 Variable-Length Body & Trailer

Immediately following the 20-byte header:
1. `group_name` (`group_name_size` bytes, ASCII)
2. `item_name` (`data_item_name_size` bytes, ASCII)
3. `value` (`value_size` bytes, raw binary struct)
4. `checksum` (`2` bytes, `uint16_t` little-endian)
5. Optional `0xFF` padding bytes to align the next record to a 4-byte boundary.

### 3.3 Integrity Algorithms

#### 16-Bit Interleaved Checksum
The NVDM checksum covers `header[1:20] + group_name + item_name + value`. Note that **`header[0]` (`status`) is intentionally excluded** so the firmware can transition a record from `0xFC` (`VALID`) to `0xF8` (`DELETE`) by clearing bit 2 in NOR flash without invalidating the stored checksum:

```python
def calculate_data_item_checksum(
    header_without_status: bytes,  # 19 bytes: header[1:20]
    group_name: bytes,
    item_name: bytes,
    value: bytes,
) -> int:
    buf = header_without_status + group_name + item_name + value
    even_sum = sum(buf[0::2]) & 0xFF
    odd_sum = sum(buf[1::2]) & 0xFF
    return (odd_sum << 8) | even_sum
```

#### 32-Bit BKDR Polynomial Rolling Hash
Used for $O(1)$ key lookup across the partition:

```python
def bkdr_hash(group: str, name: str) -> int:
    s = (group + name).encode("ascii")
    h, a, b = 0, 63689, 378551
    for char in s:
        h = (h * a + char) & 0xFFFFFFFF
        a = (a * b) & 0xFFFFFFFF
    return h
```

---

## 4. CXD90050 & DNC Calibration Payloads

### 4.1 `CXD90050AlignmentBlock` (`108` bytes)
Stored under group `"DNC"`, item names `"ALIGN_FF_L"` (`id=0`), `"ALIGN_FF_R"` (`id=1`), `"ALIGN_FB_L"` (`id=2`), `"ALIGN_FB_R"` (`id=3`):

```
struct cxd90050_biquad_q28_t { // <5i (20 bytes)
    int32_t a1;                // Q4.28 denominator z^-1 coefficient
    int32_t a2;                // Q4.28 denominator z^-2 coefficient
    int32_t b0;                // Q4.28 numerator z^0 coefficient (unity = 0x10000000)
    int32_t b1;                // Q4.28 numerator z^-1 coefficient
    int32_t b2;                // Q4.28 numerator z^-2 coefficient
};

struct cxd90050_align_block_t { // 108 bytes total
    uint32_t              filter_id;   // 0=FF_L, 1=FF_R, 2=FB_L, 3=FB_R
    float                 gain_db;     // Trim gain in dB (IEEE-754 float32)
    cxd90050_biquad_q28_t biquads[5];  // 5 cascaded 2nd-order sections (100 bytes)
};
```

### 4.2 `HowlingPreventionParam` (`32` bytes, `DNC/HOWLING_PRE_PARAM`)
 unpacked as `<ffIIffff`:
- `suppression_Wrapper_db` (`float`, default `-12.0` dB)
- `notch_q_factor` (`float`, default `8.0`)
- `hold_time_ms` (`uint32_t`, default `250` ms)
- `release_time_ms` (`uint32_t`, default `1000` ms)
- `min_freq_hz` (`float`, default `100.0` Hz)
- `max_freq_hz` (`float`, default `6000.0` Hz)
- `attenuation_step_db` (`float`, default `3.0` dB)
- `saturation_ceiling_dbfs` (`float`, default `-1.5` dBFS)

### 4.3 `HowlingDetectionParam` (`24` bytes, `DNC/HOWLING_DET_PARAM`)
Unpacked as `<ffIIff`:
- `energy_threshold_db` (`float`, default `-6.0` dB)
- `peak_to_avg_ratio_db` (`float`, default `15.0` dB)
- `window_size_samples` (`uint32_t`, default `512`)
- `trigger_count` (`uint32_t`, default `3`)
- `high_freq_weight` (`float`, default `1.25`)
- `hysteresis_db` (`float`, default `2.5` dB)

### 4.4 `OptimizerParam` (`40` bytes, `DNC/OPTIMIZER_PARAM`)
Unpacked as `<fffffIIfff`:
- Stores personal acoustic seal leakage compensation (`seal_leakage_comp`), atmospheric pressure reference (`baro_ref_hpa = 1013.25`), and pressure-to-gain slope used when the user holds the Custom/NC button for "NC Optimizer".
