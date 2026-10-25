"""Command-Line Interface for Sony WH-1000XM4 ANC Optimizer.

Zero-dependency CLI built on standard library `argparse` with full support for both:
1. Subcommand workflows:
   - `xm4-optimizer backup [output.bin]`
   - `xm4-optimizer parse [input.bin]`
   - `xm4-optimizer optimize --target-gain 7.5`
   - `xm4-optimizer dry-run --target-gain 7.5`
   - `xm4-optimizer inject --target-gain 7.5 --backup-out backup.bin`
   - `xm4-optimizer verify --target-gain 7.5`
   - `xm4-optimizer rollback <backup.bin>`
2. Top-level flag workflows:
   - `xm4-optimizer --dry-run --target-gain 7.5`
   - `xm4-optimizer --backup backup.bin`
   - `xm4-optimizer --inject --target-gain 7.5`
   - `xm4-optimizer --verify`
   - `xm4-optimizer --rollback backup.bin`
3. Programmatic test invocation via `CliRunner().invoke(cli, args, obj=...)`.
"""

from __future__ import annotations

import argparse
import contextlib
from dataclasses import dataclass
import io
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional, Sequence

from xm4_optimizer.dsp.synthesizer import synthesize_anc_filter
from xm4_optimizer.nvdm.cxd90050 import (
    CXD90050AlignmentBlock,
    FILTER_ID_ALIGN_FF_L,
    FILTER_ID_TO_NAME,
)
from xm4_optimizer.nvdm.parser import extract_cxd90050_blocks, parse_nvdm_image
from xm4_optimizer.safety.dry_run import (
    ReadOnlyTransportGuard,
    execute_dry_run,
)
from xm4_optimizer.safety.injector import (
    InjectionError,
    UnstableFilterHoldError,
    execute_injection,
)
from xm4_optimizer.safety.rollback import (
    RollbackError,
    create_nvdm_backup,
    execute_rollback,
)
from xm4_optimizer.safety.verifier import (
    VerificationError,
    verify_dsp_registers_atci,
    verify_nvdm_block_in_flash,
)
from xm4_optimizer.transport.rfcomm_socket import (
    BluetoothRfcommTransport,
    DeviceTransport,
    NVDM_PARTITION_BASE,
    NVDM_PARTITION_SIZE,
    read_nvdm_partition,
)
from xm4_optimizer.transport.virtual_device import VirtualXM4Device


def _create_transport(
    mac: Optional[str],
    mock: bool,
    custom_transport: Optional[DeviceTransport] = None,
) -> DeviceTransport:
    """Instantiates and connects either a VirtualXM4Device or BluetoothRfcommTransport."""
    if custom_transport is not None:
        if hasattr(custom_transport, "is_connected") and not getattr(
            custom_transport, "is_connected"
        ):
            custom_transport.connect()
        return custom_transport

    if mock or not mac:
        dev = VirtualXM4Device(populate_defaults=True)
        dev.connect()
        return dev

    bt = BluetoothRfcommTransport(bd_addr=mac)
    bt.connect()
    return bt


def _run_dry_run_workflow(
    transport: DeviceTransport,
    target_gain: float,
    sample_rate: float,
    filter_id: int,
    image_path: Optional[Path] = None,
) -> int:
    """Executes zero-byte dry-run workflow and prints diff tables + ASCII response curve."""
    guard = ReadOnlyTransportGuard(transport)
    if image_path is not None and image_path.exists():
        orig_image = image_path.read_bytes()
    else:
        orig_image = read_nvdm_partition(
            guard, base_addr=NVDM_PARTITION_BASE, size=NVDM_PARTITION_SIZE
        )

    blocks = extract_cxd90050_blocks(orig_image)
    filter_name = FILTER_ID_TO_NAME.get(filter_id, "ALIGN_FF_L")
    base_block = blocks.get(filter_name, CXD90050AlignmentBlock(filter_id=filter_id))

    opt_block, syn_res = synthesize_anc_filter(
        target_gain_db=target_gain,
        fs=sample_rate,
        filter_id=filter_id,
        base_block=base_block,
    )

    rc = execute_dry_run(
        original_image=orig_image,
        optimized_block=opt_block,
        synthesis_result=syn_res,
        transport=guard,
        print_output=True,
    )
    assert guard.bytes_written == 0
    return rc


def _run_backup_workflow(transport: DeviceTransport, output_path: Path) -> int:
    """Backs up the 128KB NVDM Partition 6 to disk with SHA-256 sidecar."""
    meta = create_nvdm_backup(transport, output_path)
    print(
        f"Backup complete: {meta.backup_path} "
        f"({meta.size_bytes} bytes, {meta.active_item_count} active NVDM items)\n"
        f"SHA-256: {meta.sha256_hex}"
    )
    return 0


