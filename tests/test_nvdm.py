"""Comprehensive Unit and Integration Tests for NVDM Subsystem.

Tests Physical Erase Block allocation, 20-byte data item headers, 16-bit interleaved checksums,
BKDR hash lookups, CXD90050 parameter structures, sequence number resolution, and partition parsing.
"""

from __future__ import annotations

import struct
import pytest

from xm4_optimizer.nvdm.cxd90050 import (
    BiquadCoefficients,
    CXD90050AlignmentBlock,
    deserialize_alignment_block,
    FILTER_ID_ALIGN_FB_L,
    FILTER_ID_ALIGN_FB_R,
    FILTER_ID_ALIGN_FF_L,
    FILTER_ID_ALIGN_FF_R,
    float_to_q28,
    HowlingDetectionParam,
    HowlingPreventionParam,
    OptimizerParam,
    q28_to_float,
    Q28_SCALE,
    serialize_alignment_block,
)
from xm4_optimizer.nvdm.item import (
    calculate_data_item_checksum,
    calculate_data_item_hash,
    CHECKSUM_SIZE,
    CHECKSUM_STRUCT,
    DATA_ITEM_HEADER_SIZE,
    DataItemStatus,
    DataItemType,
    NvdmItem,
    NvdmItemHeader,
    parse_item_header,
    serialize_item_record,
)
from xm4_optimizer.nvdm.parser import (
    extract_cxd90050_blocks,
    is_newer_sequence,
    NvdmChecksumError,
    NvdmCorruptError,
    NvdmPartition,
    parse_nvdm_image,
    serialize_nvdm_partition,
)
from xm4_optimizer.nvdm.peb import (
    NVDM_PARTITION_SIZE,
    parse_peb_header,
    PEB_MAGIC,
    PEB_SIZE,
    PebHeader,
    PebStatus,
    serialize_peb_header,
    TOTAL_PEBS,
)
from xm4_optimizer.transport.virtual_device import VirtualXM4Device


class TestNvdmPeb:
    """Tests PEB header parsing, wear-leveling statuses, and NOR bitwise transitions."""

    def test_peb_header_validation(self) -> None:
        raw_header = struct.pack("<IIBBBB", PEB_MAGIC, 42, int(PebStatus.ACTIVED), 0xF0, 0x01, 0x00)
        hdr = parse_peb_header(raw_header, pnum=0)
        assert hdr.is_valid_magic is True
        assert hdr.magic == PEB_MAGIC
        assert hdr.erase_count == 42
        assert hdr.status == PebStatus.ACTIVED
        assert hdr.is_active is True
        assert hdr.is_empty is False

    def test_peb_lifecycle_statuses(self) -> None:
        # Valid NOR transitions (bits 1 -> 0 only)
        h_virgin = PebHeader(PEB_MAGIC, 0, PebStatus.VIRGIN, 0xF0, 1, 0)
        assert h_virgin.can_transition_to(PebStatus.EMPTY) is True

        h_empty = PebHeader(PEB_MAGIC, 0, PebStatus.EMPTY, 0xF0, 1, 0)
        assert h_empty.can_transition_to(PebStatus.ACTIVING) is True

        h_activing = PebHeader(PEB_MAGIC, 0, PebStatus.ACTIVING, 0xF0, 1, 0)
        assert h_activing.can_transition_to(PebStatus.ACTIVED) is True

        h_actived = PebHeader(PEB_MAGIC, 0, PebStatus.ACTIVED, 0xF0, 1, 0)
        assert h_actived.can_transition_to(PebStatus.RECLAIMING) is True

        # Invalid transition: ACTIVED (0xE0) -> EMPTY (0xFE) requires flipping 0 to 1 without erase
        assert h_actived.can_transition_to(PebStatus.EMPTY) is False

    def test_peb_header_serialize_roundtrip(self) -> None:
        original = PebHeader(
            magic=PEB_MAGIC,
            erase_count=100,
            status=PebStatus.ACTIVED,
            peb_reserved=0xF0,
            version=1,
            reserved=0,
            pnum=3,
        )
        serialized = serialize_peb_header(original)
        assert len(serialized) == 12
        restored = parse_peb_header(serialized, pnum=3)
        assert restored.magic == original.magic
        assert restored.erase_count == original.erase_count
        assert restored.status == original.status

    def test_peb_short_buffer_raises(self) -> None:
        with pytest.raises(ValueError, match="too short"):
            parse_peb_header(b"short")


