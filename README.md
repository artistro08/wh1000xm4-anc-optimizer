# wh1000xm4-anc-optimizer

Calibration, NVDM Partition 6 parser, Q28 fixed-point Biquad IIR filter synthesizer, zero-byte dry-run simulator, and safe RACE/ATCI parameter injector for the **Sony WH-1000XM4** (`MT2811` / `AB155x` SoC + `CXD90050` QN1 ANC DSP).

Python 3.10+, depends only on `numpy` and the standard library (native Linux `AF_BLUETOOTH` / `BTPROTO_RFCOMM` sockets).

**Result:** +6.52 dB to +7.49 dB (+7.5 dB nominal) extra feedforward low-frequency attenuation across the **50 Hz – 800 Hz** band (engine drone, cabin hum, HVAC rumble, low-mid vocal fundamental) using a 2-stage cascaded RBJ Low-Shelf ($f_0 = 1400\text{ Hz}, S = 0.9$) + 25 Hz 2nd-order Butterworth Subsonic HPF, quantized to 32-bit signed Q28 fixed-point (`<5i`) with strict open-unit-circle pole stability ($\max |p_k| = 0.997689 < 1.0$).

> **Read before you start.**
> - Partition 6 (`0x000DF000`, 128 KB) stores per-unit acoustic trim and DNC parameters (`ALIGN_FF_L/R`, `ALIGN_FB_L/R`, `HOWLING_PRE_PARAM`, `HOWLING_DET_PARAM`, `OPTIMIZER_PARAM`).
> - If a data item in Partition 6 fails its 16-bit interleaved NVDM checksum at boot, the MT2811 firmware falls back to ROM defaults (`nvdm_audio_coef_default.h`), so a bad coefficient checksum cannot bootloop the headset.
> - **Never overwrite `HOWLING_PRE_PARAM` or `HOWLING_DET_PARAM`** when increasing feedforward gain: the CXD90050 hardware saturation and anti-howling watchdogs rely on those thresholds. `xm4-optimizer` updates only the target 108-byte `ALIGN_*` item in place and verifies bit-exact readback on both NOR flash and live DSP RAM before committing.
> - Always take a full 128 KB Partition 6 backup first (`backup` command). A single command (`rollback`) restores all 512 NOR flash pages (256 B each) bit-for-bit.

---

## Contents

