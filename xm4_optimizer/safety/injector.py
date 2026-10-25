"""Safe Staged Parameter Injector for Sony WH-1000XM4 ANC Optimization.

Enforces:
1. Pre-injection Nyquist pole stability gate (strictly |p| < 1.0 across all 5 biquads).
2. Pre-flight 128KB NVDM snapshot for automatic rollback on any fault or disconnect.
3. Selective in-place update of only the target alignment block (preserving HOWLING_PRE_PARAM,
   HOWLING_DET_PARAM, OPTIMIZER_PARAM, and unrelated PEBs/items untouched).
4. Dual-layer write: Airoha RACE ATCI (AT+HWPTOOL=COEFSET + AT+ENVDM=W) and NOR flash page
   programming (0x0402) with 16-bit interleaved NVDM checksums and CRC-8 FOTA checksums.
5. Mandatory post-injection bit-exact readback verification on both NOR flash and DSP registers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import struct
from typing import List, Optional

import numpy as np

from xm4_optimizer.dsp.stability import (
     compute_biquad_poles,
    validate_alignment_block_stability,
)
from xm4_optimizer.nvdm.cxd90050 import (
    CXD90050AlignmentBlock,
    FILTER_ID_TO_NAME,
)
from xm4_optimizer.nvdm.item import (
    calculate_data_item_checksum,
    CHECKSUM_SIZE,
    DATA_ITEM_HEADER_SIZE,
    serialize_item_record,
)
from xm4_optimizer.nvdm.parser import parse_nvdm_image, serialize_nvdm_partition
from xm4_optimizer.nvdm.peb import (
    NVDM_BASE_ADDRESS,
    NVDM_PARTITION_SIZE,
    PEB_SIZE,
)
from xm4_optimizer.safety.verifier import (
    VerificationError,
    verify_dsp_registers_atci,
    verify_nvdm_block_in_flash,
    verify_readback,
)
from xm4_optimizer.transport.protocol_race import FLASH_PAGE_SIZE
from xm4_optimizer.transport.rfcomm_socket import (
    DeviceTransport,
    read_nvdm_partition,
)


class InjectionError(Exception):
    """Raised when staged parameter injection fails."""


class UnstableFilterHoldError(InjectionError):
    """Raised when a candidate filter violates the Nyquist unit-circle stability gate."""


@dataclass
class InjectionResult:
    """Detailed report of a staged parameter injection operation."""

    success: bool
    filter_id: int
    filter_name: str
    modified_pages: List[int] = field(default_factory=list)
    new_nvdm_checksum: int = 0
    verified_flash: bool = False
    verified_dsp: bool = False
    rolled_back: bool = False


def _enforce_nyquist_stability_gate(block: CXD90050AlignmentBlock) -> None:
    """Mathematically verifies that all 5 biquad stages have poles strictly inside |z| < 1.0."""
    for stage_idx, bq in enumerate(block.biquads):
        coeffs = (bq.a1, bq.a2, bq.b0, bq.b1, bq.b2)
        if not all(np.isfinite(c) for c in coeffs):
            raise UnstableFilterHoldError(
                f"Stage {stage_idx} contains non-finite coefficients: {coeffs}"
            )
        poles = compute_biquad_poles(bq.a1, bq.a2)
        if not bool(np.all(np.abs(poles) < 1.0)):
            max_p = float(np.max(np.abs(poles))) if poles.size > 0 else float("inf")
            raise UnstableFilterHoldError(
                f"UNSTABLE FILTER REJECTED at Stage {stage_idx}: max pole magnitude "
                f"|p| = {max_p:.6f} >= 1.0 (a1={bq.a1}, a2={bq.a2})"
            )

    try:
        validate_alignment_block_stability(block, tolerance=0.0)
    except Exception as exc:
        raise UnstableFilterHoldError(
            f"Alignment block {block.filter_name} failed cascade stability verification: {exc}"
        ) from exc


def update_nvdm_image_item(
    image_bytes: bytes,
    group_name: str,
    item_name: str,
    new_value: bytes,
) -> tuple[bytes, int, List[int]]:
    """Updates a single NVDM item in-place within the 128KB image and recalculates its checksum.

    Preserves all other items (including HOWLING_PRE_PARAM, HOWLING_DET_PARAM, OPTIMIZER_PARAM)
    bit-for-bit untouched.

    Returns:
        Tuple of (updated_image_bytes, new_16bit_checksum, modified_page_offsets)
    """
    if len(image_bytes) != NVDM_PARTITION_SIZE:
        raise InjectionError(
            f"Invalid NVDM image size: {len(image_bytes)} (expected {NVDM_PARTITION_SIZE})"
        )

    partition = parse_nvdm_image(image_bytes, strict=True)
    target_item = partition.get_item(group_name, item_name)

    buf = bytearray(image_bytes)

    if target_item is not None and len(target_item.value) == len(new_value):
        peb_start = target_item.header.pnum * PEB_SIZE
        item_start = peb_start + target_item.header.offset
        hdr_slice = bytes(buf[item_start : item_start + DATA_ITEM_HEADER_SIZE])

        grp_start = item_start + DATA_ITEM_HEADER_SIZE
        grp_end = grp_start + target_item.header.group_name_size
        name_end = grp_end + target_item.header.data_item_name_size
        val_end = name_end + target_item.header.value_size
        chk_end = val_end + CHECKSUM_SIZE

        grp_b = bytes(buf[grp_start:grp_end])
        name_b = bytes(buf[grp_end:name_end])

        # Write new value bytes in-place
        buf[name_end:val_end] = new_value

        # Recompute 16-bit interleaved NVDM checksum over header[1:20] + group + name + new_value
        new_chk = calculate_data_item_checksum(
            hdr_slice[1:DATA_ITEM_HEADER_SIZE],
            grp_b,
            name_b,
            new_value,
        )
        buf[val_end:chk_end] = struct.pack("<H", new_chk)

        start_page = (item_start // FLASH_PAGE_SIZE) * FLASH_PAGE_SIZE
        end_page = ((chk_end - 1) // FLASH_PAGE_SIZE) * FLASH_PAGE_SIZE
        mod_pages = list(range(start_page, end_page + FLASH_PAGE_SIZE, FLASH_PAGE_SIZE))
        return bytes(buf), new_chk, mod_pages

    # Fallback if item did not previously exist: re-serialize active items with new item
    items_list = []
    replaced = False
    for (g, n), itm in partition.active_items.items():
        if g == group_name and n == item_name:
            items_list.append((g, n, new_value))
            replaced = True
        else:
            items_list.append((g, n, itm.value))
    if not replaced:
        items_list.append((group_name, item_name, new_value))

    new_img = serialize_nvdm_partition(items_list)
    new_part = parse_nvdm_image(new_img, strict=True)
    updated_itm = new_part.get_item(group_name, item_name)
    new_chk = updated_itm.calculated_checksum if updated_itm else 0

    mod_pages = [
        off
        for off in range(0, NVDM_PARTITION_SIZE, FLASH_PAGE_SIZE)
        if new_img[off : off + FLASH_PAGE_SIZE] != image_bytes[off : off + FLASH_PAGE_SIZE]
    ]
    return new_img, new_chk, mod_pages


def _restore_snapshot_pages(
    transport: DeviceTransport,
    snapshot_bytes: bytes,
    page_offsets: List[int],
    base_addr: int = NVDM_BASE_ADDRESS,
) -> bool:
    """Attempts to restore modified flash pages from pre-flight snapshot."""
    try:
        for rel_off in page_offsets:
            addr = base_addr + rel_off
            page_data = snapshot_bytes[rel_off : rel_off + FLASH_PAGE_SIZE]
            transport.write_flash_page(addr, page_data)
        return True
    except Exception:
        return False


def execute_injection(
    transport: DeviceTransport,
    optimized_block: CXD90050AlignmentBlock,
    target_offset: int = 0,
    backup_image: Optional[bytes] = None,
    auto_rollback_on_failure: bool = True,
    base_addr: int = NVDM_BASE_ADDRESS,
) -> bool:
    """Safely injects an optimized CXD90050AlignmentBlock into DSP RAM and NVDM Flash.

    Steps:
    1. Enforce Nyquist stability gate (all pole magnitudes < 1.0).
    2. Capture pre-flight 128KB NVDM image (or use provided backup_image).
    3. Update only the target alignment block in the NVDM image and recompute its 16-bit checksum.
    4. Stage coefficients into CXD90050 via AT+HWPTOOL=COEFSET and commit via AT+ENVDM=W.
    5. Program modified 256-byte pages to Partition 6 NOR flash via write_flash_page.
    6. Perform bit-exact readback verification on both NOR flash and DSP registers.
    7. Automatically roll back modified pages if any write or verification step fails.
    """
    # 1. Pre-injection Nyquist stability gate
    _enforce_nyquist_stability_gate(optimized_block)

    filter_name = FILTER_ID_TO_NAME.get(optimized_block.filter_id, "ALIGN_FF_L")
    new_val_bytes = optimized_block.pack()

    # 2. Pre-flight snapshot
    try:
        snapshot = (
            backup_image
            if backup_image is not None
            else read_nvdm_partition(transport, base_addr=base_addr, size=NVDM_PARTITION_SIZE)
        )
    except Exception as exc:
        raise InjectionError(f"Failed to read pre-flight NVDM snapshot: {exc}") from exc

    # 3. Update NVDM image in memory
    updated_image, _new_chk, mod_page_offsets = update_nvdm_image_item(
        snapshot,
        group_name="DNC",
        item_name=filter_name,
        new_value=new_val_bytes,
    )

    # If caller specified an explicit target_offset inside the partition, ensure its page is included
    if target_offset > 0:
        rel_off = (
            target_offset - base_addr if target_offset >= base_addr else target_offset
        )
        aligned_off = (rel_off // FLASH_PAGE_SIZE) * FLASH_PAGE_SIZE
        if 0 <= aligned_off < NVDM_PARTITION_SIZE and aligned_off not in mod_page_offsets:
            mod_page_offsets.append(aligned_off)
            mod_page_offsets.sort()

    # 4. Stage live DSP coefficients via ATCI and write modified flash pages
    try:
        for stage_idx, bq in enumerate(optimized_block.biquads):
            q28 = bq.to_q28_tuple()
            hex_coeffs = ",".join(f"0x{(c & 0xFFFFFFFF):08X}" for c in q28)
            cmd = f"AT+HWPTOOL=COEFSET,{optimized_block.filter_id},{stage_idx},{hex_coeffs}"
            resp = transport.send_atci_command(cmd)
            if "ERROR" in resp:
                raise InjectionError(
                    f"AT+HWPTOOL=COEFSET failed at stage {stage_idx}: {resp!r}"
                )

        # Write modified 256B pages to NOR flash
        for rel_off in mod_page_offsets:
            addr = base_addr + rel_off
            page_data = updated_image[rel_off : rel_off + FLASH_PAGE_SIZE]
            ok = transport.write_flash_page(addr, page_data)
            if not ok:
                raise InjectionError(
                    f"Flash page write rejected by device at address 0x{addr:08X}"
                )

        commit_resp = transport.send_atci_command("AT+ENVDM=W")
        if "ERROR" in commit_resp:
            raise InjectionError(f"AT+ENVDM=W commit failed: {commit_resp!r}")

        # 5. Mandatory post-injection readback verification
        verify_nvdm_block_in_flash(
            transport,
            expected_block=optimized_block,
            group_name="DNC",
            item_name=filter_name,
            base_addr=base_addr,
        )
        verify_dsp_registers_atci(transport, expected_block=optimized_block)

        # Verify modified pages bit-for-bit
        for rel_off in mod_page_offsets:
            addr = base_addr + rel_off
            expected_page = updated_image[rel_off : rel_off + FLASH_PAGE_SIZE]
            actual_page = transport.read_flash_page(addr)
            verify_readback(expected_page, actual_page)

    except (VerificationError, InjectionError, ConnectionError, OSError) as exc:
        if auto_rollback_on_failure:
            _restore_snapshot_pages(
                transport, snapshot, mod_page_offsets, base_addr=base_addr
            )
        if isinstance(exc, (VerificationError, InjectionError)):
            raise
        raise InjectionError(f"Transport failure during injection: {exc}") from exc

    return True
