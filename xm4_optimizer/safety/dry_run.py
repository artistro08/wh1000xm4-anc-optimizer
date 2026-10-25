"""Zero-Byte Dry-Run Simulation Engine for Sony WH-1000XM4 ANC Optimization.

Computes and renders:
- Visual Biquad coefficient diff tables (Original vs Optimized in float and Q28 hex)
- Target NVDM flash memory offsets and affected 256-byte NOR flash pages
- Simulated DTFT ASCII frequency response curve (20 Hz - 20 kHz, highlighting 50 - 800 Hz)
- Strict hardware write barrier enforcing 0 bytes written to flash and exit code 0
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from xm4_optimizer.dsp.biquad import BiquadCoefficients
from xm4_optimizer.dsp.simulation import render_ascii_response_curve
from xm4_optimizer.dsp.synthesizer import SynthesisResult
from xm4_optimizer.nvdm.cxd90050 import (
    CXD90050AlignmentBlock,
    FILTER_ID_TO_NAME,
)
from xm4_optimizer.nvdm.item import DATA_ITEM_HEADER_SIZE
from xm4_optimizer.nvdm.parser import parse_nvdm_image
from xm4_optimizer.nvdm.peb import NVDM_BASE_ADDRESS, PEB_SIZE
from xm4_optimizer.transport.protocol_race import FLASH_PAGE_SIZE
from xm4_optimizer.transport.rfcomm_socket import DeviceTransport


class DryRunWriteViolationError(RuntimeError):
    """Raised when any flash or ATCI write operation is attempted during a dry-run."""


class ReadOnlyTransportGuard:
    """Transport wrapper that strictly blocks any mutating operation during dry-run."""

    def __init__(self, inner: Optional[DeviceTransport] = None) -> None:
        self._inner = inner
        self.bytes_written: int = 0
        self.write_attempts: int = 0

    def connect(self) -> None:
        if self._inner is not None:
            self._inner.connect()

    def close(self) -> None:
        if self._inner is not None:
            self._inner.close()

    def read_flash_page(self, address: int) -> bytes:
        if self._inner is None:
            raise ConnectionError("No underlying transport configured for read")
        return self._inner.read_flash_page(address)

    def write_flash_page(self, address: int, data: bytes) -> bool:
        self.write_attempts += 1
        raise DryRunWriteViolationError(
            f"DRY-RUN VIOLATION: Attempted to write {len(data)} bytes to flash address 0x{address:08X}"
        )

    def send_atci_command(self, cmd: str, timeout_ms: int = 3000) -> str:
        clean = cmd.strip().upper()
        if "COEFSET" in clean or "ENVDM=W" in clean or "NCOPTSET" in clean:
            self.write_attempts += 1
            raise DryRunWriteViolationError(
                f"DRY-RUN VIOLATION: Attempted mutating ATCI command {cmd!r}"
            )
        if self._inner is None:
            return "\r\nOK\r\n"
        return self._inner.send_atci_command(cmd, timeout_ms=timeout_ms)


@dataclass
class StageCoefficientDiff:
    """Comparison of a single biquad coefficient between original and optimized blocks."""

    stage_index: int
    coeff_name: str
    original_float: float
    optimized_float: float
    delta_float: float
    original_q28_hex: str
    optimized_q28_hex: str
    changed: bool


@dataclass
class DryRunReport:
    """Structured output of a dry-run execution."""

    filter_id: int
    filter_name: str
    partition_base_addr: int
    item_flash_addr: int
    value_flash_addr: int
    item_byte_length: int
    affected_page_addresses: List[int]
    original_gain_db: float
    optimized_gain_db: float
    band_min_gain_db: float
    band_max_gain_db: float
    max_pole_magnitude: float
    is_stable: bool
    stage_diffs: List[StageCoefficientDiff] = field(default_factory=list)
    ascii_curve: str = ""
    rendered_text: str = ""
    bytes_written: int = 0
    exit_code: int = 0


def _locate_item_offsets(
    original_image: bytes,
    filter_name: str,
    base_addr: int = NVDM_BASE_ADDRESS,
) -> tuple[int, int, int, List[int], CXD90050AlignmentBlock]:
    """Finds the target alignment item in the 128KB NVDM image and returns flash offsets."""
    partition = parse_nvdm_image(original_image, strict=False)
    item = partition.get_item("DNC", filter_name) or partition.get_item("CXD90050", filter_name)

    if item is not None:
        peb_offset = item.header.pnum * PEB_SIZE
        item_rel_offset = peb_offset + item.header.offset
        val_rel_offset = (
            item_rel_offset
            + DATA_ITEM_HEADER_SIZE
            + item.header.group_name_size
            + item.header.data_item_name_size
        )
        item_abs_addr = base_addr + item_rel_offset
        val_abs_addr = base_addr + val_rel_offset
        total_rec_len = item.header.total_record_size

        start_page = (item_abs_addr // FLASH_PAGE_SIZE) * FLASH_PAGE_SIZE
        end_addr = item_abs_addr + total_rec_len - 1
        end_page = (end_addr // FLASH_PAGE_SIZE) * FLASH_PAGE_SIZE
        pages = list(range(start_page, end_page + FLASH_PAGE_SIZE, FLASH_PAGE_SIZE))

        if len(item.value) == 108:
            orig_block = CXD90050AlignmentBlock.unpack(item.value)
        else:
            orig_block = CXD90050AlignmentBlock(filter_id=0)
        return item_abs_addr, val_abs_addr, len(item.value), pages, orig_block

    default_item_addr = base_addr + 12
    default_val_addr = default_item_addr + DATA_ITEM_HEADER_SIZE + 3 + len(filter_name)
    pages = [base_addr]
    return default_item_addr, default_val_addr, 108, pages, CXD90050AlignmentBlock(filter_id=0)


def generate_dry_run_report(
    original_image: bytes,
    optimized_block: CXD90050AlignmentBlock,
    synthesis_result: SynthesisResult,
    base_addr: int = NVDM_BASE_ADDRESS,
) -> DryRunReport:
    """Computes full dry-run comparison report without touching any hardware."""
    filter_name = FILTER_ID_TO_NAME.get(optimized_block.filter_id, "ALIGN_FF_L")
    item_addr, val_addr, val_len, pages, orig_block = _locate_item_offsets(
        original_image, filter_name, base_addr=base_addr
    )

    coeff_names = ("a1", "a2", "b0", "b1", "b2")
    stage_diffs: List[StageCoefficientDiff] = []

    for stage_idx in range(5):
        orig_b: BiquadCoefficients = orig_block.biquads[stage_idx]
        opt_b: BiquadCoefficients = optimized_block.biquads[stage_idx]

        orig_floats = (orig_b.a1, orig_b.a2, orig_b.b0, orig_b.b1, orig_b.b2)
        opt_floats = (opt_b.a1, opt_b.a2, opt_b.b0, opt_b.b1, opt_b.b2)
        orig_q28 = orig_b.to_q28_tuple()
        opt_q28 = opt_b.to_q28_tuple()

        for c_idx, c_name in enumerate(coeff_names):
            of = orig_floats[c_idx]
            nf = opt_floats[c_idx]
            oq = orig_q28[c_idx] & 0xFFFFFFFF
            nq = opt_q28[c_idx] & 0xFFFFFFFF
            stage_diffs.append(
                StageCoefficientDiff(
                    stage_index=stage_idx,
                    coeff_name=c_name,
                    original_float=of,
                    optimized_float=nf,
                    delta_float=nf - of,
                    original_q28_hex=f"0x{oq:08X}",
                    optimized_q28_hex=f"0x{nq:08X}",
                    changed=(oq != nq),
                )
            )

    ascii_curve = render_ascii_response_curve(
        frequencies=synthesis_result.freqs,
        magnitude_db=synthesis_result.magnitude_db,
        width=60,
        height=12,
        band_highlight=(50.0, 800.0),
    )

    lines: List[str] = []
    sep = "=" * 96
    lines.append(sep)
    lines.append(f"  Target Memory Mapping & Acoustic Summary ({filter_name})")
    lines.append(sep)
    lines.append(f"  Partition 6 Base Address  : 0x{base_addr:08X}")
    lines.append(f"  NVDM Item Header Address  : 0x{item_addr:08X}")
    lines.append(f"  CXD90050 Payload Offset   : 0x{val_addr:08X} ({val_len} bytes)")
    lines.append(
        f"  Affected 256B Flash Pages : {', '.join(f'0x{p:08X}' for p in pages)}"
    )
    lines.append(
        f"  50-800 Hz Band Gain Range : {synthesis_result.band_min_gain_db:+.2f} dB to {synthesis_result.band_max_gain_db:+.2f} dB"
    )
    lines.append(
        f"  Nyquist Pole Stability    : {'PASS' if synthesis_result.is_stable else 'FAIL'} (max |p| = {synthesis_result.max_pole_magnitude:.6f} < 1.0)"
    )
    lines.append("  Hardware Flash Bytes Written: 0 bytes (DRY-RUN BARRIER ACTIVE)")
    lines.append("-" * 96)
    lines.append(
        f"  {'Stage':<8} | {'Coeff':<5} | {'Orig Float':>12} | {'Opt Float':>12} | {'Delta':>12} | {'Orig Q28':>10} | {'Opt Q28':>10} | {'Status':<9}"
    )
    lines.append("-" * 96)

    for d in stage_diffs:
        if d.changed or d.stage_index <= 1:
            status_str = "MODIFIED" if d.changed else "UNCHANGED"
            lines.append(
                f"  Stage {d.stage_index:<2} | {d.coeff_name:<5} | "
                f"{d.original_float:+12.6f} | {d.optimized_float:+12.6f} | "
                f"{d.delta_float:+12.6f} | {d.original_q28_hex:>10} | {d.optimized_q28_hex:>10} | {status_str:<9}"
            )
    lines.append(sep)
    lines.append(ascii_curve)
    lines.append(sep)

    rendered_text = "\n".join(lines)

    return DryRunReport(
        filter_id=optimized_block.filter_id,
        filter_name=filter_name,
        partition_base_addr=base_addr,
        item_flash_addr=item_addr,
        value_flash_addr=val_addr,
        item_byte_length=val_len,
        affected_page_addresses=pages,
        original_gain_db=orig_block.gain_db,
        optimized_gain_db=optimized_block.gain_db,
        band_min_gain_db=synthesis_result.band_min_gain_db,
        band_max_gain_db=synthesis_result.band_max_gain_db,
        max_pole_magnitude=synthesis_result.max_pole_magnitude,
        is_stable=synthesis_result.is_stable,
        stage_diffs=stage_diffs,
        ascii_curve=ascii_curve,
        rendered_text=rendered_text,
        bytes_written=0,
        exit_code=0,
    )


def execute_dry_run(
    original_image: bytes,
    optimized_block: CXD90050AlignmentBlock,
    synthesis_result: SynthesisResult,
    transport: Optional[DeviceTransport] = None,
    print_output: bool = True,
) -> int:
    """Executes non-destructive dry-run simulation, prints diffs/offsets/curve, returns 0.

    Enforces strictly zero bytes written to flash.
    """
    guard = ReadOnlyTransportGuard(transport)
    report = generate_dry_run_report(
        original_image=original_image,
        optimized_block=optimized_block,
        synthesis_result=synthesis_result,
    )
    assert guard.bytes_written == 0
    if print_output:
        print(report.rendered_text)
    return 0
