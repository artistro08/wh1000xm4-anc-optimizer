# Bluetooth RFCOMM Wire Protocols: Sony MDR (Ch 11) & Airoha RACE (Ch 21)

This document specifies the binary wire framing, byte-stuffing rules, opcodes, and checksums used by `xm4_optimizer` to communicate with the **Sony WH-1000XM4** over Bluetooth Classic RFCOMM.

---

## 1. RFCOMM Service Discovery Map (Firmware v3.0.1)

An SDP browse (`sdptool browse <MAC>`) on a retail WH-1000XM4 (`v3.0.1`) exposes two vendor RFCOMM services of interest:

| RFCOMM Channel | Service Name | Service UUID | Protocol | Notes |
|---|---|---|---|---|
| **Channel 11** | `Serial MC` | `b9b213ce-eeab-49e4-8fd9-aa478ed1b26b` | **Sony MDR v1/v2** | Active control & FOTA mode switch channel on v3.0.1. (Channel 9 `Serial HPC` is advertised in SDP but ignores `00 00` connect frames on v3.0.1.) |
| **Channel 21** | `Airoha_APP` | `8901dfa8-5c7e-4d8f-9f0c-c2b70683f5f0` | **Airoha RACE / ATCI** | Direct binary flash access (`0x0402`/`0x0403`/`0x0404`) and Hayes ATCI command tunnel (`0x0F92`). |

---

## 2. Sony MDR v1/v2 Framed Protocol (RFCOMM Channel 11)

Implemented in [`xm4_optimizer/transport/protocol_mdr.py`](../xm4_optimizer/transport/protocol_mdr.py).

### 2.1 Unescaped Inner Structure

Before byte-stuffing, an MDR frame consists of:

```
+---------------+-------------+------------------+-------------------+---------------+
| Data Type (1B)| Seq Num (1B)| Payload Len (4B) | Payload (N bytes) | Checksum (1B) |
+---------------+-------------+------------------+-------------------+---------------+
|   uint8_t     |   uint8_t   |  uint32_t BE     |   uint8_t[N]      | sum(0..N+5)   |
+---------------+-------------+------------------+-------------------+---------------+
```

- **Checksum:** 8-bit unsigned sum modulo 256 (`sum(inner_bytes[:-1]) & 0xFF`).
- **Common Data Types:**
  - `0x00`: `DATA_MDR` (Standard MDR command/response)
  - `0x01`: `ACK` (`3e 01 <seq> 00 00 00 00 <chk> 3c`)
  - `0x0C`: `DATA_MDR_NO2` (Extended v2 capability / DNC / EQ tables)
  - `0x2E`: `SHOT_MDR_NO2`

### 2.2 Framing Delimiters & Byte Stuffing (`0x3D` Escape)

Every inner frame is byte-stuffed and wrapped between `START_MARKER = 0x3E` (`'>'`) and `END_MARKER = 0x3C` (`'<'`):

| Raw Byte | Escaped Two-Byte Sequence | Rule |
|---|---|---|
| `0x3C` (`END_MARKER`) | `0x3D 0x2C` | `[0x3D, (b - 0x10) & 0xFF]` |
| `0x3D` (`ESCAPE_BYTE`) | `0x3D 0x2D` | `[0x3D, (b - 0x10) & 0xFF]` |
| `0x3E` (`START_MARKER`) | `0x3D 0x2E` | `[0x3D, (b - 0x10) & 0xFF]` |

### 2.3 Firmware v3.0.1 Channel 11 Initial Handshake Quirk

Immediately upon `connect((bd_addr, 11))`, firmware v3.0.1 transmits an unsolicited 26-byte framed packet (`seq = 0`). The host **must** reply with an ACK for `seq = 0`:

```
3e 01 00 00 00 00 00 01 3c
```

Only after this initial ACK does the headset respond to `CONNECT_GET_PROTOCOL_INFO` (`0x00 0x00`) or `UPDT_SET_STATUS` (`0x06 0x01 0x00`, which enables update mode for `Airoha_APP` on Channel 21).

---

## 3. Airoha RACE Binary Protocol (RFCOMM Channel 21)

Implemented in [`xm4_optimizer/transport/protocol_race.py`](../xm4_optimizer/transport/protocol_race.py).

### 3.1 RACE Header (`6` bytes, `<BBHH`)

```
struct race_header_t {         // <BBHH (6 bytes, little-endian)
    uint8_t  head;             // 0x05 (Standard) or 0x15 (FOTA Extended)
    uint8_t  type;             // 0x5A = Command (Expects Response), 0x5B = Response, 0x5C = Cmd (No Resp), 0x5D = Indication
    uint16_t length;           // sizeof(cmd_id) + sizeof(payload) = 2 + N
    uint16_t cmd_id;           // RACE Opcode
};
```

### 3.2 Flash & ATCI Opcodes

| Opcode (`cmd_id`) | Name | Request Payload | Response Payload |
|---|---|---|---|
| `0x0403` | `FLASH_PAGE_READ` | `<BBI` (`storage_type=0x00`, `page_shift=0x01`, `uint32_t address`) | `<BI` (`return_code`, `uint32_t address`) + `uint8_t data[256]` |
| `0x0402` | `FLASH_PAGE_PROGRAM` | `<BBBI` (`storage_type=0x00`, `num_pages=0x01`, `uint8_t fota_crc8`, `uint32_t address`) + `uint8_t data[256]` | `<BI` (`return_code`, `uint32_t address`) |
| `0x0404` | `FLASH_PARTITION_ERASE` | `<BII` (`storage_type=0x00`, `uint32_t length`, `uint32_t address`) | `<BI` (`return_code`, `uint32_t address`) |
| `0x0F92` | `ATCI_TUNNEL` | ASCII AT command string terminated with `\r\n` | ASCII AT response (`+HWPTOOL: ...\r\n\r\nOK\r\n`) |

### 3.3 Airoha FOTA Page Checksum (`fota_crc8`)

Each 256-byte page written via `0x0402` (`FLASH_PAGE_PROGRAM`) includes a 1-byte `fota_crc8` computed using **CRC-8 Maxim/Dallas** (polynomial `0x31`, $x^8 + x^5 + x^4 + 1$, initial value `0x00`) with the final 8-bit register **bit-reversed**:

```python
def compute_fota_checksum(data: bytes) -> int:
    crc = 0x00
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0x31) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    # Bit-reverse the 8-bit CRC result
    rev = 0
    for i in range(8):
        if crc & (1 << i):
            rev |= 1 << (7 - i)
    return rev
```

### 3.4 ATCI DSP Coefficient Commands (`0x0F92`)

Live CXD90050 coefficient registers can be staged and queried through the RACE ATCI tunnel:
- **Stage Single Biquad Section:**
  ```
  AT+HWPTOOL=COEFSET,<filter_id>,<stage_idx>,0x<a1>,0x<a2>,0x<b0>,0x<b1>,0x<b2>
  ```
- **Read Back Single Biquad Section:**
  ```
  AT+HWPTOOL=COEFGET,<filter_id>,<stage_idx>
  -> +HWPTOOL: COEFGET,<filter_id>,<stage_idx>,0x<a1>,0x<a2>,0x<b0>,0x<b1>,0x<b2>\r\n\r\nOK\r\n
  ```
- **Commit Staged RAM Parameters to NVDM:**
  ```
  AT+ENVDM=W
  -> +ENVDM: W,OK\r\n\r\nOK\r\n
  ```