| Path | What |
|---|---|
| [`xm4_optimizer/transport/protocol_mdr.py`](xm4_optimizer/transport/protocol_mdr.py) | Sony MDR v1/v2 framing (`0x3E`/`0x3C`), `0x3D` byte stuffing, modulo-256 checksum |
| [`xm4_optimizer/transport/protocol_race.py`](xm4_optimizer/transport/protocol_race.py) | Airoha RACE binary framing (`0x05 0x5A`/`0x5B`), opcodes `0x0402` (Page Program), `0x0403` (Page Read), `0x0404` (Erase), `0x0F92` (ATCI Tunnel), bit-reversed CRC-8 Maxim (`0x31`) |
| [`xm4_optimizer/transport/rfcomm_socket.py`](xm4_optimizer/transport/rfcomm_socket.py) | Native Linux `AF_BLUETOOTH` RFCOMM transport (Channel 11 `Serial MC` & Channel 21 `Airoha_APP`) |
| [`xm4_optimizer/transport/virtual_device.py`](xm4_optimizer/transport/virtual_device.py) | Full in-memory hardware emulator (128 KB NOR flash + CXD90050 DSP register bank + fault injection) |
| [`xm4_optimizer/nvdm/peb.py`](xm4_optimizer/nvdm/peb.py) | 4096-byte Physical Erase Block (PEB) header parser (`<IIBBBB`, magic `0x4D44564E` `"NVDM"`) |
| [`xm4_optimizer/nvdm/item.py`](xm4_optimizer/nvdm/item.py) | 20-byte NVDM Data Item header (`<BBHHBBHBBII`), 16-bit interleaved checksum, 32-bit BKDR hash |
| [`xm4_optimizer/nvdm/cxd90050.py`](xm4_optimizer/nvdm/cxd90050.py) | Binary packers/unpackers for `CXD90050AlignmentBlock` (108 B), `HowlingPreventionParam` (32 B), `HowlingDetectionParam` (24 B), `OptimizerParam` (40 B) |
| [`xm4_optimizer/nvdm/parser.py`](xm4_optimizer/nvdm/parser.py) | Full 128 KB NVDM image parser, RFC 1982 sequence resolver, and image serializer |
| [`xm4_optimizer/dsp/fixed_point.py`](xm4_optimizer/dsp/fixed_point.py) | Q4.28 (`scale = 2^28`) and Q1.31 fixed-point converters with saturation clamping and `<5i` struct packing |
| [`xm4_optimizer/dsp/biquad.py`](xm4_optimizer/dsp/biquad.py) | 5-coefficient 2nd-order IIR section (`[a1, a2, b0, b1, b2]`), standard vs MAC pre-negated sign normalization, Transposed Direct Form II filter engine |
| [`xm4_optimizer/dsp/synthesizer.py`](xm4_optimizer/dsp/synthesizer.py) | RBJ Audio EQ Cookbook Low-Shelf, Peaking EQ, and Butterworth Subsonic HPF synthesizer across 48 / 96 / 192 / 768 kHz |
| [`xm4_optimizer/dsp/stability.py`](xm4_optimizer/dsp/stability.py) | Closed-form Z-plane characteristic root solver, Schur-Cohn / Jury stability triangle validator, Q28 quantization invariance verifier |
| [`xm4_optimizer/dsp/simulation.py`](xm4_optimizer/dsp/simulation.py) | Vectorized DTFT $H(e^{j\omega})$ evaluator, band attenuation verifier, and ASCII frequency response plotter |
| [`xm4_optimizer/safety/dry_run.py`](xm4_optimizer/safety/dry_run.py) | Zero-byte dry-run engine (`ReadOnlyTransportGuard`) rendering Q28 hex diffs, flash page offsets, and ASCII curve |
| [`xm4_optimizer/safety/injector.py`](xm4_optimizer/safety/injector.py) | Staged parameter injector with pre-flight Nyquist gate, in-place NVDM item update, dual ATCI + flash write, and automatic rollback on fault |
| [`xm4_optimizer/safety/verifier.py`](xm4_optimizer/safety/verifier.py) | Bit-exact readback verifier for NOR flash Partition 6 and `AT+HWPTOOL=COEFGET` DSP registers |
| [`xm4_optimizer/safety/rollback.py`](xm4_optimizer/safety/rollback.py) | 128 KB Partition 6 backup creator (with `.sha256` sidecar) and 1-command bit-exact rollback engine |
| [`xm4_optimizer/cli.py`](xm4_optimizer/cli.py) | CLI entry point (`xm4-optimizer` / `python3 -m xm4_optimizer`) |
| [`docs/HARDWARE_AND_NVDM.md`](docs/HARDWARE_AND_NVDM.md) | Flash map, Partition 6 binary layout, PEB state machine, and CXD90050 struct reference |
| [`docs/DSP_ACOUSTIC_THEORY.md`](docs/DSP_ACOUSTIC_THEORY.md) | Mathematical derivations, bilinear transform, Q28 error bounds, Schur-Cohn proof, and literature references |
| [`docs/WIRE_PROTOCOLS.md`](docs/WIRE_PROTOCOLS.md) | Sony MDR v1/v2 (Channel 11) and Airoha RACE / ATCI (Channel 21) wire protocol specifications |

---

## 0. Know your headphones

Measured on a retail **Sony WH-1000XM4** (`MDRID294300`, firmware `v3.0.1`):

| Component | Details |
|---|---|
| Bluetooth / System SoC | **MediaTek / Airoha MT2811** (`AB155x` platform, ARM Cortex-M4F + Tensilica HiFi Mini DSP) |
| Dedicated ANC Processor | **Sony CXD90050** ("HD Noise Canceling Processor QN1") |
| External NOR Flash | 16 MB SPI NOR Flash (`256 B` page program granularity, `4096 B` sector/PEB erase) |
| NVDM Calibration Partition | **Partition 6** at base address `0x000DF000`, size `0x20000` (128 KB = 32 PEBs × 4096 B = 512 pages × 256 B) |
| Voice Prompt FOTA Region | `0x00510000` (~903 KB container `VP_data.bin` / 3,530 pages of 256 B) |
| RFCOMM Channel 11 (`Serial MC`) | UUID `b9b213ce-eeab-49e4-8fd9-aa478ed1b26b` — active Sony MDR control channel on firmware v3.0.1 (sends a 26-byte hello frame on connect that must be ACKed with `3e 01 00 00 00 00 00 01 3c` before commands are accepted; Channel 9 `Serial HPC` does not reply on v3.0.1) |
| RFCOMM Channel 21 (`Airoha_APP`) | UUID `8901dfa8-5c7e-4d8f-9f0c-c2b70683f5f0` — Airoha RACE binary protocol (`0x05 0x5A`) for raw flash page reads/writes and ATCI command tunneling |
| ROM Fallback | `nvdm_audio_coef_default.h` — loaded automatically by the DSP driver if Partition 6 item checksums fail |

