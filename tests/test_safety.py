"""Test Suite for Safety, Dry-Run, Staged Injection, Verifier & Rollback."""

from __future__ import annotations

from pathlib import Path
import pytest

from xm4_optimizer.dsp.biquad import BiquadCoefficients
from xm4_optimizer.dsp.synthesizer import synthesize_anc_filter
from xm4_optimizer.nvdm.cxd90050 import (
    CXD90050AlignmentBlock,
    FILTER_ID_ALIGN_FF_L,
    FILTER_ID_ALIGN_FF_R,
)
from xm4_optimizer.nvdm.parser import extract_cxd90050_blocks, parse_nvdm_image
from xm4_optimizer.safety.checksum import (
    compute_bkdr_hash,
    compute_nvdm_checksum,
    compute_race_fota_crc8,
    verify_nvdm_checksum,
    verify_race_fota_crc8,
)
from xm4_optimizer.safety.dry_run import (
    DryRunWriteViolationError,
    ReadOnlyTransportGuard,
    execute_dry_run,
    generate_dry_run_report,
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
    verify_readback,
)
from xm4_optimizer.transport.rfcomm_socket import read_nvdm_partition
from xm4_optimizer.transport.virtual_device import VirtualXM4Device


class TestChecksums:
    """Tests NVDM 16-bit interleaved checksum, BKDR hash, and RACE CRC-8."""

    def test_nvdm_checksum_deterministic_and_verify(self) -> None:
        payload = b"SONY_WH1000XM4_CXD90050_CALIBRATION_DATA_BLOCK"
        chk = compute_nvdm_checksum(payload)
        assert 0 <= chk <= 0xFFFF
        assert verify_nvdm_checksum(payload, chk) is True
        assert verify_nvdm_checksum(payload, chk ^ 0x0001) is False

    def test_race_fota_crc8_verification(self) -> None:
        page = bytes(range(256))
        crc = compute_race_fota_crc8(page)
        assert 0 <= crc <= 0xFF
        assert verify_race_fota_crc8(page, crc) is True
        assert verify_race_fota_crc8(page, (crc + 1) & 0xFF) is False

    def test_bkdr_hash_consistency(self) -> None:
        h1 = compute_bkdr_hash("DNC", "ALIGN_FF_L")
        h2 = compute_bkdr_hash("DNC", "ALIGN_FF_R")
        assert h1 != h2
        assert 0 <= h1 <= 0xFFFFFFFF