class TestNvdmItem:
    """Tests 20-byte item headers, BKDR hash, and interleaved 16-bit checksums."""

    def test_item_header_unpack(self) -> None:
        raw_header = struct.pack(
            "<BBHHBBHBBII",
            int(DataItemStatus.VALID),
            0,
            0,
            12,
            3,
            10,
            108,
            0,
            int(DataItemType.RAW),
            1,
            0x85F5B85C,
        )
        hdr = parse_item_header(raw_header)
        assert hdr.status == DataItemStatus.VALID
        assert hdr.pnum == 0
        assert hdr.offset == 12
        assert hdr.group_name_size == 3
        assert hdr.data_item_name_size == 10
        assert hdr.value_size == 108
        assert hdr.sequence_number == 1
        assert hdr.hash_name == 0x85F5B85C
        assert hdr.total_record_size == 20 + 3 + 10 + 108 + 2

    def test_bkdr_hash_known_vectors(self) -> None:
        assert calculate_data_item_hash("DNC", "ALIGN_FF_L") == 0x85F5B85C
        assert calculate_data_item_hash("DNC", "ALIGN_FF_R") == 0x85F5B862
        assert calculate_data_item_hash("DNC", "ALIGN_FB_L") == 0x96CD6080
        assert calculate_data_item_hash("DNC", "ALIGN_FB_R") == 0x96CD6086
        assert calculate_data_item_hash("DNC", "HOWLING_PRE_PARAM") == 0xC2DF0429
        assert calculate_data_item_hash("DNC", "HOWLING_DET_PARAM") == 0xF2C1BFB1
        assert calculate_data_item_hash("DNC", "OPTIMIZER_PARAM") == 0x640C9A4E

    def test_interleaved_checksum(self) -> None:
        d = b"ABCDEFGHIJKLMNOPQRST"
        # even indexed: A, C, E, G, I, K, M, O, Q, S
        # odd indexed:  B, D, F, H, J, L, N, P, R, T
        s_even = sum(d[0::2]) & 0xFF
        s_odd = sum(d[1::2]) & 0xFF
        expected = (s_odd << 8) | s_even

        # Header bytes 1..19 is 19 bytes, so test calculate_data_item_checksum
        h19 = d[:19]
        grp = b"DNC"
        name = b"PARAM"
        val = b"VALUE"
        calc = calculate_data_item_checksum(h19, grp, name, val)
        total_buf = h19 + grp + name + val
        assert (calc & 0xFF) == (sum(total_buf[0::2]) & 0xFF)
        assert ((calc >> 8) & 0xFF) == (sum(total_buf[1::2]) & 0xFF)

    def test_checksum_invariance_on_deletion(self) -> None:
        group = "DNC"
        name = "ALIGN_FF_L"
        value = b"\x12" * 108
        rec_valid = serialize_item_record(
            group_name=group,
            item_name=name,
            value=value,
            pnum=0,
            offset=12,
            sequence_number=1,
            status=DataItemStatus.VALID,
        )

        stored_chk = struct.unpack("<H", rec_valid[20 + len(group) + len(name) + len(value) : 20 + len(group) + len(name) + len(value) + 2])[0]

        # Mutate status byte in-place: VALID (0xFC) -> DELETE (0xF8)
        rec_deleted = bytearray(rec_valid)
        rec_deleted[0] = int(DataItemStatus.DELETE)

        # Recalculate checksum over rec_deleted (excluding byte 0)
        core_len_no_status = 19 + len(group) + len(name) + len(value)
        data_no_status = rec_deleted[1 : 1 + core_len_no_status]
        chk_recalculated = ((sum(data_no_status[1::2]) & 0xFF) << 8) | (sum(data_no_status[0::2]) & 0xFF)

        assert chk_recalculated == stored_chk


