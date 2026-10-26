# wh1000xm4-anc-optimizer

[![CI](https://github.com/randwardatake/wh1000xm4-anc-optimizer/actions/workflows/ci.yml/badge.svg)](https://github.com/randwardatake/wh1000xm4-anc-optimizer/actions/workflows/ci.yml)
[![Version](https://img.shields.io/badge/version-v0.1.0-blue.svg)](pyproject.toml)
[![Email](https://img.shields.io/badge/email-randward%40proton.me-6d4aff.svg?logo=protonmail&logoColor=white)](mailto:randward@proton.me)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-3776AB.svg?logo=python&logoColor=white)](pyproject.toml)

### Spatial audio status

The current AeroSpatial work is an **experimental stereo speaker virtualization prototype**: it places left and right channels at two virtual positions using an HRTF. It does not recover separate instruments or height objects from a stereo recording, decode Sony 360 Reality Audio/Dolby Atmos, or provide verified system-wide 3D playback. A Linux PipeWire route to the WH-1000XM4 was tried with Spotify, but listening was unsatisfactory: the user reported muffled sound, the existing EQ no longer being applied, and the image collapsing toward the center. The route bypasses the prior Spotify links, so loss of EasyEffects EQ is consistent with the current routing. The spatial image issue is unresolved; this prototype is not ready for use. See the [Linux notes](aerospatial/pipewire/README.md) and [spatial roadmap](docs/SPATIAL_ROADMAP.md).

Our target for **content-aware 3D playback** is to preserve actual multichannel or object-position information through to the binaural renderer. A stereo-only source can still be spatialized, but its original instrument positions cannot be recovered. We will label those modes separately and will not advertise content-aware 3D support until a source-to-headphones test demonstrates it.

Calibration, NVDM Partition 6 parser, Q28 fixed-point Biquad IIR filter synthesizer, zero-byte dry-run simulator, and safe RACE/ATCI parameter injector for the **Sony WH-1000XM4** (`MT2811` / `AB155x` SoC + `CXD90050` QN1 ANC DSP).

Python 3.10+, depends only on `numpy` and the standard library (native Linux `AF_BLUETOOTH` / `BTPROTO_RFCOMM` sockets).

**Result:** +6.52 dB to +7.49 dB (+7.5 dB nominal) extra feedforward low-frequency attenuation across the **50 Hz – 800 Hz** band (engine drone, cabin hum, HVAC rumble, low-mid vocal fundamental) using a 2-stage cascaded RBJ Low-Shelf ($f_0 = 1400\text{ Hz}, S = 0.9$) + 25 Hz 2nd-order Butterworth Subsonic HPF, quantized to 32-bit signed Q28 fixed-point (`<5i`) with strict open-unit-circle pole stability ($\max |p_k| = 0.997689 < 1.0$).

> **Read before you start.**
> - Partition 6 (`0x000DF000`, 128 KB) stores per-unit acoustic trim and DNC parameters (`ALIGN_FF_L/R`, `ALIGN_FB_L/R`, `HOWLING_PRE_PARAM`, `HOWLING_DET_PARAM`, `OPTIMIZER_PARAM`).
> - If a data item in Partition 6 fails its 16-bit interleaved NVDM checksum at boot, the MT2811 firmware falls back to ROM defaults (`nvdm_audio_coef_default.h`), so a bad coefficient checksum cannot bootloop the headset.
> - **Never overwrite `HOWLING_PRE_PARAM` or `HOWLING_DET_PARAM`** when increasing feedforward gain: the CXD90050 hardware saturation and anti-howling watchdogs rely on those thresholds. `xm4-optimizer` updates only the target 108-byte `ALIGN_*` item in place and verifies bit-exact readback on both NOR flash and live DSP RAM before committing.
> - Always take a full 128 KB Partition 6 backup first (`backup` command). A single command (`rollback`) restores all 512 NOR flash pages (256 B each) bit-for-bit.

---

## Flasher vs. Injector: Which tool should you use?

It is critical to understand the architectural distinction between **flashing** and **injecting**:

| Dimension | `flash` (Firmware Flasher) | `inject` (Calibration Injector) |
|---|---|---|
| **Target Storage** | SPI Flash Code Partitions (`~1.4 MB – 4.0 MB`) | NVDM Calibration Partition 6 (`108 bytes`) |
| **Protocol** | Airoha FOTA state machine (`0x1Cxx`) over MDR Ch 11 + RACE Ch 21 | Airoha RACE NVDM direct write over Ch 21 |
| **Sector Erase** | **Yes** — erases full 4 KB flash blocks (`CMD_ERASE 0x0404`) | **No** — writes strictly in-place to calibration keys |
| **Signature Verification**| **Hardware RSA-2048 & SHA-256** verified against SoC eFuses | **None** — validated via 16-bit Airoha running checksum |
| **Allowed Files** | **Official Sony-signed `.bin` firmware only** (e.g. v2.5.0, v3.0.1) | Synthesized Q4.28 Biquad IIR filter coefficients |
| **Brick / Bootloop Risk**| None with official firmware (A/B staging with safety abort). Unsigned files are rejected upfront (`0x02`). | **Zero** — ROM fallback (`nvdm_audio_coef_default.h`) + automated pre-flight snapshot & rollback |
| **Duration** | ~2 – 3 minutes (reboots headphones on commit) | ~1 second (applies immediately on DSP reboot) |
| **Primary Purpose** | Upgrading/downgrading base firmware (e.g. reversing Sony's v3.0 nerf) | Tuning ANC filter curve and eliminating low-frequency drone/hiss |

---

## Project Structure

```text
xm4_anc_optimizer/
├── docs/
│   ├── HARDWARE_AND_NVDM.md        # Flash map, Partition 6 layout, PEB state machine & CXD90050 structs
│   ├── DSP_ACOUSTIC_THEORY.md      # Bilinear transform, Q28 error bounds, Schur-Cohn stability proofs
│   └── WIRE_PROTOCOLS.md           # Sony MDR v1/v2 (Ch 11) & Airoha RACE/ATCI (Ch 21) wire specifications
├── presets/
│   ├── Sony_WH-1000XM4_SuperReview_3D_Spatial.json # EasyEffects 3D Spatial + Super* Review EQ preset
│   └── Sony_WH-1000XM4_ANC_on_SuperReview_EQ.txt  # Equalizer APO parametric profile
├── aerospatial/
│   ├── include/                    # C++20 AeroSpatialEngine & PipeWireBridge headers
│   ├── src/                        # Valve Steam Audio C-API wrapper & PipeWire RT DSP sink
│   ├── tests/                      # Mathematical IACC, ITD (625 us), ILD & bilateral acoustic tests
│   └── CMakeLists.txt              # Builds aerospatial-daemon and test_aerospatial
├── tools/
│   ├── backup_nvdm.py              # Direct Bluetooth RFCOMM NVDM backup tool (512 KB, MDR handshake)
│   ├── flash_firmware.py           # Direct Bluetooth RFCOMM flasher for official Sony firmware (.bin)
│   ├── route_pipewire_app_through_aerospatial.sh # PipeWire spatial routing bridge
│   ├── run_linux_steam_audio_smoke.sh # Steam Audio validation test runner
│   └── download_steamaudio.py      # Automated SDK installer for Valve Steam Audio v4.8.1
├── xm4_optimizer/
│   ├── cli.py                      # CLI entry point (python3 -m xm4_optimizer)
│   ├── transport/
│   │   ├── protocol_mdr.py         # Sony MDR v1/v2 framing, escaping, modulo-256 checksums
│   │   ├── protocol_race.py        # Airoha RACE binary framing, opcodes (0x0402, 0x0403, 0x0F92)
│   │   ├── rfcomm_socket.py        # Native Linux AF_BLUETOOTH RFCOMM transport (Ch 11 & Ch 21)
│   │   └── virtual_device.py       # Full in-memory hardware emulator & fault injection engine
│   ├── nvdm/
│   │   ├── peb.py                  # 4096-byte Physical Erase Block header parser ("NVDM" magic)
│   │   ├── item.py                 # 20-byte Data Item header, BKDR hash, 16-bit interleaved checksum
│   │   ├── cxd90050.py             # Binary serializers for AlignmentBlock (108B), Howling & Optimizer
│   │   └── parser.py               # 128 KB NVDM log-structured wear-leveling filesystem parser
│   ├── dsp/
│   │   ├── fixed_point.py          # 32-bit signed Q4.28 & Q1.31 converters with saturation clamping
│   │   ├── biquad.py               # 2nd-order IIR biquad section & Transposed Direct Form II engine
│   │   ├── synthesizer.py          # RBJ Audio EQ Cookbook Low-Shelf, Peaking EQ & Butterworth HPF
│   │   ├── stability.py            # Schur-Cohn / Jury stability triangle gate & Z-plane root solver
│   │   └── simulation.py           # Vectorized DTFT H(e^jw) evaluator & ASCII frequency response curve
│   └── safety/
│       ├── dry_run.py              # Zero-byte dry-run engine (ReadOnlyTransportGuard) & diff renderer
│       ├── injector.py             # Staged parameter injector with auto-rollback on fault
│       ├── verifier.py             # Bit-exact readback verifier for NOR flash & live DSP registers
│       └── rollback.py             # 128 KB Partition 6 backup engine with SHA-256 sidecar
└── tests/                          # 407 unit & integration tests (wire protocols, fuzzing, stability, 3D spatial DSP)
```

---

## Quickstart & Usage

> [!CAUTION]
> **CRITICAL: ALWAYS BACK UP YOUR HEADPHONES BEFORE FLASHING!**
> 
> Flashing custom biquad coefficients or modifying firmware writes directly to your headphone's internal SPI NOR flash over Bluetooth RFCOMM. 
> 
> * **Always take a bit-for-bit Partition 6 backup first** (Step 2 below). If anything ever goes wrong, you can restore your exact factory calibration in seconds.
> * **Battery Level:** Ensure your headphones have **at least 50% battery** before flashing.
> * **Proximity:** Keep the headphones within 1 meter of your Bluetooth adapter to prevent packet loss.
> * **Do NOT power off or disconnect** while a write or flash operation is active.
> * **Model Compatibility:** Firmware flashing and NVDM layouts are verified strictly for **Sony WH-1000XM4**. Do NOT attempt to flash an original XM4 firmware onto the XM4C or XM5!

Get up and running in under 2 minutes:

**1. Requirements & Setup**

Requires Python 3.10+ on Linux with standard Bluetooth tools (`bluez` / `bluetoothctl`):

```sh
# Clone & enter directory
git clone https://github.com/randwardatake/wh1000xm4-anc-optimizer.git
cd wh1000xm4-anc-optimizer

# Install dependencies (only numpy is required)
pip install numpy
```

Find your headphone's Bluetooth MAC address:
```sh
bluetoothctl devices | grep -i "WH-1000XM4"
# Example output: Device AC:80:0A:2E:45:80 WH-1000XM4
```

**2. Backup Full Headphone Calibration (Mandatory First Step)**

Takes a bit-for-bit 128 KB snapshot of Partition 6 (all factory calibration curves, serials, and DSP blocks) over Bluetooth RFCOMM:

```sh
PYTHONPATH=. python3 -m xm4_optimizer --mac AC:80:0A:2E:45:80 backup factory_nvdm_backup.bin
```
*Keep this `factory_nvdm_backup.bin` file safe. It is your permanent safety net.*

**3. Zero-Byte Dry Run (Safe Test)**

Simulate the Q4.28 filter synthesis, pole stability check, and frequency response without touching or writing a single byte to hardware:

```sh
PYTHONPATH=. python3 -m xm4_optimizer --dry-run --target-gain 7.5
```

**4. Standalone Firmware Flashing (Downgrade / Upgrade)**

Flash signed official firmware payloads (e.g. downgrading from v3.0.1 to v2.5.0) directly over Bluetooth RFCOMM:

```sh
python3 tools/flash_firmware.py --mac AC:80:0A:2E:45:80 --firmware path/to/firmware_v2.5.0.bin
```

**5. Acoustic Tuning & Soundstage Expansion Preset**

For immediate pristine acoustic balance (eliminating the bloated 200 Hz bass hump and expanding the stereo soundstage outside your ears):

* **Linux (EasyEffects):**
  ```sh
  # Copy preset to EasyEffects output folder
  cp presets/Sony_WH-1000XM4_SuperReview_3D_Spatial.json ~/.var/app/com.github.wwmm.easyeffects/data/easyeffects/output/

  # Load preset directly
  flatpak run --command=easyeffects com.github.wwmm.easyeffects -l Sony_WH-1000XM4_SuperReview_3D_Spatial
  ```
* **Windows (Equalizer APO):**
  Import `Sony_WH-1000XM4_ANC_on_SuperReview_EQ.txt` into the Equalizer APO Configuration Editor.

**6. Instant Rollback / Factory Restore**

If you ever want to return 100% back to stock, restore your 128 KB Partition 6 backup:

```sh
PYTHONPATH=. python3 -m xm4_optimizer --mac AC:80:0A:2E:45:80 rollback factory_nvdm_backup.bin
```

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

# 2. Backup the full 512 KB NVDM calibration image over Bluetooth RFCOMM (auto-detects MAC)
python3 tools/backup_nvdm.py
# (Or specify MAC and custom output manually):
python3 tools/backup_nvdm.py --mac AC:80:0A:2E:45:80 --out wh1000xm4_nvdm_backup.bin

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

The test suite includes **407 tests** covering MDR/RACE wire framing, RFCOMM socket stress/fault injection, 128 KB NVDM parser fuzzing, Q28/Q31 fixed-point extremes, adversarial pole stability boundary checks, acoustic response verification, and offline AeroSpatial DSP checks:

```sh
pytest -v
```

---

## 5. Project Roadmap

| Phase | Milestone | Scope & Architecture | Status |
|---|---|---|---|
| **Phase 1** | **ANC Hardware Optimization & Firmware Flashing** | Reverse engineered NVDM Partition 6, QN1 biquad filter calibration, and RFCOMM FOTA flasher (`tools/flash_firmware.py`). | **Completed** |
| **Phase 2** | **Acoustic Profiling & Soundstage Expansion** | Super* Review target profile, Bauer BS2B crossfeed (700 Hz / 5.0 dB), and 125% M/S lateral soundstage expansion. | **Completed** |
| **Phase 3** | **Android Companion Service (Custom Sony Connect)** | Sony MDR V2 RFCOMM listener for `unitRemove` / `unitWear` wear detection packets with rootless Android `AudioManager.setCommunicationDevice` call routing to earpiece. | **Queued (Next Stage)** |
| **Phase 4** | **Spatial audio research and Linux prototype** | Steam Audio stereo virtual-speaker prototype and per-app PipeWire routing experiment. Spotify audio reached the trial path, but user listening found muffled sound, EQ bypass, and center collapse. Multichannel/object-based input and system-wide playback remain future work. | **Prototype; failed listening acceptance; investigate before use** |