def _run_parse_workflow(
    transport: DeviceTransport,
    image_path: Optional[Path] = None,
) -> int:
    """Parses NVDM Partition 6 and prints CXD90050 + DNC blocks."""
    if image_path is not None and image_path.exists():
        img = image_path.read_bytes()
    else:
        img = read_nvdm_partition(
            transport, base_addr=NVDM_PARTITION_BASE, size=NVDM_PARTITION_SIZE
        )

    partition = parse_nvdm_image(img, strict=True)
    blocks = extract_cxd90050_blocks(partition)

    print("=" * 80)
    print("  Parsed NVDM Partition 6 (0x000DF000) Items")
    print("=" * 80)
    for (grp, name), item in partition.active_items.items():
        chk_status = "OK" if item.is_checksum_valid else "ERR"
        print(
            f"  {grp}/{name:<20} | PEB {item.header.pnum} | "
            f"Offset 0x{item.header.offset:04X} | {item.header.value_size:4d} B | "
            f"Checksum 0x{item.stored_checksum:04X} ({chk_status}) | "
            f"Hash 0x{item.header.hash_name:08X}"
        )
    print("=" * 80)
    print(f"Extracted CXD90050 / DNC Blocks: {', '.join(blocks.keys())}")
    return 0


def _run_inject_workflow(
    transport: DeviceTransport,
    target_gain: float,
    sample_rate: float,
    filter_id: int,
    backup_out: Optional[Path] = None,
) -> int:
    """Synthesizes and safely injects ANC filter parameters with automatic backup & verification."""
    if backup_out is not None:
        create_nvdm_backup(transport, backup_out)

    orig_image = read_nvdm_partition(
        transport, base_addr=NVDM_PARTITION_BASE, size=NVDM_PARTITION_SIZE
    )
    blocks = extract_cxd90050_blocks(orig_image)
    filter_name = FILTER_ID_TO_NAME.get(filter_id, "ALIGN_FF_L")
    base_block = blocks.get(filter_name, CXD90050AlignmentBlock(filter_id=filter_id))

    opt_block, syn_res = synthesize_anc_filter(
        target_gain_db=target_gain,
        fs=sample_rate,
        filter_id=filter_id,
        base_block=base_block,
    )

    ok = execute_injection(
        transport=transport,
        optimized_block=opt_block,
        backup_image=orig_image,
        auto_rollback_on_failure=True,
    )
    if ok:
        print(
            f"INJECTION & BIT-EXACT VERIFICATION SUCCEEDED "
            f"for {filter_name} (gain={target_gain:+.2f} dB, max |p|={syn_res.max_pole_magnitude:.6f} < 1.0)"
        )
        return 0
    return 1


def _run_verify_workflow(
    transport: DeviceTransport,
    target_gain: float,
    sample_rate: float,
    filter_id: int,
) -> int:
    """Verifies that flash and DSP registers match the synthesized filter block."""
    opt_block, _ = synthesize_anc_filter(
        target_gain_db=target_gain,
        fs=sample_rate,
        filter_id=filter_id,
    )
    verify_nvdm_block_in_flash(transport, expected_block=opt_block)
    verify_dsp_registers_atci(transport, expected_block=opt_block)
    print("READBACK VERIFICATION PASSED (100% BIT-EXACT)")
    return 0


def _run_rollback_workflow(transport: DeviceTransport, backup_path: Path) -> int:
    """Restores a 128KB NVDM backup image bit-for-bit."""
    ok = execute_rollback(transport, backup_path, verify=True)
    if ok:
        print(
            f"ROLLBACK SUCCEEDED: Restored 128KB NVDM partition from {backup_path} (100% bit-exact)"
        )
        return 0
    return 1


def build_parser() -> argparse.ArgumentParser:
    """Constructs the argument parser supporting both subcommands and top-level flags."""
    parser = argparse.ArgumentParser(
        prog="xm4-optimizer",
        description="Sony WH-1000XM4 ANC Optimizer CLI (NVDM Backup, Biquad Synthesis, Dry-Run, Safe Injection, Rollback).",
    )
    parser.add_argument("--mac", type=str, default=None, help="Bluetooth MAC address.")
    parser.add_argument("--mock", action="store_true", default=False, help="Use VirtualXM4Device hardware emulator.")
    parser.add_argument("--no-mock", dest="mock", action="store_false")
    parser.add_argument("--dry-run", dest="flag_dry_run", action="store_true", help="Run zero-byte dry-run simulation and exit 0.")
    parser.add_argument("--backup", dest="flag_backup", type=Path, default=None, help="Backup 128KB NVDM Partition 6 to path.")
    parser.add_argument("--parse", dest="flag_parse", action="store_true", help="Parse and display NVDM Partition 6 items.")
    parser.add_argument("--optimize", dest="flag_optimize", action="store_true", help="Synthesize optimal Biquad IIR ANC filter.")
    parser.add_argument("--inject", dest="flag_inject", action="store_true", help="Safely inject synthesized filter and verify readback.")
    parser.add_argument("--verify", dest="flag_verify", action="store_true", help="Verify bit-exact flash and DSP register readback.")
    parser.add_argument("--rollback", dest="flag_rollback", type=Path, default=None, help="Restore 128KB NVDM backup file bit-for-bit.")
    parser.add_argument("--target-gain", type=float, default=7.5, help="Target 50-800 Hz boost in dB (+6.0 to +9.0).")
    parser.add_argument("--sample-rate", type=float, default=48000.0, help="DSP sampling rate in Hz.")
    parser.add_argument("--filter-id", type=int, default=FILTER_ID_ALIGN_FF_L, help="CXD90050 filter ID (0..3).")
    parser.add_argument("--backup-out", type=Path, default=None, help="Optional pre-flight backup file path.")
    parser.add_argument("--image", dest="image_path", type=Path, default=None, help="Optional NVDM binary image path.")
    parser.add_argument("positional", nargs="*", help="Subcommand (backup, parse, optimize, dry-run, inject, verify, rollback) and arguments.")
    return parser


