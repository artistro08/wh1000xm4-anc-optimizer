"""1-Command Full 128KB NVDM Partition Backup and Bit-Exact Rollback Engine.

Provides atomic backup creation with SHA-256 sidecar verification and 1-command
rollback that restores the pristine 128KB NVDM partition (512 pages of 256B)
and synchronizes live CXD90050 DSP registers via AT+HWPTOOL=COEFSET.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import time
from typing import Union

from xm4_optimizer.nvdm.cxd90050 import (
    CXD90050AlignmentBlock,
    FILTER_ID_ALIGN_FB_L,
    FILTER_ID_ALIGN_FB_R,
    FILTER_ID_ALIGN_FF_L,
    FILTER_ID_ALIGN_FF_R,
)
from xm4_optimizer.nvdm.parser import extract_cxd90050_blocks, parse_nvdm_image
from xm4_optimizer.safety.verifier import verify_readback
from xm4_optimizer.transport.protocol_race import FLASH_PAGE_SIZE
from xm4_optimizer.transport.rfcomm_socket import (
    DeviceTransport,
    NVDM_PARTITION_BASE,
    NVDM_PARTITION_SIZE,
    read_nvdm_partition,
)


class RollbackError(Exception):
    """Raised when NVDM partition backup or rollback fails."""


@dataclass
class BackupMetadata:
    """Metadata describing a verified 128KB NVDM backup image."""

    backup_path: Path
    sidecar_sha256_path: Path
    sha256_hex: str
    size_bytes: int
    active_item_count: int
    timestamp: float


def _sync_alignment_blocks_to_dsp(
    transport: DeviceTransport,
    image_bytes: bytes,
) -> None:
    """Synchronizes CXD90050 alignment blocks from NVDM image into live DSP RAM registers."""
    blocks = extract_cxd90050_blocks(image_bytes)
    name_to_id = {
        "ALIGN_FF_L": FILTER_ID_ALIGN_FF_L,
        "ALIGN_FF_R": FILTER_ID_ALIGN_FF_R,
        "ALIGN_FB_L": FILTER_ID_ALIGN_FB_L,
        "ALIGN_FB_R": FILTER_ID_ALIGN_FB_R,
    }
    for name, filter_id in name_to_id.items():
        block = blocks.get(name)
        if isinstance(block, CXD90050AlignmentBlock):
            for stage_idx, bq in enumerate(block.biquads):
                q28 = bq.to_q28_tuple()
                hex_str = ",".join(f"0x{(c & 0xFFFFFFFF):08X}" for c in q28)
                cmd = f"AT+HWPTOOL=COEFSET,{filter_id},{stage_idx},{hex_str}"
                try:
                    transport.send_atci_command(cmd)
                except Exception:
                    # Best-effort live register sync during rollback
                    pass


def create_nvdm_backup(
    transport: DeviceTransport,
    backup_path: Union[str, Path],
    base_addr: int = NVDM_PARTITION_BASE,
    size: int = NVDM_PARTITION_SIZE,
) -> BackupMetadata:
    """Reads the full 128KB NVDM Partition 6, validates structure, and writes backup + .sha256."""
    dest = Path(backup_path)
    dest.parent.mkdir(parents=True, exist_ok=True)

    image_bytes = read_nvdm_partition(transport, base_addr=base_addr, size=size)
    if len(image_bytes) != NVDM_PARTITION_SIZE:
        raise RollbackError(
            f"Read NVDM image length {len(image_bytes)} != {NVDM_PARTITION_SIZE}"
        )

    partition = parse_nvdm_image(image_bytes, strict=True)
    sha256_hex = hashlib.sha256(image_bytes).hexdigest()

    dest.write_bytes(image_bytes)
    sidecar = Path(f"{dest}.sha256")
    sidecar.write_text(f"{sha256_hex}  {dest.name}\n", encoding="utf-8")

    return BackupMetadata(
        backup_path=dest,
        sidecar_sha256_path=sidecar,
        sha256_hex=sha256_hex,
        size_bytes=len(image_bytes),
        active_item_count=len(partition.active_items),
        timestamp=time.time(),
    )


def execute_rollback(
    transport: DeviceTransport,
    backup_file_path: Union[str, Path],
    base_addr: int = NVDM_PARTITION_BASE,
    verify: bool = True,
) -> bool:
    """Restores a 128KB NVDM backup image bit-for-bit to device flash and verifies readback.

    Args:
        transport: Connected DeviceTransport instance.
        backup_file_path: Path to the 128KB NVDM backup binary file.
        base_addr: Base flash address for Partition 6 (default 0x000DF000).
        verify: If True, reads back the entire 128KB partition and asserts bit-exact match.

    Returns:
        True when rollback and verification succeed.

    Raises:
        FileNotFoundError: If backup_file_path does not exist.
        RollbackError: If backup size, SHA-256 sidecar, NVDM structure, flash write, or readback fails.
    """
    bpath = Path(backup_file_path)
    if not bpath.exists():
        raise FileNotFoundError(f"Backup file not found: {bpath}")

    backup_bytes = bpath.read_bytes()
    if len(backup_bytes) != NVDM_PARTITION_SIZE:
        raise RollbackError(
            f"Invalid backup size: {len(backup_bytes)} bytes (expected {NVDM_PARTITION_SIZE} bytes)"
        )

    # Check optional .sha256 sidecar if present
    sidecar = Path(f"{bpath}.sha256")
    if sidecar.exists():
        expected_hash = sidecar.read_text(encoding="utf-8").strip().split()[0].lower()
        actual_hash = hashlib.sha256(backup_bytes).hexdigest().lower()
        if expected_hash != actual_hash:
            raise RollbackError(
                f"Backup SHA-256 sidecar mismatch: expected {expected_hash}, got {actual_hash}"
            )

    # Validate NVDM structure before flashing
    try:
        parse_nvdm_image(backup_bytes, strict=True)
    except Exception as exc:
        raise RollbackError(f"Backup NVDM image failed structural validation: {exc}") from exc

    # Write all 512 pages (256 bytes each) across Partition 6
    num_pages = NVDM_PARTITION_SIZE // FLASH_PAGE_SIZE
    for page_idx in range(num_pages):
        offset = page_idx * FLASH_PAGE_SIZE
        addr = base_addr + offset
        page_data = backup_bytes[offset : offset + FLASH_PAGE_SIZE]
        ok = transport.write_flash_page(addr, page_data)
        if not ok:
            raise RollbackError(f"Hardware page write failed during rollback at 0x{addr:08X}")

    # Synchronize restored alignment blocks to live DSP RAM registers
    _sync_alignment_blocks_to_dsp(transport, backup_bytes)

    try:
        transport.send_atci_command("AT+ENVDM=W")
    except Exception:
        pass

    if verify:
        readback_bytes = read_nvdm_partition(
            transport, base_addr=base_addr, size=NVDM_PARTITION_SIZE
        )
        try:
            verify_readback(backup_bytes, readback_bytes)
        except Exception as exc:
            raise RollbackError(f"Rollback readback verification failed: {exc}") from exc

    return True
