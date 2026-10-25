"""Bit-Exact Readback Verification Engine for NVDM Flash and CXD90050 DSP Registers.

Verifies that injected biquad parameters match both:
1. Non-volatile Partition 6 flash memory (0x000DF000) with valid NVDM 16-bit interleaved checksum.
2. Live CXD90050 DSP RAM coefficient registers via AT+HWPTOOL=COEFGET.
"""

from __future__ import annotations

from typing import List, Tuple

from xm4_optimizer.nvdm.cxd90050 import (
    CXD90050AlignmentBlock,
    FILTER_ID_TO_NAME,
)
from xm4_optimizer.nvdm.parser import parse_nvdm_image
from xm4_optimizer.transport.rfcomm_socket import (
    DeviceTransport,
    NVDM_PARTITION_BASE,
    NVDM_PARTITION_SIZE,
    read_nvdm_partition,
)


class VerificationError(Exception):
    """Raised when bit-exact readback verification detects a mismatch."""

    def __init__(
        self,
        message: str,
        mismatch_offsets: List[int] | None = None,
        expected_byte: int | None = None,
        actual_byte: int | None = None,
    ) -> None:
        super().__init__(message)
        self.mismatch_offsets = mismatch_offsets or []
        self.expected_byte = expected_byte
        self.actual_byte = actual_byte


def verify_readback(injected_bytes: bytes, readback_bytes: bytes) -> bool:
    """Performs bit-exact comparison between injected bytes and readback bytes.

    Args:
        injected_bytes: Expected byte sequence written to target.
        readback_bytes: Actual byte sequence read back from target.

    Returns:
        True if 100% bit-exact match.

    Raises:
        ValueError: If byte lengths differ.
        VerificationError: If any bit/byte differs between injected and readback data.
    """
    if len(injected_bytes) != len(readback_bytes):
        raise ValueError(
            f"Size mismatch: injected {len(injected_bytes)}B, read {len(readback_bytes)}B"
        )
    if injected_bytes != readback_bytes:
        mismatches = [
            i for i in range(len(injected_bytes)) if injected_bytes[i] != readback_bytes[i]
        ]
        first = mismatches[0]
        raise VerificationError(
            f"Bit mismatch at {len(mismatches)} offsets: first at offset 0x{first:04X} "
            f"(expected 0x{injected_bytes[first]:02X}, got 0x{readback_bytes[first]:02X})",
            mismatch_offsets=mismatches,
            expected_byte=injected_bytes[first],
            actual_byte=readback_bytes[first],
        )
    return True


def verify_dsp_registers_atci(
    transport: DeviceTransport,
    expected_block: CXD90050AlignmentBlock,
) -> bool:
    """Queries live DSP registers via AT+HWPTOOL=COEFGET and verifies Q28 bit-exact match."""
    target_id = expected_block.filter_id
    for stage_idx, biquad in enumerate(expected_block.biquads):
        expected_q28: Tuple[int, int, int, int, int] = biquad.to_q28_tuple()
        expected_unsigned = [c & 0xFFFFFFFF for c in expected_q28]

        resp = transport.send_atci_command(f"AT+HWPTOOL=COEFGET,{target_id},{stage_idx}")
        if "ERROR" in resp or "+HWPTOOL: COEFGET" not in resp:
            raise VerificationError(
                f"Failed to read back DSP register via ATCI for filter {target_id} stage {stage_idx}: {resp!r}"
            )

        # Parse line: +HWPTOOL: COEFGET,<target>,<stage>,0x...,0x...,0x...,0x...,0x...
        coef_line = ""
        for line in resp.splitlines():
            line_s = line.strip()
            if line_s.startswith("+HWPTOOL: COEFGET"):
                coef_line = line_s
                break

        parts = coef_line.split(",")
        if len(parts) < 8:
            raise VerificationError(
                f"Malformed COEFGET response for filter {target_id} stage {stage_idx}: {coef_line!r}"
            )

        actual_unsigned = [int(p.strip(), 16) & 0xFFFFFFFF for p in parts[3:8]]
        if actual_unsigned != expected_unsigned:
            raise VerificationError(
                f"DSP register mismatch at filter {target_id} stage {stage_idx}: "
                f"expected {[f'0x{x:08X}' for x in expected_unsigned]}, "
                f"got {[f'0x{x:08X}' for x in actual_unsigned]}"
            )
    return True


def verify_nvdm_block_in_flash(
    transport: DeviceTransport,
    expected_block: CXD90050AlignmentBlock,
    group_name: str = "DNC",
    item_name: str | None = None,
    base_addr: int = NVDM_PARTITION_BASE,
) -> bool:
    """Reads Partition 6 from device flash and verifies bit-exact match for the alignment block."""
    resolved_name = item_name or FILTER_ID_TO_NAME.get(expected_block.filter_id, "ALIGN_FF_L")
    image_bytes = read_nvdm_partition(transport, base_addr=base_addr, size=NVDM_PARTITION_SIZE)
    partition = parse_nvdm_image(image_bytes, strict=True)
    item = partition.get_item(group_name, resolved_name)
    if item is None:
        raise VerificationError(
            f"NVDM item {group_name}/{resolved_name} not found in flash readback"
        )
    if not item.is_checksum_valid:
        raise VerificationError(
            f"NVDM item {group_name}/{resolved_name} failed interleaved checksum in flash readback"
        )

    expected_bytes = expected_block.pack()
    return verify_readback(expected_bytes, item.value)
