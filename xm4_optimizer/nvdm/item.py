"""Airoha NVDM Data Item Parsing, Checksum, and BKDR Hash Calculation.

Implements the 20-byte item header (<BBHHBBHBBII), 16-bit interleaved accumulator checksum
(excluding the status byte for in-place deletion invariance), and 32-bit BKDR rolling hash.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
import struct
from typing import Tuple

DATA_ITEM_HEADER_SIZE: int = 20
DATA_ITEM_HEADER_STRUCT: str = "<BBHHBBHBBII"
CHECKSUM_SIZE: int = 2
CHECKSUM_STRUCT: str = "<H"

BKDR_SEED_A: int = 63689
BKDR_SEED_B: int = 378551
MOD_32: int = 0xFFFFFFFF


class DataItemStatus(IntEnum):
    EMPTY = 0xFF    # Unwritten flash space (EOF)
    WRITING = 0xFE  # In-progress write
    VALID = 0xFC    # Active committed item
    DELETE = 0xF8   # Deleted or superseded item
    UNKNOWN = 0x00


class DataItemType(IntEnum):
    RAW = 0x01     # Binary data
    STRING = 0x02  # String data


@dataclass(frozen=True)
class NvdmItemHeader:
    status: DataItemStatus
    pnum: int
    reserved: int
    offset: int
    group_name_size: int
    data_item_name_size: int
    value_size: int
    index: int
    data_type: DataItemType
    sequence_number: int
    hash_name: int
    raw_status: int = 0xFF

    @property
    def total_record_size(self) -> int:
        return (
            DATA_ITEM_HEADER_SIZE
            + self.group_name_size
            + self.data_item_name_size
            + self.value_size
            + CHECKSUM_SIZE
        )


@dataclass
class NvdmItem:
    header: NvdmItemHeader
    group_name: str
    item_name: str
    value: bytes
    stored_checksum: int
    calculated_checksum: int
    is_checksum_valid: bool
    is_hash_valid: bool

    @property
    def key(self) -> Tuple[str, str]:
        return (self.group_name, self.item_name)

    @property
    def is_valid(self) -> bool:
        return (
            self.header.status == DataItemStatus.VALID
            and self.is_checksum_valid
            and self.is_hash_valid
        )

    @property
    def is_deleted(self) -> bool:
        return self.header.status == DataItemStatus.DELETE


def calculate_data_item_hash(group_name: str, item_name: str) -> int:
    """Calculates the 32-bit BKDR polynomial rolling hash over group_name + item_name."""
    s = (group_name + item_name).encode("ascii", errors="replace")
    h = 0
    a = BKDR_SEED_A
    b = BKDR_SEED_B
    for char in s:
        h = (h * a + char) & MOD_32
        a = (a * b) & MOD_32
    return h


def airoha_c_checksum(chk: int, buf: bytes) -> int:
    """Airoha hardware 16-bit interleaved accumulator checksum across binary slice."""
    b0 = chk & 0xFF
    b1 = (chk >> 8) & 0xFF
    for i, b in enumerate(buf):
        if i & 1:
            b1 = (b1 + b) & 0xFF
        else:
            b0 = (b0 + b) & 0xFF
    return (b1 << 8) | b0


def calculate_data_item_checksum(
    header_1_to_19: bytes,
    group_name_bytes: bytes,
    item_name_bytes: bytes,
    value_bytes: bytes,
) -> int:
    """Calculates the 16-bit interleaved accumulator checksum.

    Low byte = sum(even indexed bytes) % 256.
    High byte = sum(odd indexed bytes) % 256.
    Note: Explicitly excludes byte 0 (status) of the header to preserve validity on in-place deletion.
    """
    d = header_1_to_19 + group_name_bytes + item_name_bytes + value_bytes
    s_even = sum(d[0::2]) & 0xFF
    s_odd = sum(d[1::2]) & 0xFF
    return (s_odd << 8) | s_even


def parse_item_header(data: bytes) -> NvdmItemHeader:
    """Parses a 20-byte item header."""
    if len(data) < DATA_ITEM_HEADER_SIZE:
        raise ValueError(
            f"Item header buffer too short: {len(data)} bytes, expected {DATA_ITEM_HEADER_SIZE}"
        )

    (
        st_raw,
        pnum,
        res,
        offset,
        g_len,
        n_len,
        v_len,
        idx,
        dtype_raw,
        seq,
        h_val,
    ) = struct.unpack(DATA_ITEM_HEADER_STRUCT, data[:DATA_ITEM_HEADER_SIZE])

    try:
        status = DataItemStatus(st_raw)
    except ValueError:
        status = DataItemStatus.UNKNOWN

    try:
        data_type = DataItemType(dtype_raw)
    except ValueError:
        data_type = DataItemType.RAW

    return NvdmItemHeader(
        status=status,
        pnum=pnum,
        reserved=res,
        offset=offset,
        group_name_size=g_len,
        data_item_name_size=n_len,
        value_size=v_len,
        index=idx,
        data_type=data_type,
        sequence_number=seq,
        hash_name=h_val,
        raw_status=st_raw,
    )


def serialize_item_record(
    group_name: str,
    item_name: str,
    value: bytes,
    pnum: int,
    offset: int,
    sequence_number: int,
    status: DataItemStatus = DataItemStatus.VALID,
    index: int = 0,
    data_type: DataItemType = DataItemType.RAW,
    pad_to_4: bool = True,
) -> bytes:
    """Serializes a complete NVDM item record including header, strings, value, checksum, and padding."""
    group_b = group_name.encode("ascii")
    name_b = item_name.encode("ascii")
    hash_val = calculate_data_item_hash(group_name, item_name)

    header_no_status = struct.pack(
        "<BHHBBHBBII",
        pnum,
        0,
        offset,
        len(group_b),
        len(name_b),
        len(value),
        index,
        int(data_type),
        sequence_number,
        hash_val,
    )
    checksum = calculate_data_item_checksum(
        header_no_status, group_b, name_b, value
    )

    full_header = bytes([int(status)]) + header_no_status
    record = (
        full_header
        + group_b
        + name_b
        + value
        + struct.pack(CHECKSUM_STRUCT, checksum)
    )

    if pad_to_4:
        pad_len = (-len(record)) % 4
        record += b"\xFF" * pad_len

    return record
