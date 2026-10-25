"""Adversarial Stress, Fuzzing, and Fault-Injection Tests for Transport Subsystem.

Tests Sony MDR framing delimiter collisions, incomplete escapes, stream desync,
Airoha RACE framing faults, Dallas/Maxim CRC-8 corruptions, partition boundary
violations, socket connection drops, and mock device error handling.
"""

from __future__ import annotations

import os
import random
import struct
from unittest.mock import MagicMock
import pytest

from xm4_optimizer.transport.protocol_mdr import (
    compute_mdr_checksum,
    mdr_escape,
    mdr_unescape,
    MdrChecksumError,
    MdrEscapeError,
    MdrError,
    MdrFrameError,
    MdrLengthError,
    MdrPacket,
    MdrPacketType,
    MdrStreamDecoder,
    START_MARKER,
    END_MARKER,
    ESCAPE_BYTE,
)
from xm4_optimizer.transport.protocol_race import (
    AtciTunnelRequest,
    AtciTunnelResponse,
    compute_fota_checksum,
    FLASH_PAGE_SIZE,
    PageProgramRequest,
    PageProgramResponse,
    PageReadRequest,
    PageReadResponse,
    RaceFramingError,
    RaceHead,
    RaceHeaderType,
    RaceLengthError,
    RaceOpcode,
    RacePacket,
    RaceReturnCode,
    RaceStorageType,
)
from xm4_optimizer.transport.rfcomm_socket import (
    BluetoothTimeoutError,
    DeviceDisconnectedError,
    FlashOperationError,
    NVDM_PARTITION_BASE,
    NVDM_PARTITION_SIZE,
    read_nvdm_partition,
    RfcommSocket,
)
from xm4_optimizer.transport.virtual_device import VirtualXM4Device