class TestCXD90050Models:
    """Tests CXD90050 Q28 fixed-point conversions, alignment blocks, and DNC parameters."""

    def test_biquad_coefficients_q28(self) -> None:
        # Standard filter coefficients
        orig = BiquadCoefficients(a1=-1.85, a2=0.88, b0=1.15, b1=-1.75, b2=0.82)
        q_tuple = orig.to_q28_tuple()
        assert len(q_tuple) == 5

        # Check conversion back
        restored = BiquadCoefficients.from_q28_tuple(q_tuple)
        assert abs(restored.a1 - orig.a1) < 1e-6
        assert abs(restored.a2 - orig.a2) < 1e-6
        assert abs(restored.b0 - orig.b0) < 1e-6
        assert abs(restored.b1 - orig.b1) < 1e-6
        assert abs(restored.b2 - orig.b2) < 1e-6

        # Packing
        packed = orig.pack()
        assert len(packed) == 20
        unpacked = BiquadCoefficients.unpack(packed)
        assert abs(unpacked.a1 - orig.a1) < 1e-6

    def test_alignment_block_pack_unpack(self) -> None:
        biquads = [
            BiquadCoefficients(a1=-1.8 + (i * 0.05), a2=0.85, b0=1.2, b1=-1.7, b2=0.8)
            for i in range(5)
        ]
        block = CXD90050AlignmentBlock(
            filter_id=FILTER_ID_ALIGN_FF_L,
            gain_db=1.5,
            biquads=biquads,
        )
        packed = serialize_alignment_block(block)
        assert len(packed) == 108

        restored = deserialize_alignment_block(packed)
        assert restored.filter_id == FILTER_ID_ALIGN_FF_L
        assert abs(restored.gain_db - 1.5) < 1e-5
        assert len(restored.biquads) == 5
        assert abs(restored.biquads[0].a1 - biquads[0].a1) < 1e-5

    def test_alignment_block_invalid_biquads_count(self) -> None:
        with pytest.raises(ValueError, match="must contain exactly 5 biquads"):
            CXD90050AlignmentBlock(filter_id=0, biquads=[BiquadCoefficients() for _ in range(4)])

    def test_howling_pre_param_pack_unpack(self) -> None:
        param = HowlingPreventionParam(
            enable=1,
            max_attenuation_db=-15.0,
            attack_time_ms=4.5,
            release_time_ms=45.0,
            notch_freq_low_hz=1200.0,
            notch_freq_high_hz=7500.0,
            notch_q=3.8,
            stability_margin_db=3.5,
        )
        packed = param.pack()
        assert len(packed) == 32
        restored = HowlingPreventionParam.unpack(packed)
        assert restored.enable == 1
        assert abs(restored.max_attenuation_db - (-15.0)) < 1e-5
        assert abs(restored.notch_freq_low_hz - 1200.0) < 1e-5

    def test_howling_det_param_pack_unpack(self) -> None:
        param = HowlingDetectionParam(
            threshold_dbfs=-20.0,
            persistence_frames=10,
            window_size=512,
            tonality_ratio=0.88,
            feedback_coherence=0.92,
            hold_time_ms=250.0,
        )
        packed = param.pack()
        assert len(packed) == 24
        restored = HowlingDetectionParam.unpack(packed)
        assert abs(restored.threshold_dbfs - (-20.0)) < 1e-5
        assert restored.persistence_frames == 10
        assert restored.window_size == 512

    def test_optimizer_param_pack_unpack(self) -> None:
        param = OptimizerParam(
            calibration_state=1,
            atmospheric_pressure_hpa=1015.0,
            pressure_trim_db=0.2,
            seal_score=0.98,
            glasses_detected=1,
            leakage_damping=1.3,
            canal_gain_l_db=1.8,
            canal_gain_r_db=1.7,
            target_ff_boost_db=8.0,
            timestamp=1690000000,
        )
        packed = param.pack()
        assert len(packed) == 40
        restored = OptimizerParam.unpack(packed)
        assert restored.calibration_state == 1
        assert abs(restored.atmospheric_pressure_hpa - 1015.0) < 1e-4
        assert restored.glasses_detected == 1
        assert restored.timestamp == 1690000000


