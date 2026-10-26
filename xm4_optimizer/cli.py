"""Command-Line Interface for Sony WH-1000XM4 Unified Hardware Platform.

Unofficial CLI for the Sony WH-1000XM4. Evidence levels for each feature: docs/STATUS.md.
1. Subcommands:
   - `xm4-optimizer backup [output.bin]`       : Pull full 512 KB NVDM calibration backup over Bluetooth RFCOMM
   - `xm4-optimizer flash <firmware.bin>`      : Flash official Sony firmware directly over Bluetooth RFCOMM
   - `xm4-optimizer spatial [start|stop|status]`: Control PipeWire EasyEffects + AeroSpatial 3D Audio bridge [EXPERIMENTAL - ACTIVE DEV]
   - `xm4-optimizer anc [status|on|ambient|off]`: Control ANC modes over Bluetooth MDR protocol (Ch 11)
   - `xm4-optimizer parse [input.bin]`        : Parse 512KB/128KB NVDM calibration dump and CXD90050 items
   - `xm4-optimizer optimize --target-gain 7.5`: Synthesize low-hiss Q4.28 ANC Biquad IIR filter
   - `xm4-optimizer dry-run --target-gain 7.5` : Zero-byte simulation barrier (writes 0 bytes)
   - `xm4-optimizer inject --target-gain 7.5`  : Emulator only (--mock); disabled on hardware, model contradicted by a real dump
   - `xm4-optimizer verify --target-gain 7.5`  : Emulator only; readback check for the injector model
   - `xm4-optimizer rollback <backup.bin>`     : Emulator only (--mock); disabled on hardware
   - `xm4-optimizer status`                    : Display Bluetooth connection, battery, and firmware status

2. Top-level flag workflows (backwards-compatible with tests):
   - `xm4-optimizer --dry-run --target-gain 7.5`
   - `xm4-optimizer --backup backup.bin`
   - `xm4-optimizer --inject --target-gain 7.5`
   - `xm4-optimizer --verify`
   - `xm4-optimizer --rollback backup.bin`
"""

from __future__ import annotations

import argparse
import contextlib
from dataclasses import dataclass
import io
import os
from pathlib import Path
import re
import socket
import struct
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Sequence

# Ensure project root is importable for standalone tools
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.backup_nvdm import backup_xm4_nvdm, find_connected_xm4_mac
from tools.flash_firmware import flash_firmware

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
    create_rfcomm_socket,
    DeviceTransport,
    NVDM_PARTITION_BASE,
    NVDM_PARTITION_SIZE,
    read_nvdm_partition,
)
from xm4_optimizer.transport.virtual_device import VirtualXM4Device


def _resolve_target_mac(user_mac: Optional[str]) -> Optional[str]:
    """Returns user-supplied MAC or automatically scans for connected WH-1000XM4."""
    if user_mac:
        return user_mac
    return find_connected_xm4_mac()


def _create_transport(
    mac: Optional[str],
    mock: bool,
    custom_transport: Optional[DeviceTransport] = None,
) -> DeviceTransport:
    """Instantiates and connects either a VirtualXM4Device (mock) or real BluetoothRfcommTransport."""
    if custom_transport is not None:
        if hasattr(custom_transport, "is_connected") and not getattr(
            custom_transport, "is_connected"
        ):
            custom_transport.connect()
        return custom_transport

    if mock:
        dev = VirtualXM4Device(populate_defaults=True)
        dev.connect()
        return dev

    # Real hardware mode: Auto-detect MAC if not provided
    effective_mac = _resolve_target_mac(mac)
    if not effective_mac:
        raise ConnectionError(
            "No connected Sony WH-1000XM4 found via Bluetooth. "
            "Please connect your headphones, provide --mac <XX:XX:XX:XX:XX:XX>, "
            "or pass --mock explicitly for offline test simulation."
        )

    bt = BluetoothRfcommTransport(bd_addr=effective_mac)
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
    """Backs up the NVDM Partition to disk with SHA-256 sidecar."""
    meta = create_nvdm_backup(transport, output_path)
    print(
        f"Backup complete: {meta.backup_path} "
        f"({meta.size_bytes} bytes, {meta.active_item_count} active NVDM items)\n"
        f"SHA-256: {meta.sha256_hex}"
    )
    return 0