class TestMdrFramingFuzzingAndStress:
    """Stress tests and fuzzers for Sony MDR protocol framing and escaping."""

    def test_mdr_delimiter_collisions_all_delimiters(self) -> None:
        """Verifies that payloads made entirely of delimiters (0x3E, 0x3C, 0x3D) roundtrip bit-exact."""
        for delimiter in (START_MARKER, END_MARKER, ESCAPE_BYTE):
            payload = bytes([delimiter] * 512)
            pkt = MdrPacket(packet_type=MdrPacketType.COMMAND, seq_num=42, payload=payload)
            wire = pkt.to_wire_bytes()

            # Wire format must begin with START_MARKER and end with END_MARKER
            assert wire[0] == START_MARKER
            assert wire[-1] == END_MARKER
            # The body between wire[1:-1] must NOT contain unescaped START_MARKER or END_MARKER
            # Any occurrence of 0x3E or 0x3C must be escaped
            unescaped_body = wire[1:-1]
            i = 0
            while i < len(unescaped_body):
                b = unescaped_body[i]
                if b == ESCAPE_BYTE:
                    i += 2
                else:
                    assert b not in (START_MARKER, END_MARKER, ESCAPE_BYTE), f"Unescaped byte 0x{b:02X} found in body"
                    i += 1

            unpacked, rem = MdrPacket.from_wire_bytes(wire)
            assert rem == b""
            assert unpacked is not None
            assert unpacked.payload == payload
            assert unpacked.seq_num == 42

    def test_mdr_escape_sequence_lookalikes_in_payload(self) -> None:
        """Verifies that raw payload bytes resembling escape sequences (0x3D 0x2C, 0x3D 0x2D, 0x3D 0x2E) roundtrip."""
        lookalike_payload = bytes([ESCAPE_BYTE, 0x2C, ESCAPE_BYTE, 0x2D, ESCAPE_BYTE, 0x2E]) * 50
        pkt = MdrPacket(packet_type=MdrPacketType.RESPONSE, seq_num=1, payload=lookalike_payload)
        wire = pkt.to_wire_bytes()

        unpacked, rem = MdrPacket.from_wire_bytes(wire)
        assert rem == b""
        assert unpacked is not None
        assert unpacked.payload == lookalike_payload

    def test_mdr_random_fuzz_payloads_roundtrip(self) -> None:
        """Fuzzes 200 arbitrary pseudo-random byte payloads of various lengths."""
        rng = random.Random(1337)
        for i in range(200):
            length = rng.randint(0, 1024)
            payload = rng.randbytes(length)
            seq = i % 256
            pkt_type = rng.choice([0, 1, 2, 3, 5])

            pkt = MdrPacket(packet_type=pkt_type, seq_num=seq, payload=payload)
            wire = pkt.to_wire_bytes()

            unpacked, rem = MdrPacket.from_wire_bytes(wire)
            assert rem == b""
            assert unpacked is not None
            assert unpacked.packet_type == pkt_type
            assert unpacked.seq_num == seq
            assert unpacked.payload == payload

    def test_mdr_incomplete_escape_trailing_byte(self) -> None:
        """mdr_unescape must raise MdrEscapeError when buffer terminates on 0x3D."""
        with pytest.raises(MdrEscapeError, match="Truncated escape sequence"):
            mdr_unescape(b"\x01\x02\x03" + bytes([ESCAPE_BYTE]))

    def test_mdr_invalid_escaped_bytes_rejection(self) -> None:
        """mdr_unescape must reject 0x3D followed by bytes not in (0x2C, 0x2D, 0x2E)."""
        invalid_escapes = [0x00, 0x10, 0x2B, 0x2F, 0x3C, 0x3D, 0x3E, 0xFF]
        for bad_byte in invalid_escapes:
            with pytest.raises(MdrEscapeError, match="Invalid escaped byte"):
                mdr_unescape(bytes([ESCAPE_BYTE, bad_byte]))

    def test_mdr_checksum_single_bitflip_matrix(self) -> None:
        """Verifies that flipping any single bit in wire payload or checksum is detected."""
        pkt = MdrPacket(packet_type=MdrPacketType.COMMAND, seq_num=10, payload=b"FixedTestData1234")
        wire = bytearray(pkt.to_wire_bytes())

        # Test flipping bits in the payload/inner section (bytes 2 to -2)
        for byte_idx in range(1, len(wire) - 1):
            for bit in range(8):
                corrupted = bytearray(wire)
                corrupted[byte_idx] ^= (1 << bit)
                # Should either raise MdrChecksumError, MdrLengthError, MdrEscapeError, or MdrFrameError
                with pytest.raises(MdrError):
                    unpacked, _ = MdrPacket.from_wire_bytes(bytes(corrupted))
                    # If it somehow unpacked without error, it's a test failure
                    if unpacked is not None and unpacked.payload == b"FixedTestData1234":
                        pytest.fail(f"Bit flip at byte {byte_idx} bit {bit} was not detected!")

    def test_mdr_stream_decoder_byte_by_byte(self) -> None:
        """Verifies MdrStreamDecoder correctly reassembles stream when fed 1 byte at a time."""
        pkt = MdrPacket(packet_type=MdrPacketType.COMMAND, seq_num=99, payload=b"StreamingByteByByte")
        wire = pkt.to_wire_bytes()

        decoder = MdrStreamDecoder()
        received: list[MdrPacket] = []
        for b in wire:
            pkts = decoder.feed(bytes([b]))
            received.extend(pkts)

        assert len(received) == 1
        assert received[0].payload == b"StreamingByteByByte"
        assert received[0].seq_num == 99

    def test_mdr_stream_decoder_random_chunk_sizes(self) -> None:
        """Feeds a concatenation of 10 packets in randomized chunk sizes (1 to 13 bytes)."""
        rng = random.Random(42)
        expected_pkts = [
            MdrPacket(packet_type=MdrPacketType.COMMAND, seq_num=i, payload=f"PktPayload_{i}".encode("ascii"))
            for i in range(10)
        ]
        stream = b"".join(p.to_wire_bytes() for p in expected_pkts)

        decoder = MdrStreamDecoder()
        received: list[MdrPacket] = []
        idx = 0
        while idx < len(stream):
            chunk_size = rng.randint(1, 13)
            chunk = stream[idx : idx + chunk_size]
            idx += chunk_size
            received.extend(decoder.feed(chunk))

        assert len(received) == 10
        for i in range(10):
            assert received[i].seq_num == i
            assert received[i].payload == f"PktPayload_{i}".encode("ascii")

    def test_mdr_stream_decoder_noise_interleaved(self) -> None:
        """Verifies that MdrStreamDecoder drops noise bytes between valid frames."""
        pkt1 = MdrPacket(packet_type=MdrPacketType.COMMAND, seq_num=1, payload=b"FirstPacket")
        pkt2 = MdrPacket(packet_type=MdrPacketType.RESPONSE, seq_num=2, payload=b"SecondPacket")

        noise = b"\x00\xFF\x12\x34GARBAGE"
        combined = noise + pkt1.to_wire_bytes() + noise + pkt2.to_wire_bytes() + noise

        decoder = MdrStreamDecoder()
        received = decoder.feed(combined)
        assert len(received) == 2
        assert received[0].payload == b"FirstPacket"
        assert received[1].payload == b"SecondPacket"

    def test_mdr_stream_decoder_recovery_from_corrupted_packet_in_same_chunk(self) -> None:
        """EMPIRICAL CHALLENGE: When a corrupted packet is followed by a valid packet in the same chunk,

        the valid packet must be returned by feed() without requiring extraneous dummy calls.
        """
        decoder = MdrStreamDecoder()
        valid_pkt = MdrPacket(packet_type=MdrPacketType.COMMAND, seq_num=1, payload=b"valid_packet")
        corrupted_pkt = bytearray(valid_pkt.to_wire_bytes())
        # Corrupt one byte of inner checksum/payload
        corrupted_pkt[5] ^= 0x55

        combined = bytes(corrupted_pkt) + valid_pkt.to_wire_bytes()
        pkts = decoder.feed(combined)

        # In current protocol_mdr.py line 170, the decoder executes 'break' after dropping
        # corrupted bytes up to next_start, failing to parse valid_pkt during feed(combined).
        if len(pkts) == 0:
            pytest.fail(
                "MdrStreamDecoder failed to return the valid packet: 'break' statement on line 170 "
                "prematurely halts the decoder loop instead of continuing to parse the buffered next packet."
            )
        assert len(pkts) == 1
        assert pkts[0].payload == b"valid_packet"