> **PipeWire / BlueZ note on Linux:** If an active A2DP audio stream (e.g. Spotify) is saturating the link or holding the headset in high-throughput sink mode, switch the default sink to idle or pause playback before running bulk RFCOMM flash operations.

---

## 1. NVDM Partition 6 Format (`0x000DF000`)

Partition 6 uses Airoha's Non-Volatile Data Management (NVDM) log-structured wear-leveling filesystem across 32 Physical Erase Blocks (PEBs) of `4096` bytes each (`0x000DF000` – `0x000FEFFF`).

### 1.1 PEB Header (`12` bytes at offset `0x000` of each 4 KB block)

```
struct peb_header_t {          // <IIBBBB (12 bytes, little-endian)
    uint32_t magic;            // 0x4D44564E ("NVDM" in ASCII LE; 0xFFFFFFFF if virgin)
    uint32_t erase_count;      // Monotonic block wear counter
    uint8_t  status;           // 0xE0 = ACTIVED, 0xFC = RECLAIMING, 0xFE = EMPTY, 0xFF = VIRGIN
    uint8_t  peb_reserved;     // 0xF0
    uint8_t  version;          // 0x01
    uint8_t  reserved;         // 0x00
};
```

### 1.2 Data Item Record (starts at offset `0x00C` inside an active PEB)

```
struct data_item_header_t {    // <BBHHBBHBBII (20 bytes, little-endian)
    uint8_t  status;           // 0xFC = VALID, 0xF8 = DELETE, 0xFE = WRITING, 0xFF = EMPTY
    uint8_t  pnum;             // Physical Erase Block index (0..31)
    uint16_t reserved;         // 0x0000
    uint16_t offset;           // Byte offset of this header within the PEB
    uint8_t  group_name_size;  // Length of ASCII group name (e.g. "DNC" -> 3)
    uint8_t  item_name_size;   // Length of ASCII item name (e.g. "ALIGN_FF_L" -> 10)
    uint16_t value_size;       // Payload byte length (e.g. 108 for CXD90050AlignmentBlock)
    uint8_t  index;            // Item table index
    uint8_t  version;          // 0x01
    uint32_t sequence_number;  // Monotonic write counter (resolved via RFC 1982 modular math)
    uint32_t hash_name;        // 32-bit polynomial BKDR hash of (group_name + item_name)
};
// Followed immediately by:
//   char     group_name[group_name_size];
//   char     item_name[item_name_size];
//   uint8_t  value[value_size];
//   uint16_t checksum;        // 16-bit interleaved sum over header[1..19] + group + name + value
//   uint8_t  padding[0..3];   // 0xFF alignment to 4-byte boundary
```

- **BKDR Hash (`hash_name`):** Polynomial rolling hash with seeds $a = 63689, b = 378551$:
  $$h_k = (h_{k-1} \cdot a_k + c_k) \bmod 2^{32}, \quad a_{k+1} = (a_k \cdot b) \bmod 2^{32}$$
- **16-Bit Interleaved Checksum (`checksum`):** Computed over `header[1:20] + group_name + item_name + value` (excluding the mutable `status` byte at offset 0 so items can be marked `0xF8 DELETE` without invalidating historical checksums):
  $$\text{checksum} = \left(\left(\sum_{i \text{ odd}} \text{data}[i]\right) \ \& \ \text{0xFF}\right) \ll 8 \;\Big|\; \left(\left(\sum_{i \text{ even}} \text{data}[i]\right) \ \& \ \text{0xFF}\right)$$

### 1.3 CXD90050 Alignment & DNC Parameter Blocks

| Group / Item Name | Size | Format | Role |
|---|---|---|---|
| `DNC/ALIGN_FF_L` | `108 B` | `<If` + `5 × <5i` | Left Feedforward (outer mic) 5-stage Biquad IIR cascade (`filter_id = 0`) |
| `DNC/ALIGN_FF_R` | `108 B` | `<If` + `5 × <5i` | Right Feedforward (outer mic) 5-stage Biquad IIR cascade (`filter_id = 1`) |
| `DNC/ALIGN_FB_L` | `108 B` | `<If` + `5 × <5i` | Left Feedback (ear-cup mic) 5-stage Biquad IIR cascade (`filter_id = 2`) |
| `DNC/ALIGN_FB_R` | `108 B` | `<If` + `5 × <5i` | Right Feedback (ear-cup mic) 5-stage Biquad IIR cascade (`filter_id = 3`) |
| `DNC/HOWLING_PRE_PARAM` | `32 B` | `<ffIIffff` | Anti-howling notch depth, Q factor, hold/release time constants, saturation ceiling |
| `DNC/HOWLING_DET_PARAM` | `24 B` | `<ffIIff` | Positive feedback oscillation detection energy ratio, peak-to-average threshold, window size |
| `DNC/OPTIMIZER_PARAM` | `40 B` | `<fffffIIfff` | Atmospheric pressure sensor (`baro_ref_hpa`) & ear-cup acoustic leakage calibration |