def _run_parse_workflow(
    transport: Optional[DeviceTransport],
    image_path: Optional[Path] = None,
) -> int:
    """Parses NVDM Partition and prints calibration items."""
    if image_path is not None and image_path.exists():
        img = image_path.read_bytes()
    else:
        if transport is None:
            raise ValueError("No NVDM image path provided and transport not connected")
        img = read_nvdm_partition(
            transport, base_addr=NVDM_PARTITION_BASE, size=NVDM_PARTITION_SIZE
        )

    partition = parse_nvdm_image(img, strict=False)
    blocks = extract_cxd90050_blocks(partition)

    print("=" * 80)
    print(f"  Parsed NVDM Calibration Items ({len(partition.pebs)} PEBs, {len(partition.active_items)} unique active items)")
    print("=" * 80)
    for (grp, name), item in partition.active_items.items():
        chk_status = "OK" if item.is_checksum_valid else "ERR"
        print(
            f"  {grp}/{name:<20} | PEB {item.header.pnum:3d} | "
            f"Offset 0x{item.header.offset:04X} | {item.header.value_size:4d} B | "
            f"Checksum 0x{item.stored_checksum:04X} ({chk_status}) | "
            f"Seq {item.header.sequence_number:6d}"
        )
    print("=" * 80)
    if blocks:
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
    if not ok:
        print("ERROR: Injection failed. Automatic rollback completed.")
        return 1

    print("INJECTION & BIT-EXACT VERIFICATION SUCCEEDED")
    return 0


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
    """Restores a NVDM backup image bit-for-bit."""
    ok = execute_rollback(transport, backup_path, verify=True)
    if ok:
        print(
            f"ROLLBACK SUCCEEDED: Restored NVDM partition from {backup_path} (100% bit-exact)"
        )
        return 0
    return 1


def _run_anc_workflow(mac: str, mode: str) -> int:
    """Queries or controls ANC modes directly over Bluetooth MDR RFCOMM channel 11."""
    print(f"[*] Connecting to Sony MDR on RFCOMM Channel 11 ({mac})...")
    s_mdr = None
    try:
        s_mdr = create_rfcomm_socket(mac, 11, timeout_s=4.0)
        s_mdr.recv(1024)
        s_mdr.sendall(bytes.fromhex("3e010000000000013c"))

        mode_clean = mode.lower()
        if mode_clean in ("on", "anc"):
            print("Setting Noise Cancelling: ON...")
            payload = bytes.fromhex("1618010000")
        elif mode_clean in ("ambient", "asm"):
            print("Setting Ambient Sound: ON (Level 20)...")
            payload = bytes.fromhex("1618010114")
        elif mode_clean in ("off",):
            print("Setting Noise Cancelling: OFF...")
            payload = bytes.fromhex("1618000000")
        else:
            print(f"Querying ANC / Ambient status...")
            payload = bytes.fromhex("1617")

        inner = struct.pack(">BBI", 0x00, 0, len(payload)) + payload
        chk = sum(inner) & 0xFF
        pkt = bytearray([0x3E]) + inner + bytes([chk, 0x3C])
        s_mdr.sendall(pkt)
        resp = s_mdr.recv(1024)
        print(f"[+] MDR Command executed successfully (Response: {resp[:16].hex()}...)")
        return 0
    except Exception as e:
        print(f"[-] MDR connection error: {e}")
        return 1
    finally:
        try:
            s_mdr.close()
        except Exception:
            pass


def _run_spatial_workflow(
    action: str,
    app_node: str = "spotify",
    azimuth: float = 60.0,
    gain: float = 1.0,
) -> int:
    """Manages the PipeWire EasyEffects + AeroSpatial Steam Audio spatial pipeline.

    [NOTE: This is an experimental prototype under active development.]
    """
    print("[!] NOTE: AeroSpatial 3D Audio Engine is currently an EXPERIMENTAL PROTOTYPE under active development.")
    script_path = REPO_ROOT / "tools" / "route_pipewire_app_through_aerospatial.sh"
    if not script_path.exists():
        print(f"Error: Routing script not found at {script_path}")
        return 1

    if action == "status":
        print("[*] Inspecting PipeWire audio routing topology...")
        try:
            links = subprocess.check_output(["pw-link", "-l"], text=True)
            if "aerospatial_3d_sink" in links:
                print("[+] AeroSpatial 3D sink is ACTIVE in PipeWire graph:")
                for line in links.splitlines():
                    if any(k in line for k in ("aerospatial", "ee_soe", app_node)):
                        print(f"    {line}")
                return 0
            else:
                print("[-] AeroSpatial 3D sink is currently INACTIVE.")
                return 0
        except FileNotFoundError:
            print("[-] PipeWire (pw-link) is not installed or unavailable on this system.")
            return 0
        except Exception as e:
            print(f"Error querying PipeWire: {e}")
            return 1

    elif action == "stop":
        print("[*] Stopping AeroSpatial 3D daemon and restoring original routing...")
        subprocess.run(["pkill", "-f", "aerospatial-daemon"], check=False)
        time.sleep(0.5)
        print("[+] AeroSpatial stopped.")
        return 0

    elif action == "start":
        print(
            f"[*] Starting AeroSpatial 3D pipeline for app '{app_node}' (Azimuth: {azimuth}°, Gain: {gain}x)..."
        )
        cmd = [
            "bash",
            str(script_path),
            app_node,
            "--azimuth",
            str(azimuth),
            "--gain",
            str(gain),
        ]
        proc = subprocess.Popen(cmd)
        print(f"[+] Started spatial routing process (PID: {proc.pid})")
        return 0

    else:
        print(f"Unknown spatial action: '{action}'. Use 'start', 'stop', or 'status'.")
        return 1