def run_cli(
    argv: Optional[Sequence[str]] = None,
    obj: Optional[Dict[str, Any]] = None,
) -> int:
    """Executes the CLI with given arguments and optional context object, returning exit code."""
    parser = build_parser()
    try:
        ns = parser.parse_args(list(argv) if argv is not None else None)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 1

    custom_transport = obj.get("transport") if obj else None
    transport = _create_transport(
        mac=ns.mac, mock=ns.mock, custom_transport=custom_transport
    )

    pos = list(ns.positional)
    subcmd = pos[0] if pos else None
    subarg = Path(pos[1]) if len(pos) > 1 else None

    try:
        if subcmd == "backup":
            out_path = subarg or Path("nvdm_backup.bin")
            return _run_backup_workflow(transport, out_path)

        if subcmd == "parse":
            return _run_parse_workflow(transport, image_path=subarg or ns.image_path)

        if subcmd in ("optimize", "dry-run"):
            return _run_dry_run_workflow(
                transport=transport,
                target_gain=ns.target_gain,
                sample_rate=ns.sample_rate,
                filter_id=ns.filter_id,
                image_path=ns.image_path,
            )

        if subcmd == "inject":
            return _run_inject_workflow(
                transport=transport,
                target_gain=ns.target_gain,
                sample_rate=ns.sample_rate,
                filter_id=ns.filter_id,
                backup_out=ns.backup_out,
            )

        if subcmd == "verify":
            return _run_verify_workflow(
                transport=transport,
                target_gain=ns.target_gain,
                sample_rate=ns.sample_rate,
                filter_id=ns.filter_id,
            )

        if subcmd == "rollback":
            if subarg is None:
                print("ERROR: rollback requires backup_path argument")
                return 1
            return _run_rollback_workflow(transport, subarg)

        # Top-level flag dispatch
        if ns.flag_backup is not None:
            rc = _run_backup_workflow(transport, ns.flag_backup)
            if not (ns.flag_dry_run or ns.flag_inject or ns.flag_parse):
                return rc

        if ns.flag_rollback is not None:
            return _run_rollback_workflow(transport, ns.flag_rollback)

        if ns.flag_parse:
            rc = _run_parse_workflow(transport, image_path=ns.image_path)
            if not (ns.flag_dry_run or ns.flag_inject):
                return rc

        if ns.flag_dry_run or ns.flag_optimize:
            return _run_dry_run_workflow(
                transport=transport,
                target_gain=ns.target_gain,
                sample_rate=ns.sample_rate,
                filter_id=ns.filter_id,
                image_path=ns.image_path,
            )

        if ns.flag_inject:
            return _run_inject_workflow(
                transport=transport,
                target_gain=ns.target_gain,
                sample_rate=ns.sample_rate,
                filter_id=ns.filter_id,
                backup_out=ns.backup_out,
            )

        if ns.flag_verify:
            return _run_verify_workflow(
                transport=transport,
                target_gain=ns.target_gain,
                sample_rate=ns.sample_rate,
                filter_id=ns.filter_id,
            )

        parser.print_help()
        return 0

    except (
        ValueError,
        FileNotFoundError,
        InjectionError,
        UnstableFilterHoldError,
        VerificationError,
        RollbackError,
    ) as exc:
        print(f"ERROR: {exc}")
        return 1
    finally:
        if custom_transport is None:
            transport.close()


@dataclass
class CliResult:
    """Result container returned by CliRunner.invoke."""

    exit_code: int
    output: str
    stdout: str


class CliRunner:
    """Test runner compatible with Click's CliRunner.invoke(cli, args, obj=...)."""

    def invoke(
        self,
        cli_callable: Any,
        args: Optional[Sequence[str]] = None,
        obj: Optional[Dict[str, Any]] = None,
    ) -> CliResult:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            rc = cli_callable(args, obj=obj)
        text = buf.getvalue()
        return CliResult(exit_code=rc, output=text, stdout=text)


def cli(
    args: Optional[Sequence[str]] = None,
    obj: Optional[Dict[str, Any]] = None,
) -> int:
    """Callable CLI entrypoint."""
    return run_cli(argv=args, obj=obj)


def main() -> None:
    """System CLI entry point."""
    sys.exit(run_cli())


if __name__ == "__main__":
    main()