Each 108-byte `CXD90050AlignmentBlock` consists of an 8-byte header (`uint32_t filter_id`, `float gain_db`) followed by 5 cascaded 20-byte biquad stages. Each biquad stage stores 5 signed 32-bit integers (`<5i`) in **Q4.28 fixed-point** (`scale = 1 << 28 = 268,435,456`, range $[-8.0, +7.999999996]$) in the exact order `[a1, a2, b0, b1, b2]`.

---

## 2. DSP Filter Synthesis & Stability Proof

### 2.1 Why a Dual-Stage Cascade (Low-Shelf + Subsonic HPF)?

In a hybrid ANC headset, total noise reduction is bounded by the product of passive seal isolation and active electro-acoustic cancellation:

$$H_{\text{ANC}}(e^{j\omega}) = 1 - P(e^{j\omega})\,W_{\text{FF}}(e^{j\omega})$$

where $P(e^{j\omega})$ is the secondary path (DAC $\to$ driver $\to$ ear cavity) and $W_{\text{FF}}(e^{j\omega})$ is the feedforward controller (`ALIGN_FF_L/R`).

1. **Stage 0 — RBJ 2nd-Order Low-Shelf ($f_0 = 1400\text{ Hz}, G = +7.5\text{ dB}, S = 0.9$):**
   Lifts feedforward loop gain across **50 Hz – 800 Hz** (+6.52 dB at 800 Hz up to +7.49 dB at 50–300 Hz) while rolling off smoothly to 0 dB above 1.4 kHz. Keeping shelf slope $S = 0.9 < 1.0$ eliminates resonant peaking at the transition corner and preserves phase margin near the feedback crossover ($1.5\text{ kHz} - 3\text{ kHz}$) per Bode's Sensitivity Integral:
   $$\int_0^\pi \ln |S(e^{j\omega})|\,d\omega = 0$$
2. **Stage 1 — 2nd-Order Butterworth Subsonic High-Pass ($f_0 = 25\text{ Hz}, Q = 1/\sqrt{2}$):**
   Attenuates infrasonic pressure pulses ($< 20\text{ Hz}$ from walking footstep thuds, bus/subway door slams, and wind buffeting) before they can saturate the 40 mm driver excursion or clip the Q28 fixed-point accumulator.
3. **Stages 2, 3, 4 — Unity Pass-Through Padding (`b0 = 0x10000000`, others `0`):**
   Preserves the fixed 108-byte `CXD90050AlignmentBlock` layout.

### 2.2 Strict Nyquist & Schur-Cohn Stability Gate

Before any coefficient is staged to ATCI or written to NOR flash, [`injector.py`](xm4_optimizer/safety/injector.py) and [`stability.py`](xm4_optimizer/dsp/stability.py) verify both:
1. **Algebraic Schur-Cohn / Jury Stability Triangle** on the quantized Q28 denominator $D(z) = 1 + a_1 z^{-1} + a_2 z^{-2}$:
   $$|a_2| < 1 \quad \text{and} \quad |a_1| < 1 + a_2$$
2. **Direct Z-Plane Characteristic Root Verification** ($z^2 + a_1 z + a_2 = 0$):
   $$p_{1,2} = \frac{-a_1 \pm \sqrt{a_1^2 - 4a_2}}{2}, \quad \max(|p_1|, |p_2|) < 1.0$$

If any stage violates $\max |p_k| < 1.0$ (even after Q28 quantization rounding), `UnstableFilterHoldError` is raised immediately with **0 bytes written** to hardware.

---

## 3. Usage