def _run_status_workflow(mac: Optional[str]) -> int:
    """Displays system-wide status of WH-1000XM4 hardware and audio graph."""
    print("=" * 65)
    print("  Sony WH-1000XM4 Optimizer Platform - Status")
    print("=" * 65)
    effective_mac = _resolve_target_mac(mac)
    print(f"  Connected Headset MAC : {effective_mac or 'Not detected (Disconnected)'}")

    # Bluetooth status
    try:
        bt_out = subprocess.check_output(["bluetoothctl", "info", effective_mac or ""], text=True) if effective_mac else ""
        connected = "Connected: yes" in bt_out
        battery = re.search(r"Battery Percentage:\s+0x[0-9a-fA-F]+\s+\(([0-9]+)\)", bt_out)
        bat_str = f"{battery.group(1)}%" if battery else "Unknown"
        print(f"  Bluetooth Link        : {'CONNECTED' if connected else 'DISCONNECTED'}")
        print(f"  Battery Level         : {bat_str}")
    except Exception:
        print("  Bluetooth Link        : Query failed")

    # Spatial audio status (experimental)
    try:
        pw_out = subprocess.check_output(["pw-link", "-l"], text=True)
        spatial_active = "aerospatial_3d_sink" in pw_out
        print(f"  AeroSpatial 3D Engine : {'ACTIVE (Experimental Prototype)' if spatial_active else 'INACTIVE (Experimental Prototype)'}")
    except Exception:
        print("  AeroSpatial 3D Engine : PipeWire unavailable (Experimental Prototype)")

    print("=" * 65)
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Constructs the argument parser supporting unified subcommands and legacy flags."""
    epilog_text = """
====================================================================================================
ARCHITECTURAL DISTINCTION: FLASHER vs. INJECTOR
====================================================================================================
1. `xm4-optimizer flash <firmware.bin>` (Full-System FOTA Flasher):
   - Scope: Replaces entire system firmware (~1.4 MB - 4.0 MB) over Bluetooth RFCOMM (Ch 11 & 21).
   - Flash Action: Erases staging flash blocks (CMD_ERASE 0x0404) and writes official signed binary.
   - Integrity: the headset runs its own integrity check (0x1C01) before commit. Use official Sony
     images only and compare their hash first (see README). Device-verified once: v3.0.1 -> v2.5.0.
   - Headset reboots automatically on commit.

2. `xm4-optimizer inject --target-gain <dB>` (Calibration Parameter Injector):
   - Status: DISABLED on real hardware. It targets a 108-byte DNC/ALIGN_* biquad item that does not
     exist on a real WH-1000XM4 (there ALIGN_* are 8-byte values under group CXD90050).
   - Runs against the in-memory emulator with --mock. --allow-unverified-write overrides the block.
   - Page writes without erase cannot restore 0 -> 1 bits; rollback is not proven. See docs/STATUS.md.
