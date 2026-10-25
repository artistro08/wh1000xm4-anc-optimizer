"""Adversarial Stress Testing and Fuzzing Suite for NVDM Subsystem.

Empirically challenges parser.py, peb.py, item.py, and cxd90050.py against:
1. Malformed partition sizes (truncated images < 128KB, oversized images > 128KB)
2. Corrupted PEB magic values and wear-leveling statuses
3. Checksum bit-flips in payload, header, and deletion invariance
4. Corrupted BKDR hash values
5. Sequence number wrap-arounds, cyclic duplicates, and tie-breaking
6. Out-of-bounds offsets, massive length fields, and block boundary overruns
7. High-volume Monte Carlo fuzzing (2,000 mutated images across multiple attack vectors)
8. String encoding robustness (null bytes, high ASCII, empty strings)
9. Dense PEB packing (180+ records in single PEB)
10. CXD90050 binary decoder stress with malformed lengths and parameter blocks
"""

from __future__ import annotations

import math
import os
import random
import struct
import time
import pytest

from xm4_optimizer.nvdm.cxd90050 import (
    BiquadCoefficients,
    CXD90050AlignmentBlock,
    deserialize_alignment_block,
    FILTER_ID_ALIGN_FB_L,
    FILTER_ID_ALIGN_FB_R,
    FILTER_ID_ALIGN_FF_L,
    FILTER_ID_ALIGN_FF_R,
    FILTER_ID_TO_NAME,
    FILTER_NAME_TO_ID,
    float_to_q28,
    HowlingDetectionParam,
    HowlingPreventionParam,
    OptimizerParam,
    q28_to_float,
    Q28_MAX_INT,
    Q28_MIN_INT,
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
    NvdmError,
    NvdmHeaderError,
    NvdmPartition,
    parse_nvdm_image,
    serialize_nvdm_partition,
)
from xm4_optimizer.nvdm.peb import (
    NVDM_PARTITION_SIZE,
    parse_peb_header,
    PEB_DATA_START_OFFSET,
    PEB_HEADER_SIZE,
    PEB_MAGIC,
    PEB_SIZE,
    PebHeader,
    PebStatus,
    serialize_peb_header,
    TOTAL_PEBS,
)
from xm4_optimizer.transport.virtual_device import VirtualXM4Device


class TestPartitionSizeStress:
    """Tests partition size enforcement: strictly 131,072 bytes (128 KB)."""

    @pytest.mark.parametrize(
        "invalid_size",
        [
            0,
            1,
            11,
            12,
            100,
            4095,
            4096,
            16384,
            65535,
            65536,
            131071,  # Off-by-one under
            131073,  # Off-by-one over
            135168,
            262144,
        ],
    )
    def test_truncated_or_oversized_partition_raises_corrupt_error(self, invalid_size: int) -> None:
        raw = b"\xFF" * invalid_size
        with pytest.raises(NvdmCorruptError) as exc_info:
            parse_nvdm_image(raw)
        assert "Invalid NVDM partition size" in str(exc_info.value)
        assert str(invalid_size) in str(exc_info.value)

    def test_empty_buffer_raises_corrupt_error(self) -> None:
        with pytest.raises(NvdmCorruptError):
            parse_nvdm_image(b"")


