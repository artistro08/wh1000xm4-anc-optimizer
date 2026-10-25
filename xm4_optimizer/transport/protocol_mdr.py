"""Sony MDR Protocol Framing, Byte Escaping, and Checksums.

Implements the Sony MDR framing protocol with delimiters 0x3E (start) and 0x3C (end),
0x3D collision byte escaping, big-endian length headers (>BBI), 8-bit checksums
modulo 256, and stateful streaming decoding.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
import struct
from typing import List, Optional, Tuple

START_MARKER: int = 0x3E  # '>'
END_MARKER: int = 0x3C    # '<'
ESCAPE_BYTE: int = 0x3D   # '='

MDR_INNER_HEADER_FORMAT: str = ">BBI"  # Type (1B), SeqNum (1B), PayloadLength (4B BE)
MDR_INNER_HEADER_SIZE: int = struct.calcsize(MDR_INNER_HEADER_FORMAT)  # 6 bytes
MDR_MIN_PACKET_SIZE: int = MDR_INNER_HEADER_SIZE + 1  # 7 bytes (header + checksum)


class MdrPacketType(IntEnum):
    COMMAND = 0x00
    RESPONSE = 0x01
    ACK = 0x02
    NOTIFICATION = 0x03
    SHORT_ACK = 0x05


# --- Exception Hierarchy ---


class MdrError(Exception):
    """Base exception for Sony MDR protocol errors."""


class MdrFrameError(MdrError):
    """Raised when framing markers are absent, misaligned, or corrupted."""


class MdrEscapeError(MdrError):
    """Raised on truncated or invalid byte escape sequence."""


class MdrChecksumError(MdrError):
    """Raised when MDR 8-bit checksum verification fails."""


class MdrLengthError(MdrError):
    """Raised when packet length field does not match received payload."""


# --- Escaping and Checksum Logic ---


def mdr_escape(data: bytes) -> bytes:
    """Escapes 0x3C, 0x3D, and 0x3E using 0x3D prefix and (byte - 0x10)."""
    out = bytearray()
    for b in data:
        if b in (END_MARKER, ESCAPE_BYTE, START_MARKER):
            out.append(ESCAPE_BYTE)
            out.append(b - 0x10)
        else:
            out.append(b)
    return bytes(out)


def mdr_unescape(escaped: bytes) -> bytes:
    """Reverses 0x3D escaping. Validates escaped bytes are in (0x2C, 0x2D, 0x2E)."""
    out = bytearray()
    i = 0
    n = len(escaped)
    while i < n:
        b = escaped[i]
        if b == ESCAPE_BYTE:
            if i + 1 >= n:
                raise MdrEscapeError("Truncated escape sequence at end of buffer")
            nb = escaped[i + 1]
            if nb not in (0x2C, 0x2D, 0x2E):
                raise MdrEscapeError(f"Invalid escaped byte 0x{nb:02X} after 0x3D")
            out.append(nb + 0x10)
            i += 2
        else:
            out.append(b)
            i += 1
    return bytes(out)


def compute_mdr_checksum(data: bytes) -> int:
    """Computes 8-bit sum modulo 256 over inner unescaped bytes."""
    return sum(data) & 0xFF


# --- Packet Dataclass & Stream Decoder ---


@dataclass(frozen=True)
class MdrPacket:
    packet_type: int
    seq_num: int
    payload: bytes = b""

    def to_wire_bytes(self) -> bytes:
        header = struct.pack(MDR_INNER_HEADER_FORMAT, self.packet_type, self.seq_num, len(self.payload))
        data_to_checksum = header + self.payload
        checksum = compute_mdr_checksum(data_to_checksum)
        unescaped = data_to_checksum + bytes([checksum])
        escaped = mdr_escape(unescaped)
        return bytes([START_MARKER]) + escaped + bytes([END_MARKER])

    @classmethod
    def from_wire_bytes(cls, wire_data: bytes) -> Tuple[Optional["MdrPacket"], bytes]:
        """Parses a single packet from wire stream. Returns (packet, remaining_bytes)."""
        start_idx = wire_data.find(bytes([START_MARKER]))
        if start_idx == -1:
            return None, wire_data

        end_idx = wire_data.find(bytes([END_MARKER]), start_idx + 1)
        if end_idx == -1:
            return None, wire_data[start_idx:]

        escaped_body = wire_data[start_idx + 1:end_idx]
        remaining = wire_data[end_idx + 1:]

        unescaped = mdr_unescape(escaped_body)
        if len(unescaped) < MDR_MIN_PACKET_SIZE:
            raise MdrFrameError(f"MDR packet too short ({len(unescaped)} < {MDR_MIN_PACKET_SIZE} bytes)")

        packet_type, seq_num, payload_len = struct.unpack(MDR_INNER_HEADER_FORMAT, unescaped[:MDR_INNER_HEADER_SIZE])
        expected_total_len = MDR_INNER_HEADER_SIZE + payload_len + 1
        if len(unescaped) != expected_total_len:
            raise MdrLengthError(f"Payload length mismatch: header specifies {payload_len}B, total unescaped is {len(unescaped)}B")

        payload = bytes(unescaped[MDR_INNER_HEADER_SIZE:MDR_INNER_HEADER_SIZE + payload_len])
        expected_checksum = unescaped[-1]
        actual_checksum = compute_mdr_checksum(unescaped[:-1])

        if actual_checksum != expected_checksum:
            raise MdrChecksumError(f"Checksum mismatch: expected 0x{expected_checksum:02X}, calculated 0x{actual_checksum:02X}")

        return cls(packet_type=packet_type, seq_num=seq_num, payload=payload), remaining


class MdrStreamDecoder:
    """Stateful stream decoder for incoming MDR byte streams."""

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, chunk: bytes) -> List[MdrPacket]:
        self._buffer.extend(chunk)
        packets: List[MdrPacket] = []
        while True:
            try:
                pkt, remaining = MdrPacket.from_wire_bytes(bytes(self._buffer))
                if pkt is None:
                    self._buffer = bytearray(remaining)
                    break
                packets.append(pkt)
                self._buffer = bytearray(remaining)
            except (MdrError, ValueError):
                # On corruption, drop up to next START_MARKER
                first_start = self._buffer.find(bytes([START_MARKER]))
                search_from = (first_start + 1) if first_start != -1 else 1
                next_start = self._buffer.find(bytes([START_MARKER]), search_from)
                if next_start != -1:
                    self._buffer = self._buffer[next_start:]
                    continue
                else:
                    self._buffer.clear()
                    break
        return packets