class TestZeroByteDryRun:
    """Verifies R3: Zero-Byte Dry-Run Engine writes 0 bytes and exits 0."""

    def test_dry_run_writes_strictly_zero_bytes(self, capsys: pytest.CaptureFixture[str]) -> None:
        dev = VirtualXM4Device(populate_defaults=True)
        dev.connect()
        snapshot_before = bytes(dev.flash)
        orig_regs = {k: list(v) for k, v in dev.dsp_registers.items()}

        opt_block, syn_res = synthesize_anc_filter(target_gain_db=7.5, fs=48000.0)
        rc = execute_dry_run(
            original_image=snapshot_before,
            optimized_block=opt_block,
            synthesis_result=syn_res,
            transport=dev,
            print_output=True,
        )

        captured = capsys.readouterr().out
        assert rc == 0
        assert dev.write_count == 0
        assert bytes(dev.flash) == snapshot_before
        assert dev.dsp_registers == orig_regs
        assert "0x000DF000" in captured
        assert "ALIGN_FF_L" in captured
        assert "MODIFIED" in captured
        assert "0 bytes (DRY-RUN BARRIER ACTIVE)" in captured

    def test_read_only_transport_guard_blocks_flash_and_atci_writes(self) -> None:
        dev = VirtualXM4Device(populate_defaults=True)
        dev.connect()
        guard = ReadOnlyTransportGuard(dev)

        # Reading is allowed
        page0 = guard.read_flash_page(0x000DF000)
        assert len(page0) == 256
        assert guard. send_atci_command("AT+HWPTOOL=COEFGET,0,0").startswith("\r\n+HWPTOOL: COEFGET")

        # Writing flash page is strictly blocked
        with pytest.raises(DryRunWriteViolationError, match="DRY-RUN VIOLATION"):
            guard.write_flash_page(0x000DF000, b"\x00" * 256)

        # Mutating ATCI commands are strictly blocked
        with pytest.raises(DryRunWriteViolationError, match="DRY-RUN VIOLATION"):
            guard.send_atci_command("AT+HWPTOOL=COEFSET,0,0,0,0,10000000,0,0")

        with pytest.raises(DryRunWriteViolationError, match="DRY-RUN VIOLATION"):
            guard.send_atci_command("AT+ENVDM=W")

        assert dev.write_count == 0
        assert guard.bytes_written == 0
        assert guard.write_attempts == 3

    def test_generate_dry_run_report_structure(self) -> None:
        dev = VirtualXM4Device(populate_defaults=True)
        dev.connect()
        opt_block, syn_res = synthesize_anc_filter(target_gain_db=8.0, fs=48000.0)
        report = generate_dry_run_report(bytes(dev.flash), opt_block, syn_res)

        assert report.exit_code == 0
        assert report.bytes_written == 0
        assert report.is_stable is True
        assert report.max_pole_magnitude < 1.0
        assert 6.0 <= report.band_max_gain_db <= 9.0
        assert len(report.stage_diffs) == 25  # 5 stages * 5 coeffs
        assert any(d.changed for d in report.stage_diffs)
        assert len(report.ascii_curve.splitlines()) >= 10


class TestStagedInjectionAndVerification:
    """Verifies R3 & R4: Safe Injection, DNC Preservation, Stability Gate, and Bit-Exact Readback."""

    def test_successful_injection_and_dnc_howling_preservation(self) -> None:
        dev = VirtualXM4Device(populate_defaults=True)
        dev.connect()

        before_blocks = extract_cxd90050_blocks(bytes(dev.flash))
        opt_block, _ = synthesize_anc_filter(
            target_gain_db=7.5, fs=48000.0, filter_id=FILTER_ID_ALIGN_FF_L
        )

        ok = execute_injection(dev, opt_block)
        assert ok is True
        assert dev.write_count >= 1

        # Verify readback in flash & DSP RAM
        assert verify_nvdm_block_in_flash(dev, opt_block) is True
        assert verify_dsp_registers_atci(dev, opt_block) is True

        # Verify DNC Howling & Optimizer blocks AND other alignment blocks are 100% preserved
        after_blocks = extract_cxd90050_blocks(bytes(dev.flash))
        assert after_blocks["HOWLING_PRE_PARAM"].pack() == before_blocks["HOWLING_PRE_PARAM"].pack()
        assert after_blocks["HOWLING_DET_PARAM"].pack() == before_blocks["HOWLING_DET_PARAM"].pack()
        assert after_blocks["OPTIMIZER_PARAM"].pack() == before_blocks["OPTIMIZER_PARAM"].pack()
        assert after_blocks["ALIGN_FF_R"].pack() == before_blocks["ALIGN_FF_R"].pack()
        assert after_blocks["ALIGN_FB_L"].pack() == before_blocks["ALIGN_FB_L"].pack()
        assert after_blocks["ALIGN_FB_R"].pack() == before_blocks["ALIGN_FB_R"].pack()

    def test_unstable_filter_rejected_before_any_hardware_write(self) -> None:
        dev = VirtualXM4Device(populate_defaults=True)
        dev.connect()
        snapshot = bytes(dev.flash)

        # Create an unstable biquad with poles outside unit circle (a1=-2.4, a2=1.44 -> pole at z=1.2)
        unstable_bq = BiquadCoefficients(a1=-2.4, a2=1.44, b0=1.0, b1=0.0, b2=0.0)
        unstable_block = CXD90050AlignmentBlock(
            filter_id=FILTER_ID_ALIGN_FF_L,
            gain_db=7.5,
            biquads=[unstable_bq] + [BiquadCoefficients.passthrough() for _ in range(4)],
        )

        with pytest.raises(UnstableFilterHoldError, match="UNSTABLE FILTER REJECTED"):
            execute_injection(dev, unstable_block)

        assert dev.write_count == 0
        assert bytes(dev.flash) == snapshot

    def test_injection_disconnect_and_crc_fault_handling(self) -> None:
        dev = VirtualXM4Device(populate_defaults=True)
        dev.connect()
        opt_block, _ = synthesize_anc_filter(target_gain_db=7.0, fs=48000.0)

        # 1. Simulate CRC-8 fault
        dev.corrupt_fota_crc = True
        with pytest.raises(InjectionError, match="Flash page write rejected"):
            execute_injection(dev, opt_block)
        dev.corrupt_fota_crc = False

        # 2. Simulate disconnect mid-write
        dev.disconnect_on_write = True
        with pytest.raises(InjectionError, match="Transport failure"):
            execute_injection(dev, opt_block)

    def test_verify_readback_detects_single_bit_flip(self) -> None:
        expected = bytes(range(108))
        assert verify_readback(expected, expected) is True

        corrupted = bytearray(expected)
        corrupted[42] ^= 0x01  # Single bit flip at offset 0x002A
        with pytest.raises(VerificationError) as exc_info:
            verify_readback(expected, bytes(corrupted))
        assert 42 in exc_info.value.mismatch_offsets
        assert exc_info.value.expected_byte == 42
        assert exc_info.value.actual_byte == (42 ^ 0x01)