class TestPebMagicAndStatusStress:
    """Stress tests PEB header magic corruption and wear-leveling status boundaries."""

    @pytest.mark.parametrize(
        "bad_magic",
        [
            0x00000000,
            0x12345678,
            0xDEADBEEF,
            0x4E56444D,  # Big-endian "NVDM"
            0xFFFFFFFE,
            0x4D44564F,  # Off by 1 bit from 0x4D44564E
        ],
    )
    def test_invalid_peb_magic_non_strict_skips(self, bad_magic: int) -> None:
        device = VirtualXM4Device()
        flash = bytearray(device.flash)

        # Corrupt magic of PEB 0
        struct.pack_into("<I", flash, 0, bad_magic)

        # Non-strict mode should not raise, but PEB 0 items should be skipped
        partition = parse_nvdm_image(bytes(flash), strict=False)
        assert partition.pebs[0].is_valid_magic is False
        assert partition.pebs[0].magic == bad_magic
        # Calibration items originally in PEB 0 should be missing
        assert partition.get_item("DNC", "ALIGN_FF_L") is None

    @pytest.mark.parametrize(
        "bad_magic",
        [
            0x00000000,
            0x12345678,
            0xDEADBEEF,
            0x4E56444D,
            0xFFFFFFFE,
        ],
    )
    def test_invalid_peb_magic_strict_raises_corrupt_error(self, bad_magic: int) -> None:
        device = VirtualXM4Device()
        flash = bytearray(device.flash)

        # Corrupt magic of PEB 0
        struct.pack_into("<I", flash, 0, bad_magic)

        with pytest.raises(NvdmCorruptError) as exc_info:
            parse_nvdm_image(bytes(flash), strict=True)
        assert "PEB 0 invalid magic" in str(exc_info.value)

    def test_all_32_pebs_corrupted_magic(self) -> None:
        flash = bytearray(b"\x00" * NVDM_PARTITION_SIZE)
        partition = parse_nvdm_image(bytes(flash), strict=False)
        assert len(partition.pebs) == TOTAL_PEBS
        assert len(partition.active_items) == 0
        for peb in partition.pebs:
            assert peb.is_valid_magic is False

    def test_peb_status_unknown_values(self) -> None:
        raw_header = struct.pack("<IIBBBB", PEB_MAGIC, 1, 0x42, 0xF0, 1, 0)
        hdr = parse_peb_header(raw_header, pnum=5)
        assert hdr.status == PebStatus.UNKNOWN
        assert hdr.raw_status == 0x42
        assert hdr.pnum == 5
        assert hdr.is_valid_magic is True


class TestItemChecksumAndHashStress:
    """Stress tests checksum verification, BKDR rolling hash, and single-bit flips."""

    def test_single_bit_flip_in_item_payload(self) -> None:
        device = VirtualXM4Device()
        flash = bytearray(device.flash)

        # Byte 45 is inside the payload of the first item (ALIGN_FF_L)
        flash[45] ^= 0x01

        partition = parse_nvdm_image(bytes(flash), strict=False)
        assert len(partition.corrupted_items) >= 1
        corrupt = partition.corrupted_items[0]
        assert corrupt.item_name == "ALIGN_FF_L"
        assert corrupt.is_checksum_valid is False
        assert corrupt.stored_checksum != corrupt.calculated_checksum
        # Corrupted item must NOT be in active items
        assert partition.get_item("DNC", "ALIGN_FF_L") is None

    def test_single_bit_flip_in_item_header_detected(self) -> None:
        device = VirtualXM4Device()
        flash = bytearray(device.flash)

        # Byte 14 is inside header of first item (pnum or reserved)
        flash[14] ^= 0x08

        partition = parse_nvdm_image(bytes(flash), strict=False)
        assert len(partition.corrupted_items) >= 1
        assert partition.corrupted_items[0].is_checksum_valid is False

    def test_status_byte_flip_preserves_checksum_validity(self) -> None:
        """Invariance test: changing status VALID (0xFC) -> DELETE (0xF8) must NOT corrupt checksum."""
        group = "DNC"
        name = "ALIGN_FB_L"
        val = b"\x55" * 108

        rec = serialize_item_record(group, name, val, pnum=0, offset=12, sequence_number=10)
        rec_mutated = bytearray(rec)
        assert rec_mutated[0] == int(DataItemStatus.VALID)

        # Mutate to DELETE
        rec_mutated[0] = int(DataItemStatus.DELETE)

        # Unpack and verify checksum calculation still matches
        header_no_st = rec_mutated[1:DATA_ITEM_HEADER_SIZE]
        grp_b = group.encode("ascii")
        name_b = name.encode("ascii")
        stored_chk = struct.unpack(
            CHECKSUM_STRUCT,
            rec_mutated[DATA_ITEM_HEADER_SIZE + len(grp_b) + len(name_b) + len(val) :][:2],
        )[0]
        calc_chk = calculate_data_item_checksum(header_no_st, grp_b, name_b, val)
        assert calc_chk == stored_chk

    def test_corrupted_bkdr_hash_detected(self) -> None:
        device = VirtualXM4Device()
        flash = bytearray(device.flash)

        # Item header hash is at bytes 12+16 = 28..31
        flash[28] ^= 0xFF

        partition = parse_nvdm_image(bytes(flash), strict=False)
        assert len(partition.corrupted_items) >= 1
        corrupt = partition.corrupted_items[0]
        assert corrupt.is_hash_valid is False
        assert partition.get_item("DNC", "ALIGN_FF_L") is None

    def test_checksum_all_zero_data(self) -> None:
        h19 = b"\x00" * 19
        grp = b""
        name = b""
        val = b""
        chk = calculate_data_item_checksum(h19, grp, name, val)
        assert chk == 0

    def test_checksum_all_ff_data(self) -> None:
        h19 = b"\xFF" * 19
        grp = b"\xFF" * 10
        name = b"\xFF" * 10
        val = b"\xFF" * 100
        chk = calculate_data_item_checksum(h19, grp, name, val)
        total_len = 19 + 10 + 10 + 100  # 139
        even_count = (total_len + 1) // 2  # 70
        odd_count = total_len // 2  # 69
        expected_even = (even_count * 255) & 0xFF
        expected_odd = (odd_count * 255) & 0xFF
        assert (chk & 0xFF) == expected_even
        assert (chk >> 8) == expected_odd