class TestRaceProtocolFuzzingAndStress:
    """Stress tests and fuzzers for Airoha RACE protocol framing and opcodes."""

    def test_race_packet_fuzz_truncated_headers(self) -> None:
        """RACE header unpack must raise RaceFramingError on any input < 6 bytes."""
        for length in range(6):
            truncated = os.urandom(length)
            with pytest.raises(RaceFramingError, match="Buffer too short for RACE header"):
                RacePacket.from_bytes(truncated)

    def test_race_packet_fuzz_invalid_length_fields(self) -> None:
        """RACE packets with length < 2 must raise RaceLengthError."""
        for bad_len in (0, 1):
            data = struct.pack("<BBHH", RaceHead.STANDARD, RaceHeaderType.CMD_EXPECTS_RESPONSE, bad_len, RaceOpcode.PAGE_READ)
            with pytest.raises(RaceLengthError, match="Invalid RACE length"):
                RacePacket.from_bytes(data)

    def test_race_packet_fuzz_payload_truncated(self) -> None:
        """RACE packet specifying payload length greater than buffer must raise RaceFramingError."""
        data = struct.pack("<BBHH", RaceHead.STANDARD, RaceHeaderType.CMD_EXPECTS_RESPONSE, 20, RaceOpcode.PAGE_READ) + b"short"
        with pytest.raises(RaceFramingError, match="Incomplete packet"):
            RacePacket.from_bytes(data)

    def test_fota_crc8_random_data_integrity(self) -> None:
        """Tests Dallas/Maxim CRC-8 across 100 random 256-byte blocks and verifies bitflips change the CRC."""
        rng = random.Random(999)
        for _ in range(100):
            data = bytearray(rng.randbytes(FLASH_PAGE_SIZE))
            orig_crc = compute_fota_checksum(data)

            # Flip random bit
            byte_idx = rng.randint(0, FLASH_PAGE_SIZE - 1)
            bit_idx = rng.randint(0, 7)
            data[byte_idx] ^= (1 << bit_idx)

            corrupted_crc = compute_fota_checksum(data)
            assert orig_crc != corrupted_crc, f"CRC8 collision on bitflip at byte {byte_idx} bit {bit_idx}"

    def test_race_page_read_unaligned_address_rejection(self) -> None:
        """PageReadRequest must reject unaligned flash addresses."""
        unaligned_addrs = [0x000DF001, 0x000DF002, 0x000DF07F, 0x000DF0FF, 0x000DF101]
        for addr in unaligned_addrs:
            with pytest.raises(ValueError, match="must be aligned to 256 bytes"):
                PageReadRequest(address=addr).to_packet()

    def test_race_page_program_unaligned_address_rejection(self) -> None:
        """PageProgramRequest must reject unaligned flash addresses."""
        unaligned_addrs = [0x000DF001, 0x000DF07F, 0x000DF0FF, 0x000DF101]
        for addr in unaligned_addrs:
            with pytest.raises(ValueError, match="must be aligned to 256 bytes"):
                PageProgramRequest(address=addr, data=b"\x00" * FLASH_PAGE_SIZE).to_packet()

    def test_race_page_program_wrong_length_rejection(self) -> None:
        """PageProgramRequest must reject data lengths not equal to 256 bytes."""
        bad_lengths = [0, 1, 100, 255, 257, 512]
        for length in bad_lengths:
            with pytest.raises(ValueError, match="Data length must be exactly 256 bytes"):
                PageProgramRequest(address=0x000DF000, data=b"\xAA" * length).to_packet()

    def test_race_page_read_response_error_payload_handling(self) -> None:
        """EMPIRICAL CHALLENGE: PageReadResponse.from_payload must gracefully handle error responses

        (return_code != 0, 5-byte payload) without raising RaceFramingError.
        """
        # Airoha firmware returns return_code (1B) and address (4B) on flash read failure (5 bytes)
        err_payload = struct.pack("<BI", RaceReturnCode.INVALID_PARAM, 0x000DF000)

        # In current protocol_race.py line 169:
        # if len(payload) < 5 + FLASH_PAGE_SIZE:
        #     raise RaceFramingError(...)
        try:
            resp = PageReadResponse.from_payload(err_payload)
            assert resp.return_code == RaceReturnCode.INVALID_PARAM
            assert resp.address == 0x000DF000
        except RaceFramingError as e:
            pytest.fail(
                f"PageReadResponse.from_payload crashed with RaceFramingError on 5-byte error response: {e}. "
                "Line 169 unconditionally requires 261 bytes even when return_code != 0, making "
                "RfcommDeviceTransport.read_flash_page error handling completely unreachable."
            )


