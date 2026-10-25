"""Airoha RACE Protocol Framing, Opcodes, and Checksums.

Implements the Airoha RACE protocol framing (<BBHH), command and response
serialization for flash page read/program and ATCI command tunneling, and
the Dallas/Maxim CRC-8 FOTA checksum.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
import struct
from typing import Optional, Tuple

RACE_HEADER_FORMAT: str = "<BBHH"
RACE_HEADER_SIZE: int = struct.calcsize(RACE_HEADER_FORMAT)  # 6 bytes
FLASH_PAGE_SIZE: int = 256  # 0x100 bytes


class RaceHead(IntEnum):
    STANDARD = 0x05
    FOTA = 0x15


class RaceHeaderType(IntEnum):
    CMD_EXPECTS_RESPONSE = 0x5A
    RESPONSE = 0x5B
    CMD_EXPECTS_NO_RESPONSE = 0x5C
    INDICATION = 0x5D


class RaceOpcode(IntEnum):
    PAGE_READ = 0x0403        # RACE_STORAGE_PAGE_READ
    PAGE_PROGRAM = 0x0402     # RACE_STORAGE_PAGE_PROGRAM
    PARTITION_ERASE = 0x0404  # RACE_STORAGE_PARTITION_ERASE
    ATCI_TUNNEL = 0x0A00      # RACE_ATCI_TUNNEL
    READ_SDK_VERSION = 0x0301
    GET_BUILD_VERSION = 0x1E08
    READ_ADDRESS = 0x1680


class RaceStorageType(IntEnum):
    NOR_FLASH = 0x00
    NVDM = 0x01


class RaceReturnCode(IntEnum):
    SUCCESS = 0x00
    INVALID_PARAM = 0x01
    FLASH_LOCKED = 0x02
    CHECKSUM_ERROR = 0x03
    ADDR_MISALIGNED = 0x04
    TIMEOUT = 0x05
    GENERIC_ERROR = 0xFF


def generate_crc8_table() -> list[int]:
    """Generates 256-entry lookup table for polynomial 0x31 (Maxim/Dallas)."""
    table: list[int] = []
    poly = 0x31
    for byte in range(256):
        curr = byte
        for _ in range(8):
            if curr & 0x80:
                curr = ((curr << 1) ^ poly) & 0xFF
            else:
                curr = (curr << 1) & 0xFF
        table.append(curr)
    return table


RACE_CRC8_TABLE: list[int] = generate_crc8_table()


def compute_fota_checksum(data: bytes) -> int:
    """Computes Airoha hardware fota_checksum across data bytes using Dallas/Maxim CRC8."""
    crc = 0
    for b in data:
        crc = RACE_CRC8_TABLE[crc ^ b]
    return crc


# --- Exception Hierarchy ---


class RaceError(Exception):
    """Base exception for all RACE protocol errors."""


class RaceFramingError(RaceError):
    """Raised when incoming byte stream has invalid RACE header or length."""


class RaceLengthError(RaceError):
    """Raised when packet length field is invalid (< 2 bytes)."""


class RaceChecksumError(RaceError):
    """Raised when hardware fota_checksum validation fails."""


class RaceResponseError(RaceError):
    """Raised when a RACE command returns a non-zero return code."""

    def __init__(self, message: str, return_code: int, address: Optional[int] = None):
        addr_str = f", address=0x{address:08X}" if address is not None else ""
        super().__init__(f"{message} (return_code=0x{return_code:02X}{addr_str})")
        self.return_code = return_code
        self.address = address


# --- Packet Models ---


@dataclass(frozen=True)
class RacePacket:
    head: int = RaceHead.STANDARD
    type: int = RaceHeaderType.CMD_EXPECTS_RESPONSE
    cmd_id: int = 0
    payload: bytes = b""

    def to_bytes(self) -> bytes:
        length = len(self.payload) + 2
        header = struct.pack(RACE_HEADER_FORMAT, self.head, self.type, length, self.cmd_id)
        return header + self.payload

    @classmethod
    def from_bytes(cls, data: bytes) -> Tuple["RacePacket", bytes]:
        if len(data) < RACE_HEADER_SIZE:
            raise RaceFramingError(f"Buffer too short for RACE header ({len(data)} < {RACE_HEADER_SIZE})")
        head, type_, length, cmd_id = struct.unpack(RACE_HEADER_FORMAT, data[:RACE_HEADER_SIZE])
        if length < 2:
            raise RaceLengthError(f"Invalid RACE length {length} (must be >= 2)")
        payload_len = length - 2
        total_len = RACE_HEADER_SIZE + payload_len
        if len(data) < total_len:
            raise RaceFramingError(f"Incomplete packet: expected {total_len} bytes, got {len(data)}")
        payload = data[RACE_HEADER_SIZE:total_len]
        remaining = data[total_len:]
        return cls(head=head, type=type_, cmd_id=cmd_id, payload=payload), remaining


@dataclass(frozen=True)
class PageReadRequest:
    address: int
    storage_type: int = RaceStorageType.NOR_FLASH
    page_count: int = 1

    def to_packet(self) -> RacePacket:
        if self.address % FLASH_PAGE_SIZE != 0:
            raise ValueError(f"Flash address 0x{self.address:08X} must be aligned to 256 bytes")
        payload = struct.pack("<BBI", self.storage_type, self.page_count, self.address)
        return RacePacket(
            head=RaceHead.STANDARD,
            type=RaceHeaderType.CMD_EXPECTS_RESPONSE,
            cmd_id=RaceOpcode.PAGE_READ,
            payload=payload,
        )


@dataclass(frozen=True)
class PageReadResponse:
    return_code: int
    address: int
    data: bytes = b""

    @classmethod
    def from_payload(cls, payload: bytes) -> "PageReadResponse":
        if len(payload) < 5:
            raise RaceFramingError(f"Payload too short for PageReadResponse ({len(payload)}B)")
        return_code, address = struct.unpack("<BI", payload[:5])
        if return_code != 0:
            return cls(return_code=return_code, address=address, data=b"")
        if len(payload) < 5 + FLASH_PAGE_SIZE:
            raise RaceFramingError(f"Payload too short for PageReadResponse data ({len(payload)}B)")
        data = payload[5:5 + FLASH_PAGE_SIZE]
        return cls(return_code=return_code, address=address, data=data)


@dataclass(frozen=True)
class PageProgramRequest:
    address: int
    data: bytes
    storage_type: int = RaceStorageType.NOR_FLASH
    page_count: int = 1

    def to_packet(self) -> RacePacket:
        if self.address % FLASH_PAGE_SIZE != 0:
            raise ValueError(f"Flash address 0x{self.address:08X} must be aligned to 256 bytes")
        if len(self.data) != FLASH_PAGE_SIZE:
            raise ValueError(f"Data length must be exactly {FLASH_PAGE_SIZE} bytes, got {len(self.data)}")
        checksum = compute_fota_checksum(self.data)
        payload = struct.pack("<BBBI", self.storage_type, self.page_count, checksum, self.address) + self.data
        return RacePacket(
            head=RaceHead.FOTA,
            type=RaceHeaderType.CMD_EXPECTS_RESPONSE,
            cmd_id=RaceOpcode.PAGE_PROGRAM,
            payload=payload,
        )


@dataclass(frozen=True)
class PageProgramResponse:
    return_code: int
    address: int

    @classmethod
    def from_payload(cls, payload: bytes) -> "PageProgramResponse":
        if len(payload) < 5:
            raise RaceFramingError(f"Payload too short for PageProgramResponse ({len(payload)}B)")
        return_code, address = struct.unpack("<BI", payload[:5])
        return cls(return_code=return_code, address=address)


@dataclass(frozen=True)
class PartitionEraseRequest:
    address: int
    length: int
    storage_type: int = RaceStorageType.NOR_FLASH

    def to_packet(self) -> RacePacket:
        payload = struct.pack("<BII", self.storage_type, self.length, self.address)
        return RacePacket(
            head=RaceHead.STANDARD,
            type=RaceHeaderType.CMD_EXPECTS_RESPONSE,
            cmd_id=RaceOpcode.PARTITION_ERASE,
            payload=payload,
        )


@dataclass(frozen=True)
class PartitionEraseResponse:
    return_code: int

    @classmethod
    def from_payload(cls, payload: bytes) -> "PartitionEraseResponse":
        if len(payload) < 1:
            raise RaceFramingError(f"Payload too short for PartitionEraseResponse ({len(payload)}B)")
        return cls(return_code=payload[0])


@dataclass(frozen=True)
class AtciTunnelRequest:
    command: str

    def to_packet(self) -> RacePacket:
        cmd_str = self.command.strip() + "\r\n"
        payload = cmd_str.encode("ascii")
        return RacePacket(
            head=RaceHead.STANDARD,
            type=RaceHeaderType.CMD_EXPECTS_RESPONSE,
            cmd_id=RaceOpcode.ATCI_TUNNEL,
            payload=payload,
        )


@dataclass(frozen=True)
class AtciTunnelResponse:
    response_text: str

    @classmethod
    def from_payload(cls, payload: bytes) -> "AtciTunnelResponse":
        return cls(response_text=payload.decode("ascii", errors="replace"))