```sh
# 1. Zero-byte dry-run simulation (prints Q28 hex diffs, flash offsets, and ASCII curve; writes 0 bytes)
PYTHONPATH=. python3 -m xm4_optimizer --dry-run --target-gain 7.5

# 2. Backup the full 128 KB NVDM Partition 6 (0x000DF000) from headphones over Bluetooth RFCOMM
PYTHONPATH=. python3 -m xm4_optimizer --mac AC:80:0A:2E:45:80 backup wh1000xm4_nvdm_backup.bin

# 3. Parse and inspect a 128 KB NVDM dump (or read live from the device)
PYTHONPATH=. python3 -m xm4_optimizer parse wh1000xm4_nvdm_backup.bin

# 4. Synthesize and safely inject +7.5 dB feedforward filter (auto-backups & verifies flash + DSP RAM)
PYTHONPATH=. python3 -m xm4_optimizer --mac AC:80:0A:2E:45:80 inject \
    --target-gain 7.5 \
    --filter-id 0 \
    --backup-out wh1000xm4_nvdm_backup.bin

# 5. Verify bit-exact readback on both NOR flash and live CXD90050 registers
PYTHONPATH=. python3 -m xm4_optimizer --mac AC:80:0A:2E:45:80 verify --target-gain 7.5

# 6. 1-command bit-exact rollback to factory NVDM backup (verifies .sha256 sidecar & full 128 KB readback)
PYTHONPATH=. python3 -m xm4_optimizer --mac AC:80:0A:2E:45:80 rollback wh1000xm4_nvdm_backup.bin
```

### Example Dry-Run Output

```text
================================================================================================
  Target Memory Mapping & Acoustic Summary (ALIGN_FF_L)
================================================================================================
  Partition 6 Base Address  : 0x000DF000
  NVDM Item Header Address  : 0x000DF00C
  CXD90050 Payload Offset   : 0x000DF02D (108 bytes)
  Affected 256B Flash Pages : 0x000DF000
  50-800 Hz Band Gain Range : +6.52 dB to +7.49 dB
  Nyquist Pole Stability    : PASS (max |p| = 0.997689 < 1.0)
  Hardware Flash Bytes Written: 0 bytes (DRY-RUN BARRIER ACTIVE)
------------------------------------------------------------------------------------------------
  Stage    | Coeff |   Orig Float |    Opt Float |        Delta |   Orig Q28 |    Opt Q28 | Status   
------------------------------------------------------------------------------------------------
  Stage 0  | a1    |    +0.000000 |    -1.781672 |    -1.781672 | 0x00000000 | 0xE37E4513 | MODIFIED 
  Stage 0  | a2    |    +0.000000 |    +0.801318 |    +0.801318 | 0x00000000 | 0x0CD23253 | MODIFIED 
  Stage 0  | b0    |    +1.000000 |    +1.060372 |    +0.060372 | 0x10000000 | 0x10F748CF | MODIFIED 
  Stage 0  | b1    |    +0.000000 |    -1.768202 |    -1.768202 | 0x00000000 | 0xE3B571CF | MODIFIED 
  Stage 0  | b2    |    +0.000000 |    +0.754416 |    +0.754416 | 0x00000000 | 0x0C121641 | MODIFIED 
  Stage 1  | a1    |    +0.000000 |    -1.995372 |    -1.995372 | 0x00000000 | 0xE012F4CE | MODIFIED 
  Stage 1  | a2    |    +0.000000 |    +0.995383 |    +0.995383 | 0x00000000 | 0x0FED1666 | MODIFIED 
  Stage 1  | b0    |    +1.000000 |    +0.997689 |    -0.002311 | 0x10000000 | 0x0FF68866 | MODIFIED 
  Stage 1  | b1    |    +0.000000 |    -1.995377 |    -1.995377 | 0x00000000 | 0xE012EF34 | MODIFIED 
  Stage 1  | b2    |    +0.000000 |    +0.997689 |    +0.997689 | 0x00000000 | 0x0FF68866 | MODIFIED 
================================================================================================
  Frequency Response Curve (Magnitude dB vs Frequency)
       ┌────────────────────────────────────────────────────────────┐
 +7.9dB│                                                            │
 +7.1dB│      **■■■■■■■■■■■■■■■■■■■■■■■                             │
 +6.4dB│    **                         ■*                           │
 +5.6dB│   *                             **                         │
 +4.9dB│  *                                *                        │
 +4.1dB│                                    *                       │
 +3.4dB│ *                                   *                      │
 +2.6dB│                                      *                     │
 +1.9dB│*                                      *                    │
 +1.1dB│                                        **                  │
 +0.4dB│                                          ******************│
 -0.4dB│                                                            │
       └────────────────────────────────────────────────────────────┘
        20Hz                                            20000Hz
        [■ = Target Band 50Hz - 800Hz]
================================================================================================
```

---

## 4. Running the Test Suite

The test suite includes **404 tests** covering MDR/RACE wire framing, RFCOMM socket stress/fault injection, 128 KB NVDM parser fuzzing, Q28/Q31 fixed-point extremes, adversarial pole stability boundary checks, acoustic response verification, and full CLI lifecycle tests:

```sh
pytest -v
```