class TestVirtualXM4DeviceStressAndFaults:
    """Stress tests, partition boundaries, and fault injection for VirtualXM4Device."""

    def test_virtual_device_flash_boundary_limits(self) -> None:
        """Verifies strict adherence to Partition 6 boundaries (0x000DF000 to 0x000FF000)."""
        device = VirtualXM4Device()
        device.connect()

        # Lower boundary (page 0)
        p0 = device.read_flash_page(NVDM_PARTITION_BASE)
        assert len(p0) == FLASH_PAGE_SIZE

        # Upper boundary (page 511: 0x000FEF00)
        last_page_addr = NVDM_PARTITION_BASE + NVDM_PARTITION_SIZE - FLASH_PAGE_SIZE
        p511 = device.read_flash_page(last_page_addr)
        assert len(p511) == FLASH_PAGE_SIZE

        # Out of bounds: exactly at end of partition (0x000FF000)
        with pytest.raises(ValueError, match="out of partition bounds"):
            device.read_flash_page(NVDM_PARTITION_BASE + NVDM_PARTITION_SIZE)

        with pytest.raises(ValueError, match="out of partition bounds"):
            device.write_flash_page(NVDM_PARTITION_BASE + NVDM_PARTITION_SIZE, b"\x00" * FLASH_PAGE_SIZE)

        # Out of bounds: before partition base
        with pytest.raises(ValueError, match="out of partition bounds"):
            device.read_flash_page(NVDM_PARTITION_BASE - FLASH_PAGE_SIZE)

        with pytest.raises(ValueError, match="out of partition bounds"):
            device.write_flash_page(NVDM_PARTITION_BASE - FLASH_PAGE_SIZE, b"\x00" * FLASH_PAGE_SIZE)

        device.close()

    def test_virtual_device_unconnected_operations_raise(self) -> None:
        """Calling operations on an unconnected device must raise ConnectionError."""
        device = VirtualXM4Device()
        with pytest.raises(ConnectionError, match="Device is not connected"):
            device.read_flash_page(NVDM_PARTITION_BASE)

        with pytest.raises(ConnectionError, match="Device is not connected"):
            device.write_flash_page(NVDM_PARTITION_BASE, b"\x00" * FLASH_PAGE_SIZE)

        with pytest.raises(ConnectionError, match="Device is not connected"):
            device.send_atci_command("AT")

    def test_virtual_device_fault_injection_flags(self) -> None:
        """Verifies all fault injection hooks: fail_read, fail_write, corrupt_fota_crc, disconnect_on_write."""
        device = VirtualXM4Device()
        device.connect()

        # fail_read
        device.fail_read = True
        with pytest.raises(IOError, match="Simulated flash read hardware error"):
            device.read_flash_page(NVDM_PARTITION_BASE)
        device.fail_read = False

        # fail_write
        device.fail_write = True
        ok = device.write_flash_page(NVDM_PARTITION_BASE + 0x1000, b"\x00" * FLASH_PAGE_SIZE)
        assert ok is False
        device.fail_write = False

        # corrupt_fota_crc
        device.corrupt_fota_crc = True
        ok = device.write_flash_page(NVDM_PARTITION_BASE + 0x1000, b"\x00" * FLASH_PAGE_SIZE)
        assert ok is False
        device.corrupt_fota_crc = False

        # disconnect_on_write
        device.disconnect_on_write = True
        with pytest.raises(ConnectionResetError, match="Connection reset by peer"):
            device.write_flash_page(NVDM_PARTITION_BASE + 0x1000, b"\x00" * FLASH_PAGE_SIZE)
        assert device.is_connected is False

    def test_virtual_device_process_race_unaligned_program_graceful_handling(self) -> None:
        """EMPIRICAL CHALLENGE: process_race_packet must return an error response packet

        when receiving an unaligned PAGE_PROGRAM command, rather than crashing with an unhandled ValueError.
        """
        device = VirtualXM4Device()
        device.connect()

        bad_addr = 0x000DF001  # Unaligned
        data = b"\x00" * FLASH_PAGE_SIZE
        crc = compute_fota_checksum(data)
        payload = struct.pack("<BBBI", 0, 1, crc, bad_addr) + data
        pkt = RacePacket(
            head=RaceHead.FOTA,
            type=RaceHeaderType.CMD_EXPECTS_RESPONSE,
            cmd_id=RaceOpcode.PAGE_PROGRAM,
            payload=payload,
        )

        try:
            resp_wire = device.process_race_packet(pkt.to_bytes())
            resp_pkt, _ = RacePacket.from_bytes(resp_wire)
            assert resp_pkt.cmd_id == RaceOpcode.PAGE_PROGRAM
            ret_code = resp_pkt.payload[0]
            assert ret_code != RaceReturnCode.SUCCESS
        except ValueError as e:
            pytest.fail(
                f"VirtualXM4Device.process_race_packet crashed with unhandled ValueError: {e}. "
                "Line 233 does not wrap write_flash_page in a try-except block, crashing on invalid packet parameters."
            )

    def test_virtual_device_process_race_out_of_bounds_program_graceful_handling(self) -> None:
        """EMPIRICAL CHALLENGE: process_race_packet must return an error response packet

        when receiving an out-of-bounds PAGE_PROGRAM command, rather than crashing with unhandled ValueError.
        """
        device = VirtualXM4Device()
        device.connect()

        bad_addr = 0x000FF000  # Partition boundary is 0x000FF000
        data = b"\x00" * FLASH_PAGE_SIZE
        crc = compute_fota_checksum(data)
        payload = struct.pack("<BBBI", 0, 1, crc, bad_addr) + data
        pkt = RacePacket(
            head=RaceHead.FOTA,
            type=RaceHeaderType.CMD_EXPECTS_RESPONSE,
            cmd_id=RaceOpcode.PAGE_PROGRAM,
            payload=payload,
        )

        try:
            resp_wire = device.process_race_packet(pkt.to_bytes())
            resp_pkt, _ = RacePacket.from_bytes(resp_wire)
            ret_code = resp_pkt.payload[0]
            assert ret_code != RaceReturnCode.SUCCESS
        except ValueError as e:
            pytest.fail(
                f"VirtualXM4Device.process_race_packet crashed with unhandled ValueError: {e} on out-of-bounds write."
            )

    def test_virtual_device_process_mdr_rejects_truncated_escape(self) -> None:
        """EMPIRICAL CHALLENGE: VirtualXM4Device.process_mdr_packet must reject trailing 0x3D before 0x3C,

        rather than swallowing the end delimiter and decoding invalid bytes.
        """
        device = VirtualXM4Device()
        device.connect()

        # Construct packet with trailing escape byte immediately preceding END_MARKER
        # Type = 76 (0x4C) ensures that if END_MARKER (0x3C) is consumed as (0x3C + 0x10 = 0x4C),
        # the checksum matches the consumed byte.
        bad_wire = bytes([START_MARKER, 76, 0x00, 0x00, 0x00, 0x00, 0x00, ESCAPE_BYTE, END_MARKER])

        try:
            resp = device.process_mdr_packet(bad_wire)
            pytest.fail(
                f"VirtualXM4Device.process_mdr_packet accepted an illegal truncated escape sequence: {resp}. "
                "Line 268-270 swallows the END_MARKER (0x3C) instead of rejecting the truncated escape."
            )
        except (ValueError, MdrError):
            # Correct behavior: rejection
            pass

    def test_virtual_device_process_mdr_rejects_invalid_escape_byte(self) -> None:
        """EMPIRICAL CHALLENGE: VirtualXM4Device.process_mdr_packet must reject 0x3D followed by bytes

        not in (0x2C, 0x2D, 0x2E).
        """
        device = VirtualXM4Device()
        device.connect()

        # 0x3D followed by 0x00 is invalid according to Sony MDR specification.
        # Type = 16 (0x10) ensures checksum matches the unescaped 0x10 if 0x3D 0x00 is accepted.
        bad_wire = bytes([START_MARKER, 16, 0x00, 0x00, 0x00, 0x00, 0x00, ESCAPE_BYTE, 0x00, END_MARKER])

        try:
            resp = device.process_mdr_packet(bad_wire)
            pytest.fail(
                f"VirtualXM4Device.process_mdr_packet accepted an invalid escape sequence (0x3D 0x00): {resp}. "
                "process_mdr_packet does not validate the escaped byte against (0x2C, 0x2D, 0x2E)."
            )
        except (ValueError, MdrError):
            # Correct behavior: rejection
            pass

    def test_virtual_device_atci_stress_invalid_commands(self) -> None:
        """Tests VirtualXM4Device ATCI Hayes command interpreter under malformed input."""
        device = VirtualXM4Device()
        device.connect()

        # Empty command
        assert "ERROR" in device.send_atci_command("")
        # Unknown AT command
        assert "ERROR" in device.send_atci_command("AT+UNKNOWN=1,2,3")
        # Malformed COEFSET (missing params)
        assert "ERROR" in device.send_atci_command("AT+HWPTOOL=COEFSET,0")
        # Non-hex coefficients in COEFSET
        assert "ERROR" in device.send_atci_command("AT+HWPTOOL=COEFSET,0,0,ZZ,YY,XX,WW,VV")

        device.close()