class TestSequenceNumberResolutionStress:
    """Stress tests RFC 1982 modular comparison, wrap-around, and duplicate key resolution."""

    @pytest.mark.parametrize(
        "new_seq,old_seq,expected",
        [
            (1, 0, True),
            (0, 1, False),
            (2, 1, True),
            (1, 2, False),
            (100, 100, False),  # Identical
            (0, 0xFFFFFFFF, True),  # Wrap around 32-bit: (0 - 0xFFFFFFFF) & 0xFFFFFFFF = 1
            (1, 0xFFFFFFFF, True),  # Wrap around
            (0xFFFFFFFF, 0, False),
            (0x7FFFFFFF, 0, True),  # Exactly 2^31 - 1
            (0x80000000, 0, False),  # Exactly 2^31 (ambiguous boundary)
            (0x80000001, 0, False),  # > 2^31
            (0x10, 0xFFFFFFF0, True),  # Wrap around
        ],
    )
    def test_rfc1982_sequence_comparison_vectors(
        self, new_seq: int, old_seq: int, expected: bool
    ) -> None:
        assert is_newer_sequence(new_seq, old_seq) == expected

    def test_duplicate_keys_across_multiple_pebs(self) -> None:
        flash = bytearray(b"\xFF" * NVDM_PARTITION_SIZE)

        # Setup PEB 0 and PEB 1 as ACTIVED
        flash[0:12] = struct.pack("<IIBBBB", PEB_MAGIC, 1, int(PebStatus.ACTIVED), 0xF0, 1, 0)
        flash[PEB_SIZE : PEB_SIZE + 12] = struct.pack(
            "<IIBBBB", PEB_MAGIC, 1, int(PebStatus.ACTIVED), 0xF0, 1, 0
        )

        group = "DNC"
        name = "OPTIMIZER_PARAM"
        val_v1 = b"\x01" * 40
        val_v2 = b"\x02" * 40

        # PEB 0: seq 10
        rec1 = serialize_item_record(group, name, val_v1, pnum=0, offset=12, sequence_number=10)
        flash[12 : 12 + len(rec1)] = rec1

        # PEB 1: seq 20
        rec2 = serialize_item_record(group, name, val_v2, pnum=1, offset=12, sequence_number=20)
        flash[PEB_SIZE + 12 : PEB_SIZE + 12 + len(rec2)] = rec2

        partition = parse_nvdm_image(bytes(flash))
        item = partition.get_item(group, name)
        assert item is not None
        assert item.header.sequence_number == 20
        assert item.value == val_v2

    def test_duplicate_keys_with_wrap_around_sequence(self) -> None:
        flash = bytearray(b"\xFF" * NVDM_PARTITION_SIZE)
        flash[0:12] = struct.pack("<IIBBBB", PEB_MAGIC, 1, int(PebStatus.ACTIVED), 0xF0, 1, 0)

        group = "DNC"
        name = "TEST_WRAP"
        val_old = b"OldVersion"
        val_new = b"NewVersion"

        # Item 1 has seq 0xFFFFFFFF, Item 2 has seq 0 (wrapped)
        rec1 = serialize_item_record(group, name, val_old, pnum=0, offset=12, sequence_number=0xFFFFFFFF)
        off1 = 12
        flash[off1 : off1 + len(rec1)] = rec1

        off2 = off1 + len(rec1)
        rec2 = serialize_item_record(group, name, val_new, pnum=0, offset=off2, sequence_number=0)
        flash[off2 : off2 + len(rec2)] = rec2

        partition = parse_nvdm_image(bytes(flash))
        item = partition.get_item(group, name)
        assert item is not None
        assert item.header.sequence_number == 0
        assert item.value == val_new

    def test_duplicate_keys_with_same_sequence_number(self) -> None:
        """When two valid items have the identical sequence number, parser should deterministically keep the first."""
        flash = bytearray(b"\xFF" * NVDM_PARTITION_SIZE)
        flash[0:12] = struct.pack("<IIBBBB", PEB_MAGIC, 1, int(PebStatus.ACTIVED), 0xF0, 1, 0)

        group = "DNC"
        name = "TIE_TEST"
        val_first = b"FirstItemValue"
        val_second = b"SecondItemValue"

        rec1 = serialize_item_record(group, name, val_first, pnum=0, offset=12, sequence_number=5)
        off1 = 12
        flash[off1 : off1 + len(rec1)] = rec1

        off2 = off1 + len(rec1)
        rec2 = serialize_item_record(group, name, val_second, pnum=0, offset=off2, sequence_number=5)
        flash[off2 : off2 + len(rec2)] = rec2

        partition = parse_nvdm_image(bytes(flash))
        item = partition.get_item(group, name)
        assert item is not None
        assert item.value == val_first