class TestNvdmParser:
    """Tests 128KB image scanning, item extraction, sequence resolution, and edge cases."""

    def test_full_partition_extraction_from_virtual_device(self) -> None:
        device = VirtualXM4Device()
        partition = parse_nvdm_image(bytes(device.flash))

        assert len(partition.pebs) == TOTAL_PEBS
        assert len(partition.corrupted_items) == 0

        # Check all 7 required calibration keys are present
        required_keys = [
            ("DNC", "ALIGN_FF_L"),
            ("DNC", "ALIGN_FF_R"),
            ("DNC", "ALIGN_FB_L"),
            ("DNC", "ALIGN_FB_R"),
            ("DNC", "HOWLING_PRE_PARAM"),
            ("DNC", "HOWLING_DET_PARAM"),
            ("DNC", "OPTIMIZER_PARAM"),
        ]
        for key in required_keys:
            item = partition.get_item(key[0], key[1])
            assert item is not None, f"Required item {key} not found"
            assert item.is_valid is True
            assert item.is_checksum_valid is True
            assert item.is_hash_valid is True

        # Check lengths
        assert len(partition.get_value("DNC", "ALIGN_FF_L")) == 108
        assert len(partition.get_value("DNC", "HOWLING_PRE_PARAM")) == 32
        assert len(partition.get_value("DNC", "HOWLING_DET_PARAM")) == 24
        assert len(partition.get_value("DNC", "OPTIMIZER_PARAM")) == 40

        # Test dictionary protocol access
        assert "ALIGN_FF_L" in partition
        assert ("DNC", "ALIGN_FF_L") in partition
        assert "DNC/ALIGN_FF_L" in partition

    def test_extract_cxd90050_blocks(self) -> None:
        device = VirtualXM4Device()
        blocks = extract_cxd90050_blocks(bytes(device.flash))

        assert "ALIGN_FF_L" in blocks
        assert isinstance(blocks["ALIGN_FF_L"], CXD90050AlignmentBlock)
        assert "ALIGN_FF_R" in blocks
        assert isinstance(blocks["ALIGN_FF_R"], CXD90050AlignmentBlock)
        assert "ALIGN_FB_L" in blocks
        assert isinstance(blocks["ALIGN_FB_L"], CXD90050AlignmentBlock)
        assert "ALIGN_FB_R" in blocks
        assert isinstance(blocks["ALIGN_FB_R"], CXD90050AlignmentBlock)
        assert "HOWLING_PRE_PARAM" in blocks
        assert isinstance(blocks["HOWLING_PRE_PARAM"], HowlingPreventionParam)
        assert "HOWLING_DET_PARAM" in blocks
        assert isinstance(blocks["HOWLING_DET_PARAM"], HowlingDetectionParam)
        assert "OPTIMIZER_PARAM" in blocks
        assert isinstance(blocks["OPTIMIZER_PARAM"], OptimizerParam)

    def test_duplicate_key_sequence_resolution(self) -> None:
        # Create an image with two versions of the same key
        group = "DNC"
        name = "ALIGN_FF_L"
        val_v1 = b"\x11" * 108
        val_v2 = b"\x22" * 108

        # In PEB 0: seq 1, then seq 2
        img = bytearray(b"\xFF" * NVDM_PARTITION_SIZE)
        img[0:12] = struct.pack("<IIBBBB", PEB_MAGIC, 1, int(PebStatus.ACTIVED), 0xF0, 1, 0)

        rec1 = serialize_item_record(group, name, val_v1, pnum=0, offset=12, sequence_number=1)
        off1 = 12
        img[off1 : off1 + len(rec1)] = rec1

        off2 = off1 + len(rec1)
        rec2 = serialize_item_record(group, name, val_v2, pnum=0, offset=off2, sequence_number=2)
        img[off2 : off2 + len(rec2)] = rec2

        partition = parse_nvdm_image(bytes(img))
        active = partition.get_item(group, name)
        assert active is not None
        assert active.header.sequence_number == 2
        assert active.value == val_v2

    def test_sequence_wraparound(self) -> None:
        assert is_newer_sequence(0, 0xFFFFFFFF) is True
        assert is_newer_sequence(0xFFFFFFFF, 0) is False
        assert is_newer_sequence(5, 3) is True
        assert is_newer_sequence(3, 5) is False
        assert is_newer_sequence(10, 10) is False

    def test_deleted_item_excluded_from_active(self) -> None:
        group = "DNC"
        name = "TEST_ITEM"
        val = b"\x33" * 20

        img = bytearray(b"\xFF" * NVDM_PARTITION_SIZE)
        img[0:12] = struct.pack("<IIBBBB", PEB_MAGIC, 1, int(PebStatus.ACTIVED), 0xF0, 1, 0)

        rec = serialize_item_record(
            group, name, val, pnum=0, offset=12, sequence_number=1, status=DataItemStatus.DELETE
        )
        img[12 : 12 + len(rec)] = rec

        partition = parse_nvdm_image(bytes(img))
        assert partition.get_item(group, name) is None

    def test_corrupt_partition_size_raises(self) -> None:
        with pytest.raises(NvdmCorruptError, match="Invalid NVDM partition size"):
            parse_nvdm_image(b"\x00" * 65536)

    def test_corrupt_checksum_detection(self) -> None:
        device = VirtualXM4Device()
        flash = bytearray(device.flash)

        # Corrupt 1 byte in ALIGN_FF_L payload
        # PEB header is 12 bytes, item header is 20 bytes, group is 3 bytes, name is 10 bytes -> value starts at 12 + 20 + 3 + 10 = 45
        flash[50] ^= 0x01

        partition = parse_nvdm_image(bytes(flash))
        assert len(partition.corrupted_items) >= 1
        assert partition.corrupted_items[0].is_checksum_valid is False

        # In strict mode, raises NvdmChecksumError
        with pytest.raises(NvdmChecksumError):
            parse_nvdm_image(bytes(flash), strict=True)

    def test_synthetic_partition_serialization(self) -> None:
        items = [
            ("DNC", "CUSTOM_PARAM_1", b"CustomValue1"),
            ("DNC", "CUSTOM_PARAM_2", b"CustomValue2"),
        ]
        image = serialize_nvdm_partition(items)
        assert len(image) == NVDM_PARTITION_SIZE

        partition = parse_nvdm_image(image)
        assert partition.get_value("DNC", "CUSTOM_PARAM_1") == b"CustomValue1"
        assert partition.get_value("DNC", "CUSTOM_PARAM_2") == b"CustomValue2"
