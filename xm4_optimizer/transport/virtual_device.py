"""Virtual XM4 Mock Device Transport and Flash Emulator.

Provides an in-memory simulation of the Sony WH-1000XM4 Bluetooth RFCOMM
subsystem (Channel 11 MDR and Channel 21 RACE/ATCI), 128KB NOR Flash Partition 6,
and CXD90050 DSP registers.
"""

from __future__ import annotations

import struct
from typing import Dict, List, Optional

from xm4_optimizer.nvdm.cxd90050 import (
    CXD90050AlignmentBlock,
    FILTER_ID_ALIGN_FB_L,
    FILTER_ID_ALIGN_FB_R,
    FILTER_ID_ALIGN_FF_L,
    FILTER_ID_ALIGN_FF_R,
    HowlingDetectionParam,
    HowlingPreventionParam,
    OptimizerParam,
)
from xm4_optimizer.transport.protocol_mdr import (
    END_MARKER,
    MdrError,
    MdrPacket,
    MdrPacketType,
    START_MARKER,
)
from xm4_optimizer.transport.protocol_race import (
    compute_fota_checksum,
    FLASH_PAGE_SIZE,
    RACE_HEADER_FORMAT,
    RaceHead,
    RaceHeaderType,
    RaceOpcode,
    RaceReturnCode,
)

# Flash Geometry Constants
NVDM_BASE_ADDR: int = 0x000DF000
NVDM_PARTITION_SIZE: int = 128 * 1024  # 131,072 bytes (128 KB)
NVDM_PAGE_SIZE: int = 256
NVDM_PEB_SIZE: int = 4096
NVDM_PEB_COUNT: int = 32

PEB_MAGIC_NVDM: int = 0x4D44564E  # "NVDM" in ASCII LE
PEB_STATUS_ACTIVED: int = 0xE0
PEB_STATUS_EMPTY: int = 0xFE
DATA_ITEM_STATUS_VALID: int = 0xFC
DATA_ITEM_STATUS_DELETE: int = 0xF8


def bkdr_hash(group: str, name: str) -> int:
    """Computes 32-bit polynomial rolling BKDR hash over group and item name."""
    s = (group + name).encode("ascii")
    h, a, b = 0, 63689, 378551
    for char in s:
        h = (h * a + char) & 0xFFFFFFFF
        a = (a * b) & 0xFFFFFFFF
    return h


def compute_nvdm_interleaved_checksum(data: bytes) -> int:
    """Computes 16-bit interleaved accumulator checksum:

    low byte = sum of even indexed bytes & 0xFF
    high byte = sum of odd indexed bytes & 0xFF
    """
    even_sum = sum(data[0::2]) & 0xFF
    odd_sum = sum(data[1::2]) & 0xFF
    return (odd_sum << 8) | even_sum