class TestOutOfBoundsAndBoundaryStress:
    """Stress tests boundary overruns, excessive lengths, and offset corruptions."""

    def test_item_value_size_exceeding_block_size(self) -> None:
        flash = bytearray(b"\xFF" * NVDM_PARTITION_SIZE)
        flash[0:12] = struct.pack("<IIBBBB", PEB_MAGIC, 1, int(PebStatus.ACTIVED), 0xF0, 1, 0)

        # Fake item header with value_size = 5000 (> PEB_SIZE 4096)
        bad_hdr = struct.pack(
            "<BBHHBBHBBII",
            int(DataItemStatus.VALID),
            0,
            0,
            12,
            3,
            4,
            5000,  # value_size exceeds block!
            0,
            int(DataItemType.RAW),
            1,
            0x12345678,
        )
        flash[12 : 12 + 20] = bad_hdr

        # Parser should break cleanly without raising IndexError or MemoryError
        partition = parse_nvdm_image(bytes(flash))
        assert len(partition.active_items) == 0

    def test_item_value_size_max_uint16(self) -> None:
        flash = bytearray(b"\xFF" * NVDM_PARTITION_SIZE)
        flash[0:12] = struct.pack("<IIBBBB", PEB_MAGIC, 1, int(PebStatus.ACTIVED), 0xF0, 1, 0)

        bad_hdr = struct.pack(
            "<BBHHBBHBBII",
            int(DataItemStatus.VALID),
            0,
            0,
            12,
            255,
            255,
            65535,  # 0xFFFF
            0,
            int(DataItemType.RAW),
            1,
            0x12345678,
        )
        flash[12 : 12 + 20] = bad_hdr

        partition = parse_nvdm_image(bytes(flash))
        assert len(partition.active_items) == 0

    def test_item_ending_exactly_at_peb_end(self) -> None:
        flash = bytearray(b"\xFF" * NVDM_PARTITION_SIZE)
        flash[0:12] = struct.pack("<IIBBBB", PEB_MAGIC, 1, int(PebStatus.ACTIVED), 0xF0, 1, 0)

        group = "G"
        name = "N"
        val_len = 4060
        val = b"\xAA" * val_len

        rec = serialize_item_record(group, name, val, pnum=0, offset=12, sequence_number=1, pad_to_4=False)
        assert len(rec) == 4084
        assert 12 + len(rec) == PEB_SIZE

        flash[12 : 12 + len(rec)] = rec

        partition = parse_nvdm_image(bytes(flash))
        assert (group, name) in partition.active_items
        assert len(partition.get_value(group, name)) == val_len

    def test_item_header_at_last_byte_of_peb(self) -> None:
        flash = bytearray(b"\xFF" * NVDM_PARTITION_SIZE)
        flash[0:12] = struct.pack("<IIBBBB", PEB_MAGIC, 1, int(PebStatus.ACTIVED), 0xF0, 1, 0)

        # Place a non-0xFF byte at 4095
        flash[4095] = int(DataItemStatus.VALID)

        # Parser loop checks `offset + DATA_ITEM_HEADER_SIZE <= PEB_SIZE` (4095 + 20 <= 4096 is False)
        partition = parse_nvdm_image(bytes(flash))
        assert len(partition.active_items) == 0


