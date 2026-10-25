"""Airoha Driver Checksum and Cryptographic Digest Utilities.

Implements:
- CRC-8 Maxim/Dallas (polynomial 0x31, x^8 + x^5 + x^4 + 1) and FOTA bit-reversed CRC-8
- CRC-16-CCITT (polynomial 0x1021, x^16 + x^12 + x^5 + 1)
- SHA-256 digest generation and verification for 128KB NVDM backup images
"""

from __future__ import annotations

import hashlib
from typing import Union

from xm4_optimizer.transport.protocol_race import compute_fota_checksum


def compute_crc8_maxim(data: bytes, init: int = 0x00) -> int:
    """Computes standard 8-bit Maxim/Dallas CRC-8 (polynomial 0x31, MSB-first)."""
    crc = init & 0xFF
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0x31) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc


def compute_airoha_fota_crc8(data: bytes) -> int:
    """Computes Airoha RACE FOTA page checksum (CRC-8 poly 0x31, bit-reversed output)."""
    return compute_fota_checksum(data)


def compute_crc16_ccitt(data: bytes, init: int = 0xFFFF) -> int:
    """Computes 16-bit CRC-16-CCITT (polynomial 0x1021, initial value 0xFFFF)."""
    crc = init & 0xFFFF
    for b in data:
        crc ^= (b & 0xFF) << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def compute_sha256_hex(data: Union[bytes, bytearray]) -> str:
    """Computes lowercase hexadecimal SHA-256 digest of binary data."""
    return hashlib.sha256(bytes(data)).hexdigest()


def verify_sha256(data: Union[bytes, bytearray], expected_hex: str) -> bool:
    """Verifies binary data against an expected hexadecimal SHA-256 digest."""
    clean_expected = expected_hex.strip().split()[0].lower()
    actual = compute_sha256_hex(data)
    return actual == clean_expected


def compute_bkdr_hash(group_name: str, item_name: str) -> int:
    """Computes 32-bit polynomial rolling BKDR hash over group and item name."""
    s = (group_name + item_name).encode("ascii")
    h, a, b = 0, 63689, 378551
    for char in s:
        h = (h * a + char) & 0xFFFFFFFF
        a = (a * b) & 0xFFFFFFFF
    return h


def compute_nvdm_checksum(data: bytes) -> int:
    """Computes 16-bit interleaved accumulator NVDM checksum."""
    even_sum = sum(data[0::2]) & 0xFF
    odd_sum = sum(data[1::2]) & 0xFF
    return (odd_sum << 8) | even_sum


def verify_nvdm_checksum(data: bytes, expected_checksum: int) -> bool:
    """Verifies 16-bit interleaved NVDM checksum."""
    return compute_nvdm_checksum(data) == (expected_checksum & 0xFFFF)


def compute_race_fota_crc8(data: bytes) -> int:
    """Alias for compute_airoha_fota_crc8."""
    return compute_airoha_fota_crc8(data)


def verify_race_fota_crc8(data: bytes, expected_crc8: int) -> bool:
    """Verifies Airoha RACE FOTA CRC-8 page checksum."""
    return compute_airoha_fota_crc8(data) == (expected_crc8 & 0xFF)