class TestBackupAndRollback:
    """Verifies R1 & R4: 128KB NVDM Backup and 1-Command Bit-Exact Rollback."""

    def test_full_128kb_backup_and_bit_exact_rollback(self, tmp_path: Path) -> None:
        dev = VirtualXM4Device(populate_defaults=True)
        dev.connect()
        pristine_flash = bytes(dev.flash)

        backup_file = tmp_path / "xm4_nvdm_backup.bin"
        meta = create_nvdm_backup(dev, backup_file)

        assert backup_file.exists()
        assert meta.sidecar_sha256_path.exists()
        assert meta.size_bytes == 131072
        assert meta.active_item_count == 7
        assert backup_file.read_bytes() == pristine_flash

        # Modify device flash by injecting new FF_L and FF_R filters
        opt_l, _ = synthesize_anc_filter(target_gain_db=8.5, filter_id=FILTER_ID_ALIGN_FF_L)
        opt_r, _ = synthesize_anc_filter(target_gain_db=8.5, filter_id=FILTER_ID_ALIGN_FF_R)
        execute_injection(dev, opt_l)
        execute_injection(dev, opt_r)
        assert bytes(dev.flash) != pristine_flash

        # Execute 1-command rollback
        rolled_back = execute_rollback(dev, backup_file, verify=True)
        assert rolled_back is True
        assert bytes(dev.flash) == pristine_flash

        # Verify DSP registers were also restored back to default passthrough
        default_l = CXD90050AlignmentBlock(filter_id=FILTER_ID_ALIGN_FF_L)
        assert verify_dsp_registers_atci(dev, default_l) is True

    def test_rollback_rejects_corrupted_sha256_sidecar(self, tmp_path: Path) -> None:
        dev = VirtualXM4Device(populate_defaults=True)
        dev.connect()
        backup_file = tmp_path / "corrupt_backup.bin"
        meta = create_nvdm_backup(dev, backup_file)

        # Tamper with the backup file without updating sidecar
        tampered = bytearray(backup_file.read_bytes())
        tampered[100] ^= 0xFF
        backup_file.write_bytes(bytes(tampered))

        with pytest.raises(RollbackError, match="SHA-256 sidecar mismatch"):
            execute_rollback(dev, backup_file)