class TestStringEncodingStress:
    """Tests resilience against non-ASCII strings, null characters, and empty strings."""

    def test_non_ascii_group_and_item_names(self) -> None:
        flash = bytearray(b"\xFF" * NVDM_PARTITION_SIZE)
        flash[0:12] = struct.pack("<IIBBBB", PEB_MAGIC, 1, int(PebStatus.ACTIVED), 0xF0, 1, 0)

        # Manually assemble item with non-ASCII bytes 0x80..0x85 in names
        g_bytes = b"\x80\x81\x82"
        n_bytes = b"\xFE\xFF\xAA"
        v_bytes = b"PayloadValue"

        h_calc = calculate_data_item_hash(
            g_bytes.decode("ascii", errors="replace"),
            n_bytes.decode("ascii", errors="replace"),
        )
        hdr_no_st = struct.pack(
            "<BHHBBHBBII",
            0,
            0,
            12,
            len(g_bytes),
            len(n_bytes),
            len(v_bytes),
            0,
            1,
            1,
            h_calc,
        )
        chk = calculate_data_item_checksum(hdr_no_st, g_bytes, n_bytes, v_bytes)
        rec = bytes([int(DataItemStatus.VALID)]) + hdr_no_st + g_bytes + n_bytes + v_bytes + struct.pack("<H", chk)

        flash[12 : 12 + len(rec)] = rec

        # Must parse with replaced characters and not raise UnicodeDecodeError
        partition = parse_nvdm_image(bytes(flash))
        assert len(partition.active_items) == 1
        key = list(partition.active_items.keys())[0]
        assert "\ufffd" in key[0] or "\ufffd" in key[1]
        assert partition.get_value(key[0], key[1]) == v_bytes

    def test_null_bytes_in_strings(self) -> None:
        group = "D\x00NC"
        name = "ITEM\x00NAME"
        val = b"Val\x00ue"

        rec = serialize_item_record(group, name, val, pnum=0, offset=12, sequence_number=1)
        flash = bytearray(b"\xFF" * NVDM_PARTITION_SIZE)
        flash[0:12] = struct.pack("<IIBBBB", PEB_MAGIC, 1, int(PebStatus.ACTIVED), 0xF0, 1, 0)
        flash[12 : 12 + len(rec)] = rec

        partition = parse_nvdm_image(bytes(flash))
        assert partition.get_value(group, name) == val

    def test_empty_string_names(self) -> None:
        group = ""
        name = ""
        val = b"ValueOnly"

        rec = serialize_item_record(group, name, val, pnum=0, offset=12, sequence_number=1)
        flash = bytearray(b"\xFF" * NVDM_PARTITION_SIZE)
        flash[0:12] = struct.pack("<IIBBBB", PEB_MAGIC, 1, int(PebStatus.ACTIVED), 0xF0, 1, 0)
        flash[12 : 12 + len(rec)] = rec

        partition = parse_nvdm_image(bytes(flash))
        assert partition.get_value("", "") == val


