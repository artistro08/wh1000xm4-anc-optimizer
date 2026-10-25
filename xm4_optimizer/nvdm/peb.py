"""Airoha NVDM Physical Erase Block (PEB) Layout and Lifecycle Management.

Defines the 12-byte PEB header structure (<IIBBBB), magic number validation (0x4D44564E),
and NOR flash wear-leveling state transitions.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
import struct
from typing import Optional

PEB_SIZE: int = 4096
TOTAL_PEBS: int = 32
NVDM_PARTITION_SIZE: int = PEB_SIZE * TOTAL_PEBS  # 131,072 bytes (128 KB)
NVDM_BASE_ADDRESS: int = 0x000DF000
PEB_MAGIC: int = 0x4D44564E  # "NVDM" in ASCII little-endian
PEB_HEADER_SIZE: int = 12
PEB_DATA_START_OFFSET: int = 12
PEB_USABLE_SIZE: int = PEB_SIZE - PEB_HEADER_SIZE  # 4084 bytes
PEB_HEADER_STRUCT: str = "<IIBBBB"


class PebStatus(IntEnum):
    VIRGIN = 0xFF      # Unformatted flash
    EMPTY = 0xFE       # Formatted, ready for allocation
    ACTIVING = 0xFC    # Transitioning to active
    TRANSFERED = 0xF0  # Items migrated during GC
    ACTIVED = 0xE0     # Active block storing items
    RECLAIMING = 0xC0  # Under garbage collection
    UNKNOWN = 0x00


@dataclass(frozen=True)
class PebHeader:
    magic: int
    erase_count: int
    status: PebStatus
    peb_reserved: int
    version: int
    reserved: int
    pnum: int = 0
    raw_status: int = 0xFF

    @property
    def is_valid_magic(self) -> bool:
        return self.magic == PEB_MAGIC

    @property
    def is_virgin(self) -> bool:
        return self.magic == 0xFFFFFFFF and self.status == PebStatus.VIRGIN

    @property
    def is_empty(self) -> bool:
        return self.status == PebStatus.EMPTY or self.is_virgin

    @property
    def is_active(self) -> bool:
        return self.status in (PebStatus.ACTIVED, PebStatus.ACTIVING)

    @property
    def is_reclaiming(self) -> bool:
        return self.status in (PebStatus.RECLAIMING, PebStatus.TRANSFERED)

    def can_transition_to(self, target_status: PebStatus) -> bool:
        """Verifies NOR flash bit-clearing invariant: bits can only flip 1 -> 0."""
        return (int(self.status) & int(target_status)) == int(target_status)


def parse_peb_header(data: bytes, pnum: int = 0) -> PebHeader:
    """Parses a 12-byte PEB header from binary slice."""
    if len(data) < PEB_HEADER_SIZE:
        raise ValueError(
            f"PEB header buffer too short: {len(data)} bytes, expected {PEB_HEADER_SIZE}"
        )

    magic, erase_count, status_raw, peb_res, ver, res = struct.unpack(
        PEB_HEADER_STRUCT, data[:PEB_HEADER_SIZE]
    )
    try:
        status = PebStatus(status_raw)
    except ValueError:
        status = PebStatus.UNKNOWN

    return PebHeader(
        magic=magic,
        erase_count=erase_count,
        status=status,
        peb_reserved=peb_res,
        version=ver,
        reserved=res,
        pnum=pnum,
        raw_status=status_raw,
    )


def serialize_peb_header(header: PebHeader) -> bytes:
    """Serializes a PebHeader to 12 bytes."""
    return struct.pack(
        PEB_HEADER_STRUCT,
        header.magic,
        header.erase_count,
        int(header.status),
        header.peb_reserved,
        header.version,
        header.reserved,
    )