class VirtualXM4Device:
    """In-memory mock device implementing DeviceTransport protocol."""

    def __init__(self, populate_defaults: bool = True):
        self.is_connected: bool = False
        self.flash: bytearray = bytearray(b"\xff" * NVDM_PARTITION_SIZE)
        self.dsp_registers: Dict[str, List[int]] = {
            "ALIGN_FF_L": [0, 0, 1 << 28, 0, 0] * 5,
            "ALIGN_FF_R": [0, 0, 1 << 28, 0, 0] * 5,
            "ALIGN_FB_L": [0, 0, 1 << 28, 0, 0] * 5,
            "ALIGN_FB_R": [0, 0, 1 << 28, 0, 0] * 5,
        }
        # Fault Injection Flags
        self.fail_read: bool = False
        self.fail_write: bool = False
        self.corrupt_fota_crc: bool = False
        self.disconnect_on_write: bool = False
        self.write_count: int = 0
        self.read_count: int = 0

        if populate_defaults:
            self._seed_default_nvdm_partition()

    def connect(self) -> None:
        """Simulates establishing RFCOMM connection."""
        self.is_connected = True

    def close(self) -> None:
        """Simulates closing RFCOMM connection."""
        self.is_connected = False

    # --- DeviceTransport Protocol Implementation ---

    def read_flash_page(self, address: int) -> bytes:
        """Reads 256 bytes from NOR flash at 256-byte aligned address."""
        if not self.is_connected:
            raise ConnectionError("Device is not connected")
        if self.fail_read:
            raise IOError("Simulated flash read hardware error (RACE code 0x01)")
        if address < NVDM_BASE_ADDR or address + NVDM_PAGE_SIZE > (
            NVDM_BASE_ADDR + NVDM_PARTITION_SIZE
        ):
            raise ValueError(f"Flash address 0x{address:08X} out of partition bounds")
        if address % NVDM_PAGE_SIZE != 0:
            raise ValueError(
                f"Flash address 0x{address:08X} is not 256-byte aligned"
            )

        self.read_count += 1
        offset = address - NVDM_BASE_ADDR
        return bytes(self.flash[offset : offset + NVDM_PAGE_SIZE])

    def write_flash_page(self, address: int, data: bytes) -> bool:
        """Writes 256 bytes to NOR flash with CRC-8 fota_checksum validation."""
        if not self.is_connected:
            raise ConnectionError("Device is not connected")
        if self.disconnect_on_write:
            self.is_connected = False
            raise ConnectionResetError(
                "Connection reset by peer during staged flash write"
            )
        if self.fail_write:
            return False
        if len(data) != NVDM_PAGE_SIZE:
            raise ValueError(
                f"Data length must be exactly {NVDM_PAGE_SIZE} bytes, got {len(data)}"
            )
        if address < NVDM_BASE_ADDR or address + NVDM_PAGE_SIZE > (
            NVDM_BASE_ADDR + NVDM_PARTITION_SIZE
        ):
            raise ValueError(f"Flash address 0x{address:08X} out of partition bounds")
        if address % NVDM_PAGE_SIZE != 0:
            raise ValueError(
                f"Flash address 0x{address:08X} is not 256-byte aligned"
            )

        calc_crc = compute_fota_checksum(data)
        if self.corrupt_fota_crc:
            return False  # Firmware rejects with checksum mismatch

        self.write_count += 1
        offset = address - NVDM_BASE_ADDR
        self.flash[offset : offset + NVDM_PAGE_SIZE] = data
        return True

    def send_atci_command(self, cmd: str, timeout_ms: int = 3000) -> str:
        """Executes Hayes ATCI command string and returns response."""
        if not self.is_connected:
            raise ConnectionError("Device is not connected")

        cmd = cmd.strip()
        if cmd == "AT":
            return "\r\nOK\r\n"

        if cmd.startswith("AT+HWPTOOL=COEFSET"):
            # Syntax: AT+HWPTOOL=COEFSET,<target>,<filter_idx>,<a1>,<a2>,<b0>,<b1>,<b2>
            parts = cmd.split(",")
            if len(parts) >= 8:
                try:
                    target_idx = int(parts[1])
                    stage_idx = int(parts[2])
                    if not (0 <= target_idx < 4 and 0 <= stage_idx < 5):
                        return "\r\n+HWPTOOL: ERROR,INVALID_PARAMS\r\n\r\nERROR\r\n"
                    coeffs = [int(p, 16) for p in parts[3:8]]
                    target_name = ["ALIGN_FF_L", "ALIGN_FF_R", "ALIGN_FB_L", "ALIGN_FB_R"][
                        target_idx
                    ]
                    start_idx = stage_idx * 5
                    self.dsp_registers[target_name][start_idx : start_idx + 5] = coeffs
                    return "\r\n+HWPTOOL: COEFSET,OK\r\n\r\nOK\r\n"
                except Exception:
                    return "\r\n+HWPTOOL: ERROR,INVALID_PARAMS\r\n\r\nERROR\r\n"
            return "\r\n+HWPTOOL: ERROR,INVALID_PARAMS\r\n\r\nERROR\r\n"

        if cmd.startswith("AT+HWPTOOL=COEFGET"):
            parts = cmd.split(",")
            if len(parts) >= 3:
                try:
                    target_idx = int(parts[1])
                    stage_idx = int(parts[2])
                    if not (0 <= target_idx < 4 and 0 <= stage_idx < 5):
                        return "\r\n+HWPTOOL: ERROR,INVALID_PARAMS\r\n\r\nERROR\r\n"
                    target_name = ["ALIGN_FF_L", "ALIGN_FF_R", "ALIGN_FB_L", "ALIGN_FB_R"][
                        target_idx
                    ]
                    start_idx = stage_idx * 5
                    coeffs = self.dsp_registers[target_name][start_idx : start_idx + 5]
                    coeff_str = ",".join(f"0x{c:08X}" for c in coeffs)
                    return f"\r\n+HWPTOOL: COEFGET,{target_idx},{stage_idx},{coeff_str}\r\n\r\nOK\r\n"
                except Exception:
                    return "\r\nERROR\r\n"

        if cmd.startswith("AT+ENVDM=W"):
            return "\r\n+ENVDM: W,OK\r\n\r\nOK\r\n"

        return "\r\nERROR\r\n"

    # --- Wire Protocol Simulation (RACE & MDR) ---

    def process_race_packet(self, packet: bytes) -> bytes:
        """Processes raw RACE binary frame and returns raw response frame."""
        if len(packet) < 6:
            raise ValueError("RACE packet too short")
        head, type_, length, cmd_id = struct.unpack(RACE_HEADER_FORMAT, packet[:6])
        payload = packet[6 : 4 + length]

        if cmd_id == RaceOpcode.PAGE_READ:
            storage_type, page_shift, address = struct.unpack("<BBI", payload[:6])
            try:
                page_data = self.read_flash_page(address)
                resp_payload = struct.pack("<BI", 0x00, address) + page_data
                resp_hdr = struct.pack(
                    RACE_HEADER_FORMAT, RaceHead.STANDARD, RaceHeaderType.RESPONSE, len(resp_payload) + 2, RaceOpcode.PAGE_READ
                )
                return resp_hdr + resp_payload
            except Exception:
                err_payload = struct.pack("<BI", 0x01, address)
                resp_hdr = struct.pack(
                    RACE_HEADER_FORMAT, RaceHead.STANDARD, RaceHeaderType.RESPONSE, len(err_payload) + 2, RaceOpcode.PAGE_READ
                )
                return resp_hdr + err_payload

        elif cmd_id == RaceOpcode.PAGE_PROGRAM:
            if len(payload) < 7:
                err_payload = struct.pack("<BI", RaceReturnCode.INVALID_PARAM, 0)
                resp_hdr = struct.pack(
                    RACE_HEADER_FORMAT,
                    RaceHead.STANDARD,
                    RaceHeaderType.RESPONSE,
                    len(err_payload) + 2,
                    RaceOpcode.PAGE_PROGRAM,
                )
                return resp_hdr + err_payload

            storage_type, num_pages, fota_crc, address = struct.unpack("<BBBI", payload[:7])
            page_data = payload[7 : 7 + 256]
            calc_crc = compute_fota_checksum(page_data)
            if calc_crc != fota_crc or self.corrupt_fota_crc:
                resp_payload = struct.pack("<BI", 0x02, address)  # Checksum error
            else:
                try:
                    ok = self.write_flash_page(address, page_data)
                    ret_code = RaceReturnCode.SUCCESS if ok else RaceReturnCode.INVALID_PARAM
                except (ConnectionError, ConnectionResetError):
                    raise
                except ValueError as exc:
                    if "aligned" in str(exc).lower():
                        ret_code = RaceReturnCode.ADDR_MISALIGNED  # 0x04
                    else:
                        ret_code = RaceReturnCode.INVALID_PARAM  # 0x01
                except Exception:
                    ret_code = RaceReturnCode.INVALID_PARAM  # 0x01
                resp_payload = struct.pack("<BI", int(ret_code), address)
            resp_hdr = struct.pack(
                RACE_HEADER_FORMAT, RaceHead.STANDARD, RaceHeaderType.RESPONSE, len(resp_payload) + 2, RaceOpcode.PAGE_PROGRAM
            )
            return resp_hdr + resp_payload

        elif cmd_id == RaceOpcode.ATCI_TUNNEL:
            cmd_str = payload.decode("ascii", errors="ignore")
            resp_str = self.send_atci_command(cmd_str)
            resp_payload = resp_str.encode("ascii")
            resp_hdr = struct.pack(
                RACE_HEADER_FORMAT, RaceHead.STANDARD, RaceHeaderType.RESPONSE, len(resp_payload) + 2, RaceOpcode.ATCI_TUNNEL
            )
            return resp_hdr + resp_payload

        # Default unknown command response
        err_payload = struct.pack("<B", 0xFF)
        return (
            struct.pack(RACE_HEADER_FORMAT, RaceHead.STANDARD, RaceHeaderType.RESPONSE, len(err_payload) + 2, cmd_id)
            + err_payload
        )

    def process_mdr_packet(self, packet: bytes) -> bytes:
        """Processes raw MDR packet with delimiters and escaping."""
        if len(packet) < 2 or packet[0] != START_MARKER or packet[-1] != END_MARKER:
            raise ValueError("Invalid MDR framing delimiters")

        pkt, remaining = MdrPacket.from_wire_bytes(packet)
        if pkt is None or remaining:
            raise ValueError("Invalid MDR packet framing or incomplete packet")

        # Send ACK (Type=0x02)
        return self._format_mdr_frame(int(MdrPacketType.ACK), pkt.seq_num, b"")

    def _format_mdr_frame(self, pkt_type: int, seq: int, payload: bytes) -> bytes:
        """Builds framed and escaped MDR response packet."""
        inner = struct.pack(">BBI", pkt_type, seq, len(payload)) + payload
        cksum = sum(inner) & 0xFF
        inner += bytes([cksum])

        escaped = bytearray([0x3E])
        for b in inner:
            if b in (0x3C, 0x3D, 0x3E):
                escaped.extend([0x3D, (b - 0x10) & 0xFF])
            else:
                escaped.append(b)
        escaped.append(0x3C)
        return bytes(escaped)

    # --- Seed Helper ---

    def _seed_default_nvdm_partition(self) -> None:
        """Pre-populates PEB 0 with valid NVDM header and default items."""
        # PEB 0 Header at offset 0
        peb_hdr = struct.pack(
            "<IIBBBB", PEB_MAGIC_NVDM, 1, PEB_STATUS_ACTIVED, 0xF0, 0x01, 0x00
        )
        self.flash[0:12] = peb_hdr

        offset = 12
        seq = 1

        items_to_seed = [
            (
                "DNC",
                "ALIGN_FF_L",
                CXD90050AlignmentBlock(filter_id=FILTER_ID_ALIGN_FF_L).pack(),
            ),
            (
                "DNC",
                "ALIGN_FF_R",
                CXD90050AlignmentBlock(filter_id=FILTER_ID_ALIGN_FF_R).pack(),
            ),
            (
                "DNC",
                "ALIGN_FB_L",
                CXD90050AlignmentBlock(filter_id=FILTER_ID_ALIGN_FB_L).pack(),
            ),
            (
                "DNC",
                "ALIGN_FB_R",
                CXD90050AlignmentBlock(filter_id=FILTER_ID_ALIGN_FB_R).pack(),
            ),
            ("DNC", "HOWLING_PRE_PARAM", HowlingPreventionParam().pack()),
            ("DNC", "HOWLING_DET_PARAM", HowlingDetectionParam().pack()),
            ("DNC", "OPTIMIZER_PARAM", OptimizerParam().pack()),
        ]

        for grp, name, val in items_to_seed:
            grp_b = grp.encode("ascii")
            name_b = name.encode("ascii")
            h = bkdr_hash(grp, name)
            hdr = struct.pack(
                "<BBHHBBHBBII",
                DATA_ITEM_STATUS_VALID,
                0,
                0,
                offset,
                len(grp_b),
                len(name_b),
                len(val),
                0,
                1,
                seq,
                h,
            )
            cksum_target = hdr[1:20] + grp_b + name_b + val
            cksum = compute_nvdm_interleaved_checksum(cksum_target)
            item_bytes = hdr + grp_b + name_b + val + struct.pack("<H", cksum)
            pad = (4 - (len(item_bytes) % 4)) % 4
            item_bytes += b"\xff" * pad

            self.flash[offset : offset + len(item_bytes)] = item_bytes
            offset += len(item_bytes)
            seq += 1