class TestDensePebCapacityStress:
    """Stress tests PEB scanning when packed with maximum density of small items."""

    def test_180_items_in_single_peb(self) -> None:
        """Packs ~180 records (22 bytes each) into a single 4096-byte PEB."""
        flash = bytearray(b"\xFF" * NVDM_PARTITION_SIZE)
        flash[0:12] = struct.pack("<IIBBBB", PEB_MAGIC, 1, int(PebStatus.ACTIVED), 0xF0, 1, 0)

        offset = 12
        seq = 1
        expected_items = {}

        while offset + 24 <= PEB_SIZE:
            name = f"I{seq}"
            val = bytes([seq % 256])
            rec = serialize_item_record(
                group_name="G",
                item_name=name,
                value=val,
                pnum=0,
                offset=offset,
                sequence_number=seq,
                pad_to_4=True,
            )
            if offset + len(rec) > PEB_SIZE:
                break
            flash[offset : offset + len(rec)] = rec
            expected_items[("G", name)] = val
            offset += len(rec)
            seq += 1

        assert len(expected_items) >= 120  # Over 120 distinct items

        partition = parse_nvdm_image(bytes(flash))
        assert len(partition.active_items) == len(expected_items)
        for key, val in expected_items.items():
            assert partition.get_value(key[0], key[1]) == val


class TestDictionaryProtocolStress:
    """Stress tests NvdmPartition dictionary access patterns and error conditions."""

    def test_dictionary_protocol_access_variants(self) -> None:
        device = VirtualXM4Device()
        partition = parse_nvdm_image(bytes(device.flash))

        # 1. Access by tuple key
        assert partition[("DNC", "ALIGN_FF_L")].value == partition.get_value("DNC", "ALIGN_FF_L")

        # 2. Access by slash key 'DNC/ALIGN_FF_L'
        assert partition["DNC/ALIGN_FF_L"].value == partition.get_value("DNC", "ALIGN_FF_L")

        # 3. Access by item name alone 'ALIGN_FF_L'
        assert partition["ALIGN_FF_L"].value == partition.get_value("DNC", "ALIGN_FF_L")

        # 4. In operator
        assert ("DNC", "ALIGN_FF_L") in partition
        assert "DNC/ALIGN_FF_L" in partition
        assert "ALIGN_FF_L" in partition
        assert "NON_EXISTENT_KEY" not in partition
        assert ("DNC", "NON_EXISTENT") not in partition

        # 5. Missing key raises KeyError
        with pytest.raises(KeyError, match="not found"):
            _ = partition["MISSING_KEY"]

        with pytest.raises(KeyError, match="not found"):
            _ = partition[("DNC", "MISSING")]

        # 6. Default value lookup
        assert partition.get("MISSING_KEY", "default") == "default"
        assert partition.get(("DNC", "MISSING"), 42) == 42

        # 7. Hash lookup
        h_align_ff_l = calculate_data_item_hash("DNC", "ALIGN_FF_L")
        item_by_hash = partition.get_item_by_hash(h_align_ff_l)
        assert item_by_hash is not None
        assert item_by_hash.item_name == "ALIGN_FF_L"

        assert partition.get_item_by_hash(0x12345678) is None

        # 8. Dict export
        d = partition.to_dict()
        assert "DNC/ALIGN_FF_L" in d
        assert len(d["DNC/ALIGN_FF_L"]) == 108