====================================================================================================
"""
    parser = argparse.ArgumentParser(
        prog="xm4-optimizer",
        description="Unofficial Sony WH-1000XM4 tools (NVDM backup and parser, firmware flasher, ANC control, experimental spatial audio). Not affiliated with Sony.",
        epilog=epilog_text,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--mac", type=str, default=None, help="Bluetooth MAC address of the headphones (auto-detected if omitted).")
    parser.add_argument("--mock", action="store_true", default=False, help="Use VirtualXM4Device hardware emulator for test simulation.")
    parser.add_argument("--no-mock", dest="mock", action="store_false")
    parser.add_argument("--dry-run", dest="flag_dry_run", action="store_true", help="Run zero-byte dry-run simulation and exit 0 (DRY-RUN BARRIER ACTIVE).")
    parser.add_argument("--backup", dest="flag_backup", type=Path, default=None, help="Backup NVDM calibration image to path.")
    parser.add_argument("--parse", dest="flag_parse", action="store_true", help="Parse and display NVDM calibration items and checksums.")
    parser.add_argument("--optimize", dest="flag_optimize", action="store_true", help="Synthesize optimal Biquad IIR ANC filter coefficients.")
    parser.add_argument("--inject", dest="flag_inject", action="store_true", help="[INJECTOR, emulator only] Inject the synthesized filter into the emulated 108B NVDM model.")
    parser.add_argument("--verify", dest="flag_verify", action="store_true", help="Verify bit-exact flash and DSP register readback.")
    parser.add_argument("--rollback", dest="flag_rollback", type=Path, default=None, help="Restore NVDM backup file bit-for-bit.")
    parser.add_argument("--allow-unverified-write", action="store_true", default=False, help="Allow inject/rollback on real hardware even though they are not hardware-validated.")
    parser.add_argument("--target-gain", type=float, default=7.5, help="Target 50-800 Hz boost in dB (+6.0 to +9.0).")
    parser.add_argument("--sample-rate", type=float, default=48000.0, help="DSP sampling rate in Hz.")
    parser.add_argument("--filter-id", type=int, default=FILTER_ID_ALIGN_FF_L, help="CXD90050 filter ID (0..3).")
    parser.add_argument("--backup-out", type=Path, default=None, help="Optional pre-flight backup file path.")
    parser.add_argument("--image", dest="image_path", type=Path, default=None, help="Optional NVDM binary image path.")
    parser.add_argument(
        "positional",
        nargs="*",
        help="Subcommand: backup (dump NVDM), flash (full official FOTA firmware install), inject (surgical 108B NVDM write), dry-run, verify, rollback, parse, optimize, anc, spatial, status.",
    )
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

    pos = list(ns.positional)
    subcmd = pos[0].lower() if pos else None
    subarg = pos[1] if len(pos) > 1 else None

    # Fast-path for subcommands that do not require RFCOMM transport connection upfront
    if subcmd == "status" or subcmd == "info":
        return _run_status_workflow(mac=ns.mac)

    if subcmd == "spatial":
        action = subarg or "status"
        app = pos[2] if len(pos) > 2 else "spotify"
        return _run_spatial_workflow(action=action, app_node=app)

    if subcmd == "flash":
        if not subarg:
            print("ERROR: flash requires path to signed Sony firmware .bin file")
            return 1
        effective_mac = _resolve_target_mac(ns.mac)
        if not effective_mac:
            print("ERROR: No connected Sony WH-1000XM4 found via Bluetooth. Provide --mac <ADDR> manually.")
            return 1
        ok = flash_firmware(bd_addr=effective_mac, fw_path=Path(subarg))
        return 0 if ok else 1

    if subcmd == "anc":
        effective_mac = _resolve_target_mac(ns.mac)
        if not effective_mac:
            print("ERROR: No connected Sony WH-1000XM4 found via Bluetooth.")
            return 1
        mode = subarg or "status"
        return _run_anc_workflow(mac=effective_mac, mode=mode)

    if subcmd == "parse" and subarg is not None:
        return _run_parse_workflow(transport=None, image_path=Path(subarg))

    # Real Hardware Hardware Backup Subcommand:
    # If not in mock mode and no custom mock transport is injected, use proven hardware method directly!
    custom_transport = obj.get("transport") if obj else None
    if subcmd == "backup" and not ns.mock and custom_transport is None:
        effective_mac = _resolve_target_mac(ns.mac)
        if not effective_mac:
            print("ERROR: No connected Sony WH-1000XM4 found via Bluetooth. Connect headphones or pass --mac <ADDR> (or pass --mock for test simulation).")
            return 1
        out_path = Path(subarg) if subarg else Path("wh1000xm4_nvdm_backup.bin")
        try:
            backup_xm4_nvdm(bd_addr=effective_mac, output_path=out_path)
            return 0
        except Exception as e:
            print(f"ERROR: Real hardware backup failed: {e}")
            return 1

    # The inject/rollback write paths model a 108-byte DNC/ALIGN_* biquad item that does not
    # match a real WH-1000XM4 NVDM dump (see docs/STATUS.md), so refuse them on hardware.
    wants_write = subcmd in ("inject", "rollback") or ns.flag_inject or ns.flag_rollback is not None
    if wants_write and not ns.mock and custom_transport is None and not ns.allow_unverified_write:
        print(
            "ERROR: inject/rollback are not validated on real hardware and are disabled.\n"
            "       Their NVDM model does not match a real WH-1000XM4 dump (see docs/STATUS.md).\n"
            "       Use --mock to simulate, or --allow-unverified-write if you accept the risk."
        )
        return 2

    # Connect transport for remaining workflows
    transport = _create_transport(
        mac=ns.mac, mock=ns.mock, custom_transport=custom_transport
    )

    try:
        if subcmd == "backup":
            out_path = Path(subarg) if subarg else Path("nvdm_backup.bin")
            return _run_backup_workflow(transport, out_path)

        if subcmd == "parse":
            return _run_parse_workflow(transport, image_path=Path(subarg) if subarg else ns.image_path)

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
            return _run_rollback_workflow(transport, Path(subarg))

        # Top-level flag dispatch (backwards compatibility)
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
        ConnectionError,
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
