"""Full 128KB Airoha NVDM Partition Image Parser and Serializer.

Scans 32 Physical Erase Blocks (PEBs) across 0x000DF000, validates PEB and Item headers,
computes 16-bit interleaved checksums and BKDR hashes, resolves duplicate items
via RFC 1982 modular sequence comparison, and extracts CXD90050 calibration parameters.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import struct
from typing import Any, Dict, Iterator, List, Optional, Tuple, Union

from xm4_optimizer.nvdm.cxd90050 import (
    CXD90050AlignmentBlock,
    HowlingDetectionParam,
    HowlingPreventionParam,
    OptimizerParam,
)
from xm4_optimizer.nvdm.item import (
    airoha_c_checksum,
    calculate_data_item_checksum,
    calculate_data_item_hash,
    CHECKSUM_SIZE,
    CHECKSUM_STRUCT,
    DATA_ITEM_HEADER_SIZE,
    DataItemStatus,
    NvdmItem,
    NvdmItemHeader,
    parse_item_header,
    serialize_item_record,
)
from xm4_optimizer.nvdm.peb import (
    NVDM_PARTITION_SIZE,
    parse_peb_header,
    PEB_DATA_START_OFFSET,
    PEB_MAGIC,
    PEB_SIZE,
    PebHeader,
    PebStatus,
    serialize_peb_header,
    TOTAL_PEBS,
)

# --- Exception Hierarchy ---


class NvdmError(Exception):
    """Base exception for all NVDM parsing and formatting errors."""


class NvdmCorruptError(NvdmError):
    """Raised when an NVDM partition image or block is corrupt."""


class NvdmChecksumError(NvdmError):
    """Raised when a data item fails interleaved checksum verification."""


class NvdmKeyNotFoundError(NvdmError):
    """Raised when a required key is absent from the partition."""


class NvdmHeaderError(NvdmError):
    """Raised when a PEB or Data Item header is truncated or malformed."""


# --- Sequence Number Comparison (RFC 1982) ---


def is_newer_sequence(new_seq: int, old_seq: int) -> bool:
    """Determines whether new_seq is newer than old_seq under 32-bit modular arithmetic."""
    diff = (new_seq - old_seq) & 0xFFFFFFFF
    return 0 < diff < 0x80000000


# --- Partition Container ---


@dataclass
class NvdmPartition:
    """Container holding parsed PEBs and active/corrupted NVDM items."""

    pebs: List[PebHeader]
    active_items: Dict[Tuple[str, str], NvdmItem]
    all_items: List[NvdmItem] = field(default_factory=list)
    corrupted_items: List[NvdmItem] = field(default_factory=list)

    def get_item(self, group_name: str, item_name: str) -> Optional[NvdmItem]:
        """Looks up an active item by group and item name."""
        return self.active_items.get((group_name, item_name))

    def get_value(self, group_name: str, item_name: str) -> Optional[bytes]:
        """Looks up an active item's raw value bytes."""
        item = self.get_item(group_name, item_name)
        return item.value if item is not None else None

    def get_item_by_hash(self, hash_name: int) -> Optional[NvdmItem]:
        """Looks up an active item by 32-bit BKDR hash."""
        for item in self.active_items.values():
            if item.header.hash_name == hash_name:
                return item
        return None

    def list_keys(self) -> List[Tuple[str, str]]:
        """Returns list of active (group, name) tuples."""
        return list(self.active_items.keys())

    def to_dict(self) -> Dict[str, Any]:
        """Exports dictionary of active items mapped as 'group/name': value."""
        return {f"{k[0]}/{k[1]}": item.value for k, item in self.active_items.items()}

    # --- Dictionary protocol compatibility ---

    def __getitem__(self, key: Union[Tuple[str, str], str]) -> NvdmItem:
        if isinstance(key, tuple):
            if key in self.active_items:
                return self.active_items[key]
        elif isinstance(key, str):
            # Try finding by item_name alone or 'group/name'
            if "/" in key:
                parts = tuple(key.split("/", 1))
                if parts in self.active_items:
                    return self.active_items[parts]
            for (g, n), itm in self.active_items.items():
                if n == key:
                    return itm
        raise KeyError(f"Key {key} not found in active NVDM partition items")

    def __contains__(self, key: Union[Tuple[str, str], str]) -> bool:
        if isinstance(key, tuple):
            return key in self.active_items
        elif isinstance(key, str):
            if "/" in key:
                return tuple(key.split("/", 1)) in self.active_items
            return any(n == key for (_, n) in self.active_items.keys())
        return False

    def get(self, key: Union[Tuple[str, str], str], default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default

    def keys(self) -> Iterator[Tuple[str, str]]:
        return iter(self.active_items.keys())

    def values(self) -> Iterator[NvdmItem]:
        return iter(self.active_items.values())

    def items(self) -> Iterator[Tuple[Tuple[str, str], NvdmItem]]:
        return iter(self.active_items.items())


# --- Image Parsing Engine ---


def parse_nvdm_image(
    image_bytes: bytes,
    strict: bool = False,
) -> NvdmPartition:
    """Parses an Airoha NVDM partition image (supports 128KB mock and 512KB hardware dumps)."""
    if len(image_bytes) not in (NVDM_PARTITION_SIZE, 512 * 1024):
        raise NvdmCorruptError(
            f"Invalid NVDM partition size: {len(image_bytes)} bytes (expected {NVDM_PARTITION_SIZE} or 524288)"
        )

    num_pebs = len(image_bytes) // PEB_SIZE
    pebs: List[PebHeader] = []
    all_items: List[NvdmItem] = []
    corrupted_items: List[NvdmItem] = []

    # 1. Parse all PEBs (32 for legacy mock or 128 for real hardware)
    for pnum in range(num_pebs):
        start = pnum * PEB_SIZE
        peb_bytes = image_bytes[start : start + PEB_SIZE]
        peb_hdr = parse_peb_header(peb_bytes, pnum=pnum)
        pebs.append(peb_hdr)

        # Skip empty or virgin PEBs
        if peb_hdr.is_empty:
            continue

        # Check magic on non-virgin block
        if not peb_hdr.is_valid_magic:
            if strict:
                raise NvdmCorruptError(f"PEB {pnum} invalid magic: 0x{peb_hdr.magic:08X}")
            continue

        # 2. Parse items in active or reclaiming PEB
        offset = PEB_DATA_START_OFFSET
        while offset + DATA_ITEM_HEADER_SIZE <= PEB_SIZE:
            st_byte = peb_bytes[offset]
            if st_byte == 0xFF:
                # Unwritten flash: end of items in this PEB
                break

            try:
                hdr = parse_item_header(peb_bytes[offset : offset + DATA_ITEM_HEADER_SIZE])
            except Exception:
                break

            core_len = hdr.total_record_size
            if offset + core_len > PEB_SIZE:
                # Corrupted item exceeding block bounds
                break

            # Slice item components
            curr = offset + DATA_ITEM_HEADER_SIZE
            group_b = peb_bytes[curr : curr + hdr.group_name_size]
            curr += hdr.group_name_size
            name_b = peb_bytes[curr : curr + hdr.data_item_name_size]
            curr += hdr.data_item_name_size
            val_b = peb_bytes[curr : curr + hdr.value_size]
            curr += hdr.value_size
            chk_b = peb_bytes[curr : curr + CHECKSUM_SIZE]
            stored_chk = struct.unpack(CHECKSUM_STRUCT, chk_b)[0]

            # Decode Strings & Verify Hash (strip null bytes if stored by C firmware)
            g_str = group_b.rstrip(b"\x00").decode("ascii", errors="replace")
            n_str = name_b.rstrip(b"\x00").decode("ascii", errors="replace")
            calc_h = calculate_data_item_hash(g_str, n_str)
            h_valid = (calc_h == hdr.hash_name) or (calculate_data_item_hash(group_b.decode("ascii", errors="replace"), name_b.decode("ascii", errors="replace")) == hdr.hash_name)

            # Verify Checksum (support real Airoha hardware accumulator and legacy concatenation)
            c1 = airoha_c_checksum(0, peb_bytes[offset + 1 : offset + DATA_ITEM_HEADER_SIZE])
            c2 = airoha_c_checksum(c1, group_b + name_b)
            real_chk = airoha_c_checksum(c2, val_b)
            legacy_chk = calculate_data_item_checksum(
                peb_bytes[offset + 1 : offset + DATA_ITEM_HEADER_SIZE],
                group_b,
                name_b,
                val_b,
            )
            chk_valid = (stored_chk == real_chk) or (stored_chk == legacy_chk)
            calc_chk = real_chk if stored_chk == real_chk else legacy_chk

            item = NvdmItem(
                header=hdr,
                group_name=g_str,
                item_name=n_str,
                value=val_b,
                stored_checksum=stored_chk,
                calculated_checksum=calc_chk,
                is_checksum_valid=chk_valid,
                is_hash_valid=h_valid,
            )

            if not chk_valid or not h_valid:
                corrupted_items.append(item)
                if strict and not chk_valid:
                    raise NvdmChecksumError(
                        f"Checksum mismatch for {g_str}:{n_str} (stored 0x{stored_chk:04X}, calc 0x{calc_chk:04X})"
                    )
            else:
                all_items.append(item)

            # Advance offset to next candidate record (support packed or 4-byte padded)
            next_off = offset + core_len
            aligned_off = (next_off + 3) & ~3
            if aligned_off < PEB_SIZE and peb_bytes[aligned_off] in (
                DataItemStatus.VALID,
                DataItemStatus.DELETE,
                DataItemStatus.WRITING,
            ):
                offset = aligned_off
            else:
                offset = next_off

    # 3. Resolve duplicate keys (highest sequence number wins)
    active_items: Dict[Tuple[str, str], NvdmItem] = {}
    for item in all_items:
        if item.header.status != DataItemStatus.VALID:
            continue

        key = item.key
        if key not in active_items:
            active_items[key] = item
        else:
            existing = active_items[key]
            if is_newer_sequence(item.header.sequence_number, existing.header.sequence_number):
                active_items[key] = item

    return NvdmPartition(
        pebs=pebs,
        active_items=active_items,
        all_items=all_items,
        corrupted_items=corrupted_items,
    )


def extract_cxd90050_blocks(
    source: Union[NvdmPartition, bytes],
) -> Dict[str, Any]:
    """Extracts parsed CXD90050 alignment blocks and DNC parameter blocks.

    Returns dict with keys:
    - ALIGN_FF_L: CXD90050AlignmentBlock
    - ALIGN_FF_R: CXD90050AlignmentBlock
    - ALIGN_FB_L: CXD90050AlignmentBlock
    - ALIGN_FB_R: CXD90050AlignmentBlock
    - HOWLING_PRE_PARAM: HowlingPreventionParam
    - HOWLING_DET_PARAM: HowlingDetectionParam
    - OPTIMIZER_PARAM: OptimizerParam
    """
    if isinstance(source, bytes):
        partition = parse_nvdm_image(source)
    else:
        partition = source

    blocks: Dict[str, Any] = {}

    for name in ("ALIGN_FF_L", "ALIGN_FF_R", "ALIGN_FB_L", "ALIGN_FB_R"):
        val = partition.get_value("DNC", name)
        if val is not None and len(val) == 108:
            blocks[name] = CXD90050AlignmentBlock.unpack(val)

    val_howl_pre = partition.get_value("DNC", "HOWLING_PRE_PARAM")
    if val_howl_pre is not None and len(val_howl_pre) == 32:
        blocks["HOWLING_PRE_PARAM"] = HowlingPreventionParam.unpack(val_howl_pre)

    val_howl_det = partition.get_value("DNC", "HOWLING_DET_PARAM")
    if val_howl_det is not None and len(val_howl_det) == 24:
        blocks["HOWLING_DET_PARAM"] = HowlingDetectionParam.unpack(val_howl_det)

    val_opt = partition.get_value("DNC", "OPTIMIZER_PARAM")
    if val_opt is not None and len(val_opt) == 40:
        blocks["OPTIMIZER_PARAM"] = OptimizerParam.unpack(val_opt)

    return blocks


def serialize_nvdm_partition(
    items: List[Tuple[str, str, bytes]],
    erase_counts: Optional[List[int]] = None,
) -> bytes:
    """Serializes a synthetic 128KB NVDM partition containing the given items."""
    partition = bytearray(b"\xFF" * NVDM_PARTITION_SIZE)

    # Setup PEB 0 as ACTIVED
    ec0 = erase_counts[0] if erase_counts and len(erase_counts) > 0 else 1
    peb0_hdr = struct.pack(
        "<IIBBBB",
        PEB_MAGIC,
        ec0,
        int(PebStatus.ACTIVED),
        0xF0,
        0x01,
        0x00,
    )
    partition[0:12] = peb0_hdr

    offset = PEB_DATA_START_OFFSET
    seq = 1
    for grp, name, val in items:
        rec = serialize_item_record(
            group_name=grp,
            item_name=name,
            value=val,
            pnum=0,
            offset=offset,
            sequence_number=seq,
            status=DataItemStatus.VALID,
            pad_to_4=True,
        )
        if offset + len(rec) > PEB_SIZE:
            raise ValueError(f"Items exceed PEB 0 capacity ({offset + len(rec)} > {PEB_SIZE})")
        partition[offset : offset + len(rec)] = rec
        offset += len(rec)
        seq += 1

    return bytes(partition)
