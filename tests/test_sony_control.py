"""Tests for the MDR v1 control client and the SDP channel lookup.

Payload fixtures were captured from a WH-1000XM4 on firmware 2.5.0 (2026-10-26).
"""

from __future__ import annotations

import socket
import uuid

import pytest

from xm4_optimizer.transport.protocol_mdr import MdrPacket
from xm4_optimizer.transport.sdp import find_rfcomm_channels, parse_data_element
from xm4_optimizer.transport.sony_control import (
    AmbientSoundState,
    AncMode,
    MessageType,
    SonyControlClient,
    SonyControlError,
    decode_ambient,
    decode_battery,
    decode_firmware_version,
    encode_ambient_set,
)


class TestPayloadCodecs:
    def test_decode_captured_replies(self) -> None:
        assert decode_firmware_version(bytes.fromhex("050205322e352e30")) == "2.5.0"
        battery = decode_battery(bytes.fromhex("11005000"))
        assert battery.level == 80 and not battery.charging
        nc = decode_ambient(bytes.fromhex("6702010202010000"))
        assert nc == AmbientSoundState(AncMode.NOISE_CANCELLING, False, 0)
        ambient = decode_ambient(bytes.fromhex("670201020001000a"))
        assert ambient == AmbientSoundState(AncMode.AMBIENT, False, 10)

    @pytest.mark.parametrize(
        "state, wire",
        [
            (AmbientSoundState(AncMode.AMBIENT, False, 10), "680211020001000a"),
            (AmbientSoundState(AncMode.NOISE_CANCELLING, False, 0), "6802110202010000"),
            (AmbientSoundState(AncMode.WIND, False, 0), "6802110201010000"),
            (AmbientSoundState(AncMode.OFF, False, 0), "6802000200010000"),
        ],
    )
    def test_encode_ambient_set(self, state: AmbientSoundState, wire: str) -> None:
        assert encode_ambient_set(state).hex() == wire

    def test_encode_rejects_bad_level(self) -> None:
        with pytest.raises(ValueError):
            encode_ambient_set(AmbientSoundState(AncMode.AMBIENT, False, 21))

    def test_decode_rejects_wrong_payload(self) -> None:
        with pytest.raises(SonyControlError):
            decode_ambient(bytes.fromhex("11005000"))


class FakeHeadset:
    """Socket double that answers MDR v1 requests like the captured WH-1000XM4."""

    def __init__(self) -> None:
        self.state = bytes.fromhex("6702010202010000")
        self.sent_payloads: list[bytes] = []
        self._outbox = b""

    def settimeout(self, _t: float) -> None:
        pass

    def close(self) -> None:
        pass

    def sendall(self, data: bytes) -> None:
        pkt, _ = MdrPacket.from_wire_bytes(data)
        if pkt is None or pkt.packet_type == MessageType.ACK:
            return
        self.sent_payloads.append(pkt.payload)
        out = [MdrPacket(MessageType.ACK, 1 - pkt.seq_num, b"")]
        code = pkt.payload[0]
        if code == 0x04:
            out.append(MdrPacket(MessageType.COMMAND_1, 1, bytes.fromhex("050205322e352e30")))
        elif code == 0x10:
            out.append(MdrPacket(MessageType.COMMAND_1, 1, bytes.fromhex("11005000")))
        elif code == 0x66:
            out.append(MdrPacket(MessageType.COMMAND_1, 1, self.state))
        elif code == 0x68:
            p = pkt.payload
            self.state = bytes([0x67, 0x02, 0x00 if p[2] == 0 else 0x01]) + p[3:]
            out.append(MdrPacket(MessageType.COMMAND_1, 1, bytes([0x69]) + self.state[1:]))
        self._outbox += b"".join(m.to_wire_bytes() for m in out)

    def recv(self, _n: int) -> bytes:
        if not self._outbox:
            raise socket.timeout()
        data, self._outbox = self._outbox, b""
        return data


class TestSonyControlClient:
    def test_get_and_set_with_readback(self) -> None:
        dev = FakeHeadset()
        client = SonyControlClient(dev, timeout_s=0.5)
        assert client.get_firmware_version() == "2.5.0"
        assert client.get_battery().level == 80
        assert client.get_ambient_sound().mode is AncMode.NOISE_CANCELLING
        result = client.set_ambient_sound(AmbientSoundState(AncMode.AMBIENT, True, 15))
        assert result == AmbientSoundState(AncMode.AMBIENT, True, 15)
        assert dev.sent_payloads[-2].hex() == "680211020001010f"

    def test_no_reply_raises(self) -> None:
        class Silent(FakeHeadset):
            def sendall(self, data: bytes) -> None:
                pass

        with pytest.raises(SonyControlError):
            SonyControlClient(Silent(), timeout_s=0.3).get_battery()


class TestSdp:
    def test_rfcomm_channel_from_attribute_list(self) -> None:
        # One record: attribute 0x0004 = ((L2CAP), (RFCOMM, channel 9))
        proto = b"\x35\x0c" + b"\x35\x03\x19\x01\x00" + b"\x35\x05\x19\x00\x03\x08\x09"
        record = b"\x35" + bytes([3 + 2 + len(proto) - 2]) + b"\x09\x00\x04" + b"\x35" + bytes([len(proto) - 2]) + proto[2:]
        lists = b"\x35" + bytes([len(record)]) + record
        decoded, end = parse_data_element(lists)
        assert end == len(lists)
        assert find_rfcomm_channels(decoded) == [9]

    def test_uuid128_decodes(self) -> None:
        u = uuid.UUID("96cc203e-5068-46ad-b32d-e316f5e069ba")
        value, _ = parse_data_element(b"\x1c" + u.bytes)
        assert value == u