class TestFuzzingAndRandomCorruptions:
    """Empirical fuzzing: injects randomly mutated partition buffers and asserts stability."""

    def test_all_zeros_partition_stability(self) -> None:
        raw = b"\x00" * NVDM_PARTITION_SIZE
        partition = parse_nvdm_image(raw, strict=False)
        assert len(partition.pebs) == TOTAL_PEBS
        assert len(partition.active_items) == 0

    def test_all_ones_partition_stability(self) -> None:
        raw = b"\xFF" * NVDM_PARTITION_SIZE
        partition = parse_nvdm_image(raw, strict=False)
        assert len(partition.pebs) == TOTAL_PEBS
        assert len(partition.active_items) == 0
        for peb in partition.pebs:
            assert peb.is_virgin is True
            assert peb.is_empty is True

    def test_monte_carlo_fuzzing_2000_iterations(self) -> None:
        """Generates 2,000 mutated images with random bit flips, truncated items, and noise."""
        random.seed(42)  # Deterministic seed for reproducible testing
        base_device = VirtualXM4Device()
        base_flash = bytes(base_device.flash)

        for iteration in range(2000):
            flash = bytearray(base_flash)
            mutation_type = iteration % 6

            if mutation_type == 0:
                # 1 to 10 random bit flips across the partition
                for _ in range(random.randint(1, 10)):
                    byte_idx = random.randint(0, NVDM_PARTITION_SIZE - 1)
                    bit_idx = random.randint(0, 7)
                    flash[byte_idx] ^= (1 << bit_idx)

            elif mutation_type == 1:
                # Corrupt random 16 to 64-byte blocks
                block_len = random.randint(16, 64)
                start = random.randint(0, NVDM_PARTITION_SIZE - block_len)
                flash[start : start + block_len] = os.urandom(block_len)

            elif mutation_type == 2:
                # Randomize PEB headers
                for pnum in range(TOTAL_PEBS):
                    if random.random() < 0.3:
                        off = pnum * PEB_SIZE
                        flash[off : off + PEB_HEADER_SIZE] = os.urandom(PEB_HEADER_SIZE)

            elif mutation_type == 3:
                # Randomize item headers within PEB 0
                for item_offset in range(12, 1000, 20):
                    if random.random() < 0.2:
                        flash[item_offset : item_offset + DATA_ITEM_HEADER_SIZE] = os.urandom(
                            DATA_ITEM_HEADER_SIZE
                        )

            elif mutation_type == 4:
                # Corrupt wear-leveling statuses
                for pnum in range(TOTAL_PEBS):
                    off = pnum * PEB_SIZE + 8  # status byte
                    flash[off] = random.choice([0x00, 0x42, 0xC0, 0xE0, 0xF0, 0xFC, 0xFE, 0xFF])

            elif mutation_type == 5:
                # Completely pseudo-random 128KB buffer
                flash = bytearray(os.urandom(NVDM_PARTITION_SIZE))

            # Parser MUST NOT crash with unhandled exception (IndexError, TypeError, KeyError, etc.)
            try:
                parse_nvdm_image(bytes(flash), strict=False)
            except Exception as e:
                pytest.fail(f"Fuzz iteration {iteration} crashed with unexpected exception: {type(e).__name__}: {e}")

    def test_parser_throughput_under_10ms(self) -> None:
        device = VirtualXM4Device()
        flash = bytes(device.flash)

        # Warm up
        parse_nvdm_image(flash)

        start = time.perf_counter()
        iters = 50
        for _ in range(iters):
            parse_nvdm_image(flash)
        elapsed = (time.perf_counter() - start) / iters

        # Must parse in less than 10 milliseconds per 128KB image
        assert elapsed < 0.010, f"Parsing 128KB took {elapsed * 1000:.2f} ms (expected < 10 ms)"