class TestRfcommTransportStressAndSocketFaults:
    """Stress tests for RFCOMM socket timeout handling, disconnects, and partition reading."""

    def test_rfcomm_socket_broken_pipe_on_send(self) -> None:
        """RfcommSocket.send_all must translate BrokenPipeError into DeviceDisconnectedError and close."""
        sock = RfcommSocket("00:11:22:33:44:55", 21)
        mock_raw = MagicMock()
        mock_raw.sendall.side_effect = BrokenPipeError("Broken pipe")
        sock._sock = mock_raw

        with pytest.raises(DeviceDisconnectedError, match="Connection lost during send"):
            sock.send_all(b"test")

        assert sock._sock is None

    def test_rfcomm_socket_timeout_on_recv(self) -> None:
        """RfcommSocket.recv_exact must translate TimeoutError into BluetoothTimeoutError."""
        sock = RfcommSocket("00:11:22:33:44:55", 21)
        mock_raw = MagicMock()
        mock_raw.recv.side_effect = TimeoutError("Timed out")
        sock._sock = mock_raw

        with pytest.raises(BluetoothTimeoutError, match="Recv timed out waiting for"):
            sock.recv_exact(10)

    def test_rfcomm_socket_premature_close_on_recv(self) -> None:
        """RfcommSocket.recv_exact must detect premature close when recv returns empty bytes."""
        sock = RfcommSocket("00:11:22:33:44:55", 21)
        mock_raw = MagicMock()
        mock_raw.recv.return_value = b""  # EOF
        sock._sock = mock_raw

        with pytest.raises(DeviceDisconnectedError, match="Socket closed prematurely"):
            sock.recv_exact(10)

        assert sock._sock is None

    def test_read_nvdm_partition_unaligned_size_raises(self) -> None:
        """read_nvdm_partition must reject partition sizes not divisible by 256."""
        device = VirtualXM4Device()
        device.connect()

        with pytest.raises(ValueError, match="must be a multiple of 256 bytes"):
            read_nvdm_partition(device, size=1000)

        device.close()

    def test_read_nvdm_partition_hardware_failure_aborts(self) -> None:
        """read_nvdm_partition must abort when hardware read fails mid-stream."""
        device = VirtualXM4Device()
        device.connect()
        device.fail_read = True

        with pytest.raises(IOError, match="Simulated flash read hardware error"):
            read_nvdm_partition(device)

        device.close()
