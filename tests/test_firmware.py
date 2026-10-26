"""Tests for the known-firmware allowlist used by the flasher."""

from __future__ import annotations

from pathlib import Path

from tools.flash_firmware import flash_firmware
from xm4_optimizer.firmware import KNOWN_FIRMWARE, identify_firmware


def test_table_entries_are_well_formed() -> None:
    assert len(KNOWN_FIRMWARE) == 5
    for digest, fw in KNOWN_FIRMWARE.items():
        assert len(digest) == 64 and int(digest, 16) >= 0
        assert fw.model_id == "MDRID294300" and fw.size > 1_000_000


def test_unknown_data_is_not_identified() -> None:
    assert identify_firmware(b"\x00" * 1395504) is None


def test_flasher_refuses_unknown_file_before_connecting(tmp_path: Path, monkeypatch) -> None:
    def no_socket(*_a, **_k):
        raise AssertionError("must not connect for an unknown image")

    monkeypatch.setattr("tools.flash_firmware.create_rfcomm_socket", no_socket)
    bogus = tmp_path / "custom.bin"
    bogus.write_bytes(b"\xff" * 4096)
    assert flash_firmware("00:11:22:33:44:55", bogus) is False