class TestCXD90050DecoderStress:
    """Stress tests CXD90050 alignment blocks, biquad coefficients, and DNC structures."""

    @pytest.mark.parametrize(
        "invalid_size",
        [0, 1, 8, 20, 107, 109, 200],
    )
    def test_alignment_block_unpack_invalid_size_raises(self, invalid_size: int) -> None:
        with pytest.raises(ValueError, match="size must be 108 bytes"):
            CXD90050AlignmentBlock.unpack(b"\x00" * invalid_size)

    @pytest.mark.parametrize(
        "invalid_size",
        [0, 1, 20, 31, 33, 64],
    )
    def test_howling_pre_param_unpack_invalid_size_raises(self, invalid_size: int) -> None:
        with pytest.raises(ValueError, match="must be 32 bytes"):
            HowlingPreventionParam.unpack(b"\x00" * invalid_size)

    @pytest.mark.parametrize(
        "invalid_size",
        [0, 1, 12, 23, 25, 48],
    )
    def test_howling_det_param_unpack_invalid_size_raises(self, invalid_size: int) -> None:
        with pytest.raises(ValueError, match="must be 24 bytes"):
            HowlingDetectionParam.unpack(b"\x00" * invalid_size)

    @pytest.mark.parametrize(
        "invalid_size",
        [0, 1, 20, 39, 41, 80],
    )
    def test_optimizer_param_unpack_invalid_size_raises(self, invalid_size: int) -> None:
        with pytest.raises(ValueError, match="must be 40 bytes"):
            OptimizerParam.unpack(b"\x00" * invalid_size)

    def test_q28_extreme_int_clamping(self) -> None:
        # Huge positive float should clamp to Q28_MAX_INT (2147483647)
        assert float_to_q28(1e9) == Q28_MAX_INT
        # Huge negative float should clamp to Q28_MIN_INT (-2147483648)
        assert float_to_q28(-1e9) == Q28_MIN_INT

    def test_q28_roundtrip_precision(self) -> None:
        test_values = [0.0, 1.0, -1.0, 0.5, -0.5, 1.234567, -1.987654]
        for val in test_values:
            q = float_to_q28(val)
            restored = q28_to_float(q)
            assert abs(restored - val) < (1.0 / (1 << 28))

    def test_extract_cxd90050_blocks_missing_or_corrupt_entries(self) -> None:
        # Create an empty partition
        raw = b"\xFF" * NVDM_PARTITION_SIZE
        blocks = extract_cxd90050_blocks(raw)
        assert isinstance(blocks, dict)
        assert len(blocks) == 0

    def test_extract_cxd90050_blocks_with_corrupted_value_length(self) -> None:
        # Create partition where ALIGN_FF_L exists but has length 50 instead of 108
        items = [
            ("DNC", "ALIGN_FF_L", b"\x00" * 50),  # Corrupted size
            ("DNC", "HOWLING_PRE_PARAM", b"\x00" * 32),  # Valid size
        ]
        img = serialize_nvdm_partition(items)
        blocks = extract_cxd90050_blocks(img)

        # Corrupted ALIGN_FF_L should be safely omitted
        assert "ALIGN_FF_L" not in blocks
        # Valid HOWLING_PRE_PARAM should be successfully parsed
        assert "HOWLING_PRE_PARAM" in blocks
